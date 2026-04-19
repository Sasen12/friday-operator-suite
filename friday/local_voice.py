from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import time
import warnings
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import httpx
import numpy as np
import soundcard as sc
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

from friday.speech import (
    LocalAudioFrame,
    build_local_speech_config,
    synthesize_text_frames,
    transcribe_audio_frames,
)


logger = logging.getLogger("friday-local")

MCP_SERVER_URL = os.getenv("FRIDAY_MCP_URL", "http://127.0.0.1:8000/sse")
OLLAMA_CHAT_URL = os.getenv("FRIDAY_OLLAMA_CHAT_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_LLM_MODEL", "gemma4")
TTS_SPEED = float(os.getenv("FRIDAY_TTS_SPEED", "1.15") or 1.15)
WAKE_WORD_MODE = os.getenv("FRIDAY_WAKE_WORD_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}


_WAKE_PREFIX_RE = re.compile(
    r"^\s*(?:(?:hey|okay|ok|hi|yo)\s+)?friday\b[\s,.:;!?-]*",
    flags=re.IGNORECASE,
)

_SLEEP_PATTERNS = [
    re.compile(
        r"^\s*(?:(?:hey|okay|ok|hi|yo)\s+)?friday\b[\s,.:;!?-]*"
        r"(?:go\s+to\s+sleep|sleep|stand\s+by|stop\s+listening|disarm|good\s*night)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:go\s+to\s+sleep|sleep|stand\s+by|stop\s+listening|disarm|good\s*night)\b"
        r"[\s,.:;!?-]*(?:friday\b)?",
        flags=re.IGNORECASE,
    ),
]


def _strip_wake_phrase(text: str) -> tuple[bool, str]:
    cleaned = _WAKE_PREFIX_RE.sub("", text, count=1).strip()
    if cleaned == text.strip():
        return False, text.strip()
    return True, cleaned


def _is_sleep_phrase(text: str) -> bool:
    normalized = text.strip()
    return any(pattern.search(normalized) for pattern in _SLEEP_PATTERNS)


def _normalize_message_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _tool_signature(tool: Any) -> str:
    schema = tool.inputSchema or {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
    parts: list[str] = []
    for key in properties.keys():
        parts.append(f"{key}*" if key in required else key)
    return ", ".join(parts)


def _tool_catalog_text(tools: list[Any]) -> str:
    lines: list[str] = []
    for tool in sorted(tools, key=lambda item: item.name):
        description = _normalize_message_text(tool.description or "")
        signature = _tool_signature(tool)
        if signature:
            lines.append(f"- {tool.name}({signature}): {description}")
        else:
            lines.append(f"- {tool.name}: {description}")
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        parsed = json.loads(candidate[start : end + 1])
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Model did not return a valid JSON object.")


def _content_to_text(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text).strip())
            continue
        structured = getattr(item, "structuredContent", None)
        if structured is not None:
            parts.append(json.dumps(structured, ensure_ascii=False))
            continue
        try:
            parts.append(json.dumps(item.model_dump(), ensure_ascii=False))
        except Exception:
            parts.append(str(item))
    if not parts and getattr(result, "structuredContent", None) is not None:
        parts.append(json.dumps(result.structuredContent, ensure_ascii=False))
    return "\n".join(part for part in parts if part).strip()


def _pcm16_to_numpy(audio_bytes: bytes, num_channels: int) -> np.ndarray:
    audio = np.frombuffer(audio_bytes, dtype=np.int16)
    if num_channels > 1:
        audio = audio.reshape(-1, num_channels)
    return audio


def _play_frames(frames: list[LocalAudioFrame]) -> None:
    speaker = sc.default_speaker()
    for frame in frames:
        audio = _pcm16_to_numpy(bytes(frame.data), int(frame.num_channels)).astype(np.float32)
        audio = np.clip(audio / 32768.0, -1.0, 1.0)
        if audio.ndim == 1:
            audio = audio.reshape(-1, 1)
        speaker.play(audio, samplerate=int(frame.sample_rate))


def _default_input_samplerate() -> int:
    raw = os.getenv("FRIDAY_AUDIO_SAMPLE_RATE", "48000").strip()
    try:
        rate = int(raw)
        return rate if rate > 0 else 48000
    except Exception:
        return 48000


def _record_utterance(
    *,
    sample_rate: int,
    max_seconds: float = 20.0,
    start_threshold: float = 0.018,
    stop_threshold: float = 0.012,
    silence_seconds: float = 1.1,
    block_size: int = 1024,
) -> tuple[bytes, int] | None:
    max_blocks = max(1, int(max_seconds * sample_rate / block_size))
    threshold_factor = 32768.0

    microphone = sc.default_microphone()
    capture_channels = 1
    try:
        capture_channels = max(1, min(2, int(getattr(microphone, "channels", 2) or 2)))
    except Exception:
        capture_channels = 1

    recorder_blocksize = max(block_size * 4, 4096)
    record_chunk_frames = max(512, block_size)
    recorder_attempts: list[dict[str, Any]] = [
        {
            "samplerate": sample_rate,
            "channels": capture_channels,
            "blocksize": recorder_blocksize,
        },
        {
            "samplerate": sample_rate,
            "channels": capture_channels,
        },
        {
            "samplerate": sample_rate,
            "channels": 1,
        },
        {
            "samplerate": sample_rate,
        },
    ]

    def _capture_with_recorder(recorder_kwargs: dict[str, Any]) -> list[np.ndarray]:
        pre_roll = deque(maxlen=max(4, int(0.35 * sample_rate / block_size)))
        blocks: list[np.ndarray] = []
        started = False
        silence_run = 0

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=getattr(sc, "SoundcardRuntimeWarning", RuntimeWarning),
                message=".*data discontinuity in recording.*",
            )
            with microphone.recorder(**recorder_kwargs) as recorder:
                for _ in range(max_blocks):
                    data = recorder.record(numframes=record_chunk_frames)
                    block = np.asarray(data, dtype=np.float32)
                    if block.size == 0:
                        continue

                    if block.ndim == 1:
                        block = block.reshape(-1, 1)

                    mono = block.mean(axis=1)
                    rms = float(np.sqrt(np.mean(mono * mono)))

                    if not started:
                        pre_roll.append(block.copy())
                        if rms >= start_threshold:
                            started = True
                            blocks.extend(pre_roll)
                            pre_roll.clear()
                            silence_run = 0
                        continue

                    blocks.append(block.copy())
                    if rms < stop_threshold:
                        silence_run += 1
                        if (silence_run * record_chunk_frames / sample_rate) >= silence_seconds:
                            break
                    else:
                        silence_run = 0

        return blocks

    blocks: list[np.ndarray] = []
    last_error: Exception | None = None
    for recorder_kwargs in recorder_attempts:
        try:
            blocks = _capture_with_recorder(recorder_kwargs)
        except RuntimeError as exc:
            if "invalid argument" not in str(exc).lower():
                raise
            last_error = exc
            continue
        if blocks:
            break
    else:
        if last_error is not None:
            raise last_error

    if not blocks:
        return None

    audio = np.concatenate(blocks)
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * threshold_factor).astype(np.int16)
    return pcm.tobytes(), sample_rate


@dataclass
class PlannedActions:
    say: str
    actions: list[dict[str, Any]]


class LocalFridayRuntime:
    def __init__(self) -> None:
        self.local_speech = build_local_speech_config(tts_speed=TTS_SPEED)
        self.conversation: list[dict[str, str]] = []
        self._wake_active_until = 0.0
        self._wake_window_seconds = float(os.getenv("FRIDAY_WAKE_WORD_WINDOW_SECONDS", "30") or 30)
        self._tool_catalog = ""
        self._tool_names: set[str] = set()
        self._sample_rate = _default_input_samplerate()

    @asynccontextmanager
    async def connect_mcp(self):
        async with sse_client(MCP_SERVER_URL, timeout=15, sse_read_timeout=300) as transport:
            async with ClientSession(*transport) as session:
                await session.initialize()
                yield session

    async def _load_tools(self, session: ClientSession) -> None:
        result = await session.list_tools()
        self._tool_catalog = _tool_catalog_text(result.tools)
        self._tool_names = {tool.name for tool in result.tools}

    async def _ollama_chat(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.2,
            },
        }
        if json_mode:
            payload["format"] = "json"

        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(OLLAMA_CHAT_URL, json=payload)
            response.raise_for_status()
            data = response.json()

        message = data.get("message") or {}
        return _normalize_message_text(message.get("content", ""))

    def _build_plan_messages(self, user_text: str) -> list[dict[str, str]]:
        system_prompt = (
            "You are F.R.I.D.A.Y., a fully on-device Windows operator. "
            "Use the local MCP tools to act on the user's laptop. "
            "Be concise, calm, and a little witty. "
            "When the user wants the computer to do something, choose the smallest useful set of tool actions. "
            "Prefer batch tools for multi-step work. "
            "Ask for confirmation before destructive actions, overwriting files, force-closing programs, or sending email. "
            "Return only valid JSON with keys 'say' and 'actions'. "
            "'say' should be a brief acknowledgement or direct answer. "
            "'actions' should be a list of objects with 'tool' and 'arguments'. "
            "Only use tools from the catalog. "
            "If no tool is needed, leave 'actions' empty. "
            "Never include markdown, code fences, or extra commentary."
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": f"Tool catalog:\n{self._tool_catalog}"},
            *self.conversation[-8:],
            {"role": "user", "content": user_text},
        ]

    def _build_final_messages(
        self,
        user_text: str,
        tool_results: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        system_prompt = (
            "You are F.R.I.D.A.Y. speaking to the user after local tools have run. "
            "Respond in 1-3 short sentences, natural and calm. "
            "Do not mention JSON, tools, or implementation details. "
            "If the task succeeded, mention the important outcome or saved path. "
            "If the task failed, explain the problem plainly and suggest the smallest next step."
        )
        result_blob = json.dumps(tool_results, ensure_ascii=False, indent=2)
        return [
            {"role": "system", "content": system_prompt},
            *self.conversation[-8:],
            {"role": "user", "content": f"User request:\n{user_text}\n\nTool results:\n{result_blob}"},
        ]

    async def plan(self, user_text: str) -> PlannedActions:
        messages = self._build_plan_messages(user_text)
        content = await self._ollama_chat(messages, json_mode=True)
        try:
            payload = _extract_json_object(content)
        except Exception:
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "Your previous answer was not valid JSON. "
                        "Return only valid JSON with keys 'say' and 'actions'. "
                        f"Original request: {user_text}"
                    ),
                },
            ]
            content = await self._ollama_chat(retry_messages, json_mode=True)
            payload = _extract_json_object(content)

        say = _normalize_message_text(payload.get("say", "")) or "On it, boss."
        actions_raw = payload.get("actions", [])
        actions: list[dict[str, Any]] = []
        if isinstance(actions_raw, list):
            for action in actions_raw:
                if not isinstance(action, dict):
                    continue
                tool = _normalize_message_text(action.get("tool", ""))
                if not tool or tool not in self._tool_names:
                    continue
                arguments = action.get("arguments", {})
                if isinstance(arguments, str):
                    with contextlib.suppress(Exception):
                        arguments = json.loads(arguments)
                if not isinstance(arguments, dict):
                    arguments = {}
                actions.append({"tool": tool, "arguments": arguments})

        return PlannedActions(say=say, actions=actions)

    async def execute_actions(
        self,
        session: ClientSession,
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for action in actions:
            tool_name = action["tool"]
            arguments = action.get("arguments", {})
            print(f"TOOL {tool_name} {json.dumps(arguments, ensure_ascii=False)}", flush=True)
            try:
                result = await session.call_tool(tool_name, arguments)
                result_text = _content_to_text(result)
                result_entry = {
                    "tool": tool_name,
                    "ok": not bool(result.isError),
                    "text": result_text,
                }
                if getattr(result, "structuredContent", None) is not None:
                    result_entry["structured"] = result.structuredContent
                results.append(result_entry)
            except Exception as exc:
                results.append(
                    {
                        "tool": tool_name,
                        "ok": False,
                        "text": str(exc),
                    }
                )
        return results

    async def speak(self, text: str) -> None:
        utterance = _normalize_message_text(text)
        if not utterance:
            return
        print(f"FRIDAY: {utterance}", flush=True)
        frames = await synthesize_text_frames(utterance, self.local_speech)
        _play_frames(frames)

    async def _handle_user_text(self, session: ClientSession, text: str) -> None:
        utterance = _normalize_message_text(text)
        if not utterance:
            return

        if _is_sleep_phrase(utterance):
            self._wake_active_until = 0.0
            await self.speak("Standing by.")
            return

        now = time.monotonic()
        if WAKE_WORD_MODE:
            has_wake_word, stripped = _strip_wake_phrase(utterance)
            if has_wake_word:
                self._wake_active_until = now + self._wake_window_seconds
                utterance = stripped
            elif now <= self._wake_active_until:
                self._wake_active_until = now + self._wake_window_seconds
            else:
                return

        if not utterance:
            return

        self.conversation.append({"role": "user", "content": utterance})
        if len(self.conversation) > 20:
            self.conversation = self.conversation[-20:]

        plan = await self.plan(utterance)
        if plan.actions:
            await self.speak(plan.say or "On it, boss.")
            tool_results = await self.execute_actions(session, plan.actions)
            final_text = await self._ollama_chat(self._build_final_messages(utterance, tool_results))
            await self.speak(final_text or "Done.")
            self.conversation.append({"role": "assistant", "content": final_text or "Done."})
            return

        await self.speak(plan.say or "Done.")
        self.conversation.append({"role": "assistant", "content": plan.say or "Done."})

    async def console_loop(self, session: ClientSession) -> None:
        print("FRIDAY local console is ready. Type a command and press Enter.", flush=True)
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if line == "":
                break
            await self._handle_user_text(session, line)

    async def voice_loop(self, session: ClientSession) -> None:
        print("FRIDAY local voice is ready. Speak naturally into the microphone.", flush=True)
        print(f"Using Whisper model: {self.local_speech.stt_model}", flush=True)
        print(f"Using Piper voice: {self.local_speech.tts_model}", flush=True)

        while True:
            try:
                print("LISTENING", flush=True)
                recording = await asyncio.to_thread(
                    _record_utterance,
                    sample_rate=self._sample_rate,
                )
                if not recording:
                    continue

                audio_bytes, sample_rate = recording
                samples_per_channel = len(audio_bytes) // 2
                frame = LocalAudioFrame(
                    data=audio_bytes,
                    sample_rate=sample_rate,
                    num_channels=1,
                    samples_per_channel=samples_per_channel,
                )
                transcript, duration, language = await transcribe_audio_frames([frame], self.local_speech)
                if not transcript:
                    continue

                print(f"YOU: {transcript}", flush=True)
                print(f"TRANSCRIPT {duration:.2f}s {language}", flush=True)
                await self._handle_user_text(session, transcript)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logger.exception("Local voice loop error: %s", exc)
                print(f"ERROR: {exc}", flush=True)
                await asyncio.sleep(1.0)


@asynccontextmanager
async def _local_runtime_session() -> Any:
    runtime = LocalFridayRuntime()
    async with runtime.connect_mcp() as session:
        await runtime._load_tools(session)
        yield runtime, session


async def _run(mode: str) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    logger.info(
        "FRIDAY local mode online | STT=%s | LLM=%s | TTS=%s | wake_word=%s",
        os.getenv("FRIDAY_LOCAL_STT_MODEL", "base.en"),
        OLLAMA_MODEL,
        os.getenv("FRIDAY_LOCAL_TTS_MODEL", "en_US-lessac-medium"),
        WAKE_WORD_MODE,
    )

    async with _local_runtime_session() as (runtime, session):
        await runtime.speak("You're awake late at night, boss? What are you up to?")
        if mode == "console":
            await runtime.console_loop(session)
        else:
            await runtime.voice_loop(session)


def main() -> None:
    args = [arg.strip().lower() for arg in sys.argv[1:] if arg.strip()]
    mode = "voice"
    if args:
        if args[0] == "console":
            mode = "console"
        elif args[0] in {"dev", "voice"}:
            mode = "voice"
    try:
        anyio.run(_run, mode)
    except KeyboardInterrupt:
        pass
