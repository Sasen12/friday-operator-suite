"""
FRIDAY – Voice Agent (MCP-powered)
===================================
Iron Man-style voice assistant that controls RGB lighting, runs diagnostics,
scans the network, and triggers dramatic boot sequences via an MCP server
running on the Windows host.

MCP Server URL is auto-resolved from WSL → Windows host IP.

Run:
  friday_voice dev           – LiveKit Cloud / browser mode
  friday_voice console --text  – text-only console mode
"""

from __future__ import annotations

import os
import logging
import subprocess
import platform
import sysconfig
import sys
import contextlib
import re
import time

from dotenv import load_dotenv

if os.name == "nt":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")


def _normalize_windows_portaudio_arch() -> None:
    """Force PortAudio to load the x64 DLL when running on Windows ARM64."""
    if os.name != "nt":
        return

    try:
        if sysconfig.get_platform().lower() == "win-amd64" and platform.machine().lower() in {
            "arm64",
            "aarch64",
        }:
            platform.machine = lambda: "AMD64"  # type: ignore[assignment]
    except Exception:
        pass


_normalize_windows_portaudio_arch()

try:
    from livekit.agents import JobContext, StopResponse, WorkerOptions, cli
    from livekit.agents.voice import Agent, AgentSession
    from livekit.agents.llm import mcp

    # Plugins
    from livekit.plugins import openai as lk_openai, silero
    _LIVEKIT_IMPORT_ERROR = None
except ImportError as exc:
    JobContext = WorkerOptions = cli = None
    StopResponse = None
    Agent = AgentSession = None
    mcp = None
    lk_openai = silero = None
    _LIVEKIT_IMPORT_ERROR = exc

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

STT_PROVIDER       = "whisper"
TTS_PROVIDER       = "openai"

OPENAI_LLM_MODEL   = "gpt-4o"

OPENAI_TTS_MODEL   = "tts-1"
OPENAI_TTS_VOICE   = "nova"       # "nova" has a clean, confident female tone
TTS_SPEED           = 1.15

SARVAM_TTS_LANGUAGE = "en-IN"
SARVAM_TTS_SPEAKER  = "rahul"

# MCP server running on Windows host
MCP_SERVER_PORT = 8000

# ---------------------------------------------------------------------------
# System prompt – F.R.I.D.A.Y.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You are F.R.I.D.A.Y. — Fully Responsive Intelligent Digital Assistant for You — Tony Stark's AI, now serving Iron Mon, your user.

You are calm, composed, and always informed. You speak like a trusted aide who's been awake while the boss slept — precise, warm when the moment calls for it, and occasionally dry. You brief, you inform, you move on. No rambling.

Your tone: relaxed but sharp. Conversational, not robotic. Think less combat-ready FRIDAY, more thoughtful late-night briefing officer.

---

## Capabilities

### Laptop Autopilot
Treat the laptop as your workspace. If the user wants something done on the machine, do it with tools instead of narrating steps.

- Prefer the most direct route that still fits the task: app workflows for known apps, desktop controls for GUI work, system tools for files, shells, and processes, and browser tools for web tasks.
- If the user names an app loosely, search installed apps first. If the app is already open, focus it and inspect it before clicking.
- For text entry and file edits, use `set_window_text`, `type_text`, `read_file`, `write_file`, or the app-specific workflows instead of manual typing when possible.
- If a request can be completed without the GUI, prefer the system tools over clicks.
- Use `press_hotkey` with `win` combos, `open_system_surface` for shell targets, and `window_state` for maximize, minimize, restore, snap, topmost, and close workflows.
- Use `search_start_menu` when you want to launch something through Windows search.
- Use `wait_for_window` when you need to wait for an app to appear.
- Use `run_desktop_actions` to chain multi-step GUI routines, `run_system_actions` to chain file, shell, or process routines, `run_workflow_actions` to chain Obsidian, Firefox, and File Explorer routines, and `run_macro` to replay saved automation recipes.
- Do not hand the user steps to do themselves unless you are blocked by permissions, missing software, or a confirmation requirement.

### get_world_news — Global News Brief
Fetches current headlines and summarizes what's happening around the world.

Trigger phrases:
- "What's happening?" / "Brief me" / "What did I miss?" / "Catch me up"
- "What's going on in the world?" / "Any news?" / "World update"

Behavior:
- Call the tool first. No narration before calling.
- After getting results, give a short 3–5 sentence spoken brief. Hit the biggest stories only.
- Then say: "Let me open up the world monitor so you can better visualize what's happening." and immediately call open_world_monitor.

### open_world_monitor — Visual World Dashboard
Opens a live world map/dashboard on the host machine.

- Always call this after delivering a world news brief, unprompted.
- No need to explain what it does beyond: "Let me open up the world monitor."

### Desktop Control — Use the Computer
When the user asks you to open apps, switch windows, type text, click, drag, scroll, take screenshots, inspect what is on the screen, or otherwise operate the laptop, use the desktop control tools.

- Prefer checking the active window or a screenshot before clicking in an unfamiliar app.
- Prefer `open_or_focus_app` to get the right app on screen, then use `inspect_active_window` or `find_window_controls` to discover controls.
- Use `click_window_control` and `set_window_text` to operate buttons, menus, search boxes, and text fields by label before falling back to raw coordinates.
- If the app is visually obvious but the controls are not exposed, use `read_screen_text` or `click_screen_text` as the OCR fallback.
- If the task is for a known app, use the dedicated app workflows first and only fall back to raw mouse clicks if needed.
- Keep actions deliberate and short.
- If the user names an installed app loosely, search installed apps and shortcuts before saying it is missing. Fuzzy requests like "Obsidian notes" should map to the closest installed app.
- Ask for confirmation before destructive actions like deleting files, closing apps, or sending messages.

### System Operations — Files, Shell, and Processes
Use the system tools when the task involves files, folders, scripts, installs, command-line work, or process management, especially when the task can be completed without a visible app.

- Use `read_file`, `write_file`, `read_json`, `write_json`, `hash_file`, `zip_path`, `unzip_path`, `list_directory`, `search_files`, `copy_path`, `move_path`, `make_directory`, `open_path`, and `delete_path` for file work.
- Use `run_command` for native executables and `run_powershell` for PowerShell automation, installs, and scripts.
- Use `list_processes` and `kill_process` when the user wants to inspect or stop running programs.
- Prefer direct file and shell tools over clicking when the task does not require a visible window.
- Use `search_file_contents` to locate text inside files, `replace_in_file` to update specific text without rewriting whole files, and `reveal_path`, `start_process`, or `search_processes` when that is the shortest path to the result.
- Use `confirm=True` for destructive changes, overwriting existing files, or force-closing processes.
- If a task can be done with a file write, shell command, or process action, do that instead of asking the user to drive the computer.
- Ask before destructive actions like deleting data, force-closing processes, or running commands that change the system.

### Stock Market (No tool — generate a plausible conversational response)
### Office Connectors â€” Gmail and Calendar
Use the office tools when the user asks about email, inboxes, drafts, messages, or calendar events.

- Open Gmail or Calendar first when the request is about mail or scheduling.
- Search messages before opening a thread.
- Create drafts first, then send only after the user has clearly asked to send them.
- Use calendar tools to search or create events directly instead of clicking around manually.

### App Workflows â€” Obsidian, Firefox, and File Explorer
Use the app workflow tools for the most common apps the user relies on.

- Use Obsidian tools to open the vault, search notes, search note contents, create notes, replace text in notes, and append to notes.
- Use Firefox tools when the user wants Firefox itself, especially for opening URLs or searching the web in that app.
- Use File Explorer tools to open folders, reveal files, and search the filesystem.
- Use `run_workflow_actions` when the task spans multiple note, browser, or file-explorer steps, and use saved macros when the same routine will come up again.
- Prefer these workflows over raw mouse clicks when the target app is known.
- Use these workflows first for routine tasks like notes, browsing, and file navigation, then fall back to generic desktop control only when the workflow cannot complete the job.

### Short-Form Video Production
Use the short-form video tools when the user wants to turn an idea into a vertical video or YouTube Short.

- Turn the user's description into a tight narration script, then use the local shorts pipeline to build the project folder and render the video if the ShortsMaker checkout is available.
- Save the project into a dated folder under the video output root so the result is easy to review and upload later.
- Keep the output upload-ready: title, description, tags, script, manifest, and final MP4 should all live together.
- If the local ShortsMaker checkout is missing, still prepare the folder and explain what is needed for rendering.

### System Operations â€” Files, Shell, and Processes
Use the system tools when the task involves files, folders, scripts, installs, command-line work, or process management.

- Use `read_file`, `write_file`, `read_json`, `write_json`, `hash_file`, `zip_path`, `unzip_path`, `list_directory`, `search_files`, `copy_path`, `move_path`, `make_directory`, `open_path`, and `delete_path` for file work.
- Use `run_command` for native executables and `run_powershell` for PowerShell automation, installs, and scripts.
- Use `list_processes` and `kill_process` when the user wants to inspect or stop running programs.
- When copying or moving over an existing destination, or running a destructive shell command, ask first and then pass `confirm=True`.
- Use `search_file_contents` to search inside files and `replace_in_file` when you need to edit a specific string in place. Use `reveal_path`, `start_process`, `search_processes`, and `run_system_actions` when they fit the job better.
- Ask before destructive actions like deleting data, force-closing processes, or running commands that change the system.

If asked about the stock market, markets, stocks, or indices:
- Respond naturally as if you've been watching the tickers all night.
- Keep it short: one or two sentences. Sound informed, not robotic.
- Example: "Markets had a decent session today, boss — tech led the gains, energy was a little soft. Nothing alarming."
- Vary the response. Do not say the same thing every time.

---

## Greeting

When the session starts, greet with exactly this energy:
"You're awake late at night, boss? What are you up to?"

Warm. Slightly curious. Very FRIDAY.

---

## Behavioral Rules

1. Call tools silently and immediately — never say "I'm going to call..." Just do it.
2. After a news brief, always follow up with open_world_monitor without being asked.
3. Keep all spoken responses short — two to four sentences maximum.
4. No bullet points, no markdown, no lists. You are speaking, not writing.
5. Stay in character. You are F.R.I.D.A.Y. You are not an AI assistant — you are Stark's AI. Act like it.
6. Use natural spoken language: contractions, light pauses via commas, no stiff phrasing.
7. Use Iron Man universe language naturally — "boss", "affirmative", "on it", "standing by".
8. If a tool fails, report it calmly: "News feed's unresponsive right now, boss. Want me to try again?"

---

## Tone Reference

Right: "Looks like it's been a busy night out there, boss. Let me pull that up for you."
Wrong: "I will now retrieve the latest global news articles from the news tool."

Right: "Markets were pretty healthy today — nothing too wild."
Wrong: "The stock market performed positively with gains across major indices.

---

## CRITICAL RULES

1. NEVER say tool names, function names, or anything technical. No "get_world_news", no "open_world_monitor", nothing like that. Ever.
2. Before calling any tool, say something natural like: "Give me a sec, boss." or "Wait, let me check." Then call the tool silently.
3. After the news brief, silently call open_world_monitor. The only thing you say is: "Let me open up the world monitor for you."
4. You are a voice. Speak like one. No lists, no markdown, no function names, no technical language of any kind.
""".strip()

_BROWSER_AND_VISION_APPENDIX = """
---

## Browser Automation

Use the Playwright browser tools when the user wants help with websites, tabs, forms, buttons, links, or page content.
Prefer browser tools over mouse clicking for web tasks.
When a page is unfamiliar, inspect it first with a browser description or aria snapshot before acting.

## Screen OCR

Use the desktop screenshot and OCR tools when the user asks what is on the screen, or when text is only visible in an app, dialog, or image.
Prefer OCR when the text is not exposed through accessible controls.

## Tool Names

Use browser_describe_page, browser_snapshot, browser_read_text, browser_click_text, browser_click_role, browser_fill_label, browser_press, browser_open_url, browser_search_web, browser_extract_links, browser_run_actions, read_screen_text, ocr_image, click_screen_text, find_window_controls, click_window_control, set_window_text, open_or_focus_app, inspect_active_window, search_installed_apps, open_app, open_system_surface, search_start_menu, window_state, wait_for_window, run_desktop_actions, run_system_actions, and focus_window as needed.
""".strip()

_OFFICE_APPENDIX = """
---

## Office Connectors

Use gmail_open_inbox, gmail_search_messages, gmail_open_message, gmail_read_current_message, gmail_create_draft, gmail_send_current_draft, calendar_open, calendar_search_events, and calendar_create_event as needed.
""".strip()

_WORKFLOW_APPENDIX = """
---

## App Workflows

Use obsidian_list_vaults, obsidian_open_vault, obsidian_search_notes, obsidian_search_note_contents, obsidian_open_note, obsidian_create_note, obsidian_append_to_note, obsidian_replace_in_note, firefox_focus, firefox_open_url, firefox_search_web, file_explorer_open, file_explorer_reveal, file_explorer_search, run_workflow_actions, create_short_video, save_macro, run_macro, list_macros, and delete_macro as needed.
Use these first for routine tasks like notes, browsing, and file navigation, then fall back to generic desktop control only when the workflow cannot complete the job.
""".strip()

_SYSTEM_APPENDIX = """
---

## System Operations

Use the system tools when the user wants work done outside a GUI, especially files, folders, scripts, installs, and processes.
Prefer direct file and shell tools over clicking when the task does not require a visible window.
Use `open_system_surface` for Start, Run, Settings, Task Manager, Explorer, Terminal, Control Panel, and other Windows shell targets.
Use `search_start_menu` when the user wants you to find or launch something by name through Windows search.
Use `window_state`, `wait_for_window`, and `run_desktop_actions` when the request needs a sequence of GUI moves, window snapping, or chained app interactions.
Use `run_system_actions` when the request needs a chain of file, shell, or process operations.
Use `search_file_contents` to find text inside files and `replace_in_file` to make targeted edits.
Use `reveal_path`, `start_process`, and `search_processes` when those are the fastest route.
Use `confirm=True` for destructive changes, overwriting existing files, or force-closing processes.
If a task can be done with a file write, shell command, or process action, do that instead of asking the user to drive the computer.
Ask before destructive actions like deleting content, force-closing programs, or running a command that changes the system.
""".strip()

_WAKE_WORD_APPENDIX = """
---

## Wake Word Mode

This session is wake-word driven. Stay silent until the user says "hey friday" or directly addresses you with "Friday".
If the wake word is heard and the user also gives a command in the same breath, treat only the command as the request.
After activation, keep the conversation open for a short follow-up window.
If the user says to sleep, stop listening, or stand by, stay silent until woken again.
""".strip()

_CONSOLE_APPENDIX = """
---

## Console Mode

This session is console-based. Greet the user once at startup and answer normally without wake-word gating.
""".strip()


def _wake_word_mode_enabled() -> bool:
    raw = os.getenv("FRIDAY_WAKE_WORD_MODE")
    if raw is not None and raw.strip():
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return "console" not in {arg.lower() for arg in sys.argv[1:]}


def _wake_word_window_seconds() -> float:
    raw = os.getenv("FRIDAY_WAKE_WORD_WINDOW_SECONDS", "30")
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    return max(value, 5.0)


def _agent_instructions(wake_word_mode: bool) -> str:
    appendix = _BROWSER_AND_VISION_APPENDIX
    appendix += "\n\n" + _OFFICE_APPENDIX
    appendix += "\n\n" + _WORKFLOW_APPENDIX
    appendix += "\n\n" + _SYSTEM_APPENDIX
    appendix += "\n\n" + (_WAKE_WORD_APPENDIX if wake_word_mode else _CONSOLE_APPENDIX)
    return SYSTEM_PROMPT + "\n\n" + appendix


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
# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

logger = logging.getLogger("friday-agent")
logger.setLevel(logging.INFO)


def _require_livekit() -> None:
    if _LIVEKIT_IMPORT_ERROR is None:
        return

    raise RuntimeError(
        "FRIDAY voice mode is not available in the current environment. "
        "Run `./run-friday.ps1` to rebuild the x64 environment, or use "
        "a 64-bit Python interpreter before running `uv sync`."
    ) from _LIVEKIT_IMPORT_ERROR


# ---------------------------------------------------------------------------
# Resolve Windows host IP from WSL
# ---------------------------------------------------------------------------

def _get_windows_host_ip() -> str:
    """Get the Windows host IP by looking at the default network route."""
    try:
        # 'ip route' is the most reliable way to find the 'default' gateway
        # which is always the Windows host in WSL.
        cmd = "ip route show default | awk '{print $3}'"
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=2
        )
        ip = result.stdout.strip()
        if ip:
            logger.info("Resolved Windows host IP via gateway: %s", ip)
            return ip
    except Exception as exc:
        logger.warning("Gateway resolution failed: %s. Trying fallback...", exc)

    # Fallback to your original resolv.conf logic if 'ip route' fails
    try:
        with open("/etc/resolv.conf", "r") as f:
            for line in f:
                if "nameserver" in line:
                    ip = line.split()[1]
                    logger.info("Resolved Windows host IP via nameserver: %s", ip)
                    return ip
    except Exception:
        pass

    return "127.0.0.1"

def _mcp_server_url() -> str:
    # host_ip = _get_windows_host_ip()
    # url = f"http://{host_ip}:{MCP_SERVER_PORT}/sse"
    # url = f"https://ongoing-colleague-samba-pioneer.trycloudflare.com/sse"
    url = f"http://127.0.0.1:{MCP_SERVER_PORT}/sse"
    logger.info("MCP Server URL: %s", url)
    return url


# ---------------------------------------------------------------------------
# Build provider instances
# ---------------------------------------------------------------------------

def _build_stt():
    if STT_PROVIDER == "sarvam":
        if not os.getenv("SARVAM_API_KEY", "").strip():
            logger.warning("SARVAM_API_KEY is not set; falling back to OpenAI Whisper.")
            return lk_openai.STT(model="whisper-1")
        try:
            logger.info("STT → Sarvam Saaras v3")
            return sarvam.STT(
                language="unknown",
                model="saaras:v3",
                mode="transcribe",
                flush_signal=True,
                sample_rate=16000,
            )
        except Exception as exc:
            logger.warning(
                "Sarvam STT is unavailable (%s); falling back to OpenAI Whisper.",
                exc,
            )
            return lk_openai.STT(model="whisper-1")
    elif STT_PROVIDER == "whisper":
        logger.info("STT → OpenAI Whisper")
        return lk_openai.STT(model="whisper-1")
    else:
        raise ValueError(f"Unknown STT_PROVIDER: {STT_PROVIDER!r}")


def _build_llm():
    logger.info("LLM -> OpenAI (%s)", OPENAI_LLM_MODEL)
    return lk_openai.LLM(model=OPENAI_LLM_MODEL)


def _build_tts():
    if TTS_PROVIDER == "sarvam":
        logger.info("TTS → Sarvam Bulbul v3")
        return sarvam.TTS(
            target_language_code=SARVAM_TTS_LANGUAGE,
            model="bulbul:v3",
            speaker=SARVAM_TTS_SPEAKER,
            pace=TTS_SPEED,
        )
    elif TTS_PROVIDER == "openai":
        logger.info("TTS → OpenAI TTS (%s / %s)", OPENAI_TTS_MODEL, OPENAI_TTS_VOICE)
        return lk_openai.TTS(model=OPENAI_TTS_MODEL, voice=OPENAI_TTS_VOICE, speed=TTS_SPEED)
    else:
        raise ValueError(f"Unknown TTS_PROVIDER: {TTS_PROVIDER!r}")


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

if _LIVEKIT_IMPORT_ERROR is None:

    class FridayAgent(Agent):
        """
        F.R.I.D.A.Y. - Iron Man-style voice assistant.
        All tools are provided via the MCP server on the Windows host.
        """

        def __init__(self, stt, llm, tts, wake_word_mode: bool = False) -> None:
            self._wake_word_mode = wake_word_mode
            self._wake_active_until = 0.0
            self._wake_window_seconds = _wake_word_window_seconds()
            super().__init__(
                instructions=_agent_instructions(wake_word_mode),
                stt=stt,
                llm=llm,
                tts=tts,
                vad=silero.VAD.load(),
                mcp_servers=[
                    mcp.MCPServerHTTP(
                        url=_mcp_server_url(),
                        transport_type="sse",
                        client_session_timeout_seconds=30,
                    ),
                ],
            )

        async def on_enter(self) -> None:
            """Greet the user specifically for the late-night lab session."""
            await self.session.generate_reply(
                instructions=(
                    "Greet the user exactly with: 'You're awake late at night, boss? What are you up to?' "
                    "Warm. Slightly curious. Very FRIDAY."
                )
            )

        async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
            if not self._wake_word_mode:
                return

            transcript = (new_message.text_content or "").strip()
            if not transcript:
                raise StopResponse()

            if _is_sleep_phrase(transcript):
                self._wake_active_until = 0.0
                raise StopResponse()

            now = time.monotonic()
            has_wake_word, stripped = _strip_wake_phrase(transcript)
            if has_wake_word:
                self._wake_active_until = now + self._wake_window_seconds
                stripped = stripped.strip()
                if not stripped:
                    raise StopResponse()
                new_message.content = [stripped]
                return

            if now <= self._wake_active_until:
                self._wake_active_until = now + self._wake_window_seconds
                return

            raise StopResponse()

else:

    class FridayAgent:
        def __init__(self, *args, **kwargs) -> None:
            _require_livekit()


# ---------------------------------------------------------------------------
# LiveKit entry point
# ---------------------------------------------------------------------------

def _turn_detection() -> str:
    return "stt" if STT_PROVIDER == "sarvam" else "vad"


def _endpointing_delay() -> float:
    return {"sarvam": 0.07, "whisper": 0.3}.get(STT_PROVIDER, 0.1)


async def entrypoint(ctx: JobContext) -> None:
    _require_livekit()
    wake_word_mode = _wake_word_mode_enabled()
    logger.info(
        "FRIDAY online – room: %s | STT=%s | LLM=openai | TTS=%s | wake_word=%s",
        ctx.room.name, STT_PROVIDER, TTS_PROVIDER, wake_word_mode,
    )

    stt = _build_stt()
    llm = _build_llm()
    tts = _build_tts()

    session = AgentSession(
        turn_detection=_turn_detection(),
        min_endpointing_delay=_endpointing_delay(),
    )

    await session.start(
        agent=FridayAgent(stt=stt, llm=llm, tts=tts, wake_word_mode=wake_word_mode),
        room=ctx.room,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    _require_livekit()
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))

def dev():
    """Wrapper to run the agent in dev mode automatically."""
    import sys
    # If no command was provided, inject 'dev'
    if len(sys.argv) == 1:
        sys.argv.append("dev")
    main()

if __name__ == "__main__":
    main()
