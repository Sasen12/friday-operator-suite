from __future__ import annotations

import asyncio
import io
import logging
import os
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from faster_whisper import WhisperModel
from huggingface_hub import hf_hub_download
from piper import PiperVoice, SynthesisConfig

logger = logging.getLogger(__name__)

_PIPER_VOICE_REPO = "rhasspy/piper-voices"


@dataclass(frozen=True)
class LocalSpeechConfig:
    stt_model: str
    stt_device: str
    stt_compute_type: str
    stt_language: str
    stt_download_root: Path
    stt_local_files_only: bool
    tts_model: str
    tts_config_path: Path | None
    tts_download_dir: Path
    tts_use_cuda: bool
    tts_speed: float
    tts_volume: float


@dataclass(frozen=True)
class LocalAudioFrame:
    data: bytes
    sample_rate: int
    num_channels: int
    samples_per_channel: int


def _env_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


def _env_path(value: str | None) -> Path | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return Path(value).expanduser()


def build_local_speech_config(
    *, tts_speed: float, env: Mapping[str, str] | None = None
) -> LocalSpeechConfig:
    env = env or os.environ

    stt_download_root = _env_path(env.get("FRIDAY_LOCAL_STT_DOWNLOAD_ROOT"))
    if stt_download_root is None:
        stt_download_root = Path.home() / ".cache" / "friday" / "whisper"

    tts_download_dir = _env_path(env.get("FRIDAY_LOCAL_TTS_DOWNLOAD_DIR"))
    if tts_download_dir is None:
        tts_download_dir = Path.home() / ".cache" / "friday" / "piper"

    tts_config_path = _env_path(env.get("FRIDAY_LOCAL_TTS_CONFIG"))

    return LocalSpeechConfig(
        stt_model=env.get("FRIDAY_LOCAL_STT_MODEL", "base.en").strip() or "base.en",
        stt_device=env.get("FRIDAY_LOCAL_STT_DEVICE", "cpu").strip() or "cpu",
        stt_compute_type=env.get("FRIDAY_LOCAL_STT_COMPUTE_TYPE", "int8").strip() or "int8",
        stt_language=env.get("FRIDAY_LOCAL_STT_LANGUAGE", "en").strip() or "en",
        stt_download_root=stt_download_root,
        stt_local_files_only=_env_bool(env.get("FRIDAY_LOCAL_STT_OFFLINE"), default=False),
        tts_model=env.get("FRIDAY_LOCAL_TTS_MODEL", "en_US-lessac-medium").strip()
        or "en_US-lessac-medium",
        tts_config_path=tts_config_path,
        tts_download_dir=tts_download_dir,
        tts_use_cuda=_env_bool(env.get("FRIDAY_LOCAL_TTS_USE_CUDA"), default=False),
        tts_speed=max(float(tts_speed), 0.01),
        tts_volume=float(env.get("FRIDAY_LOCAL_TTS_VOLUME", "1.0") or 1.0),
    )


@lru_cache(maxsize=4)
def _load_whisper_model(
    model_size_or_path: str,
    device: str,
    compute_type: str,
    download_root: str,
    local_files_only: bool,
) -> WhisperModel:
    logger.info(
        "Loading local Whisper model %s (device=%s, compute_type=%s)",
        model_size_or_path,
        device,
        compute_type,
    )
    return WhisperModel(
        model_size_or_path=model_size_or_path,
        device=device,
        compute_type=compute_type,
        download_root=download_root,
        local_files_only=local_files_only,
    )


def _resolve_piper_model_spec(
    spec: str, download_dir: Path, config_path: Path | None
) -> tuple[Path, Path]:
    candidate = Path(spec).expanduser()
    if candidate.exists() or candidate.suffix.lower() == ".onnx" or os.sep in spec or (
        os.altsep and os.altsep in spec
    ):
        model_path = candidate
        resolved_config = config_path or Path(f"{candidate}.json")
        return model_path, resolved_config

    parts = spec.split("-")
    if len(parts) < 3:
        raise ValueError(
            "FRIDAY_LOCAL_TTS_MODEL must be a local .onnx path or a Piper voice preset "
            "such as en_US-lessac-medium"
        )

    quality = parts[-1]
    voice_name = parts[-2]
    lang_tag = "-".join(parts[:-2])
    language = lang_tag.split("_", 1)[0]
    repo_prefix = f"{language}/{lang_tag}/{voice_name}/{quality}/{spec}"

    model_file = hf_hub_download(
        repo_id=_PIPER_VOICE_REPO,
        filename=f"{repo_prefix}.onnx",
        cache_dir=str(download_dir),
    )
    config_file = config_path or Path(
        hf_hub_download(
            repo_id=_PIPER_VOICE_REPO,
            filename=f"{repo_prefix}.onnx.json",
            cache_dir=str(download_dir),
        )
    )
    return Path(model_file), config_file


@lru_cache(maxsize=4)
def _load_piper_voice(
    model_spec: str,
    config_path: str,
    use_cuda: bool,
    download_dir: str,
) -> PiperVoice:
    model_path, resolved_config = _resolve_piper_model_spec(
        model_spec,
        Path(download_dir),
        Path(config_path) if config_path else None,
    )
    logger.info("Loading local Piper voice %s", model_spec)
    return PiperVoice.load(
        model_path=model_path,
        config_path=resolved_config,
        use_cuda=use_cuda,
        download_dir=Path(download_dir),
    )


async def transcribe_audio_frames(
    frames: list[Any], config: LocalSpeechConfig
) -> tuple[str, float, str]:
    if not frames:
        return "", 0.0, config.stt_language

    sample_rate = int(getattr(frames[0], "sample_rate", 16000) or 16000)
    num_channels = int(getattr(frames[0], "num_channels", 1) or 1)
    pcm_bytes = b"".join(bytes(getattr(frame, "data", b"")) for frame in frames)

    def _pcm_to_wav_bytes(audio_bytes: bytes, rate: int, channels: int) -> bytes:
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wav_file:
                wav_file.setnchannels(max(channels, 1))
                wav_file.setsampwidth(2)
                wav_file.setframerate(max(rate, 1))
                wav_file.writeframes(audio_bytes)
            return buffer.getvalue()

    wav_bytes = _pcm_to_wav_bytes(pcm_bytes, sample_rate, num_channels)

    def _run_transcription() -> tuple[str, float, str]:
        model = _load_whisper_model(
            config.stt_model,
            config.stt_device,
            config.stt_compute_type,
            str(config.stt_download_root),
            config.stt_local_files_only,
        )
        segments, info = model.transcribe(
            io.BytesIO(wav_bytes),
            language=config.stt_language,
            task="transcribe",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            vad_filter=False,
            condition_on_previous_text=False,
            word_timestamps=False,
            suppress_blank=True,
        )
        transcript = "".join(segment.text for segment in segments).strip()
        detected_language = info.language or config.stt_language
        return transcript, info.duration, detected_language

    return await asyncio.to_thread(_run_transcription)


async def synthesize_text_frames(
    text: str, config: LocalSpeechConfig
) -> list[LocalAudioFrame]:
    cleaned_text = text.strip()
    if not cleaned_text:
        return []

    def _run_synthesis() -> list[LocalAudioFrame]:
        voice = _load_piper_voice(
            config.tts_model,
            str(config.tts_config_path) if config.tts_config_path else "",
            config.tts_use_cuda,
            str(config.tts_download_dir),
        )

        length_scale = 1.0 / config.tts_speed if config.tts_speed > 0 else 1.0
        syn_config = SynthesisConfig(
            length_scale=length_scale,
            volume=config.tts_volume,
        )

        frames: list[LocalAudioFrame] = []
        for chunk in voice.synthesize(cleaned_text, syn_config=syn_config):
            pcm = chunk.audio_int16_bytes
            samples_per_channel = len(pcm) // (2 * chunk.sample_channels)
            frames.append(
                LocalAudioFrame(
                    data=pcm,
                    sample_rate=chunk.sample_rate,
                    num_channels=chunk.sample_channels,
                    samples_per_channel=samples_per_channel,
                )
            )

        return frames

    return await asyncio.to_thread(_run_synthesis)
