"""
Short-form video generation helpers for FRIDAY.

This module prepares dated project folders for short-form videos and, when
possible, runs the local ShortsMaker/ClipForge pipeline to render the final
MP4. The output is designed to be upload-ready by default.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any


_SHORTS_MAKER_ENV_VARS = ("SHORTS_MAKER_DIR", "SHORTSMAKER_DIR")
_DEFAULT_SHORTS_OUTPUT_ROOT = pathlib.Path("C:/Edits/AI Vids")


def _slugify(text: str, fallback: str = "short") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or fallback


def _resolve_path(path: str) -> pathlib.Path:
    raw = path.strip()
    if not raw:
        raise ValueError("path cannot be empty")
    return pathlib.Path(raw).expanduser()


def _normalize_tags(raw_tags: str) -> list[str]:
    tags: list[str] = []
    for item in raw_tags.split(","):
        tag = item.strip().lstrip("#")
        if tag:
            tags.append(tag)
    return tags


def _derive_title(description: str) -> str:
    cleaned = " ".join(description.strip().split())
    if not cleaned:
        return "Untitled Short"
    sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    words = sentence.split()
    if len(words) <= 8:
        return sentence[:80].strip() or "Untitled Short"
    return " ".join(words[:8]).strip().rstrip(".,;:!?") + "..."


def _load_shortsmaker_dir(shorts_maker_dir: str = "") -> pathlib.Path | None:
    candidate = shorts_maker_dir.strip()
    if candidate:
        path = _resolve_path(candidate)
        if path.exists():
            return path

    for env_name in _SHORTS_MAKER_ENV_VARS:
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            path = _resolve_path(env_value)
            if path.exists():
                return path

    return None


def _candidate_python_interpreters(shorts_maker_dir: pathlib.Path | None) -> list[pathlib.Path]:
    candidates: list[pathlib.Path] = []
    if shorts_maker_dir is not None:
        windows_python = shorts_maker_dir / ".venv" / "Scripts" / "python.exe"
        linux_python = shorts_maker_dir / ".venv" / "bin" / "python"
        if windows_python.exists():
            candidates.append(windows_python)
        if linux_python.exists():
            candidates.append(linux_python)
    candidates.append(pathlib.Path(sys.executable))
    return candidates


def _default_output_root() -> pathlib.Path:
    env_value = os.getenv("FRIDAY_SHORTS_OUTPUT_ROOT", "").strip()
    if env_value:
        return _resolve_path(env_value)
    return _DEFAULT_SHORTS_OUTPUT_ROOT


def _date_folder(root: pathlib.Path) -> pathlib.Path:
    return root / _dt.date.today().isoformat()


def _build_setup_config(cache_dir: pathlib.Path, assets_dir: pathlib.Path) -> dict[str, Any]:
    return {
        "hugging_face_access_token": "",
        "cache_dir": str(cache_dir),
        "assets_dir": str(assets_dir),
        "retry": {"max_retries": 3, "delay": 5},
        "notify": False,
        "reddit_praw": {
            "client_id": "",
            "client_secret": "",
            "user_agent": "Friday Shorts Generator",
        },
        "reddit_post_getter": {
            "subreddit_name": "friday",
            "record_file_json": "post.json",
            "record_file_txt": "post.txt",
        },
        "audio": {
            "output_script_file": "generated_audio_script.txt",
            "output_audio_file": "output.wav",
            "transcript_json": "transcript.json",
            "device": "cpu",
            "model": "large-v2",
            "batch_size": 16,
            "compute_type": "int8",
        },
        "video": {
            "background_videos_urls": [],
            "background_music_urls": [],
            "font_dir": "fonts",
            "credits_dir": "credits",
        },
    }


def _write_json_text(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _write_render_script(
    script_path: pathlib.Path,
    setup_file: pathlib.Path,
    source_text_file: pathlib.Path,
    output_video: pathlib.Path,
    speed_factor: float,
) -> None:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_text = f"""
from pathlib import Path
import json
import sys

import yaml

from ShortsMaker import MoviepyCreateVideo, ShortsMaker

setup_file = Path({str(setup_file)!r})
source_text_file = Path({str(source_text_file)!r})
output_video = Path({str(output_video)!r})
speed_factor = {float(speed_factor)!r}

with setup_file.open("r", encoding="utf-8") as handle:
    cfg = yaml.safe_load(handle)

cache_dir = Path(cfg["cache_dir"])
audio_output = cache_dir / cfg["audio"]["output_audio_file"]
script_output = cache_dir / cfg["audio"]["output_script_file"]
transcript_output = cache_dir / cfg["audio"]["transcript_json"]

source_text = source_text_file.read_text(encoding="utf-8")

generator = ShortsMaker(str(setup_file))
generator.generate_audio(
    source_txt=source_text,
    output_audio=str(audio_output),
    output_script_file=str(script_output),
)
generator.generate_audio_transcript(
    source_audio_file=str(audio_output),
    source_text_file=str(script_output),
)
generator.quit()

video = MoviepyCreateVideo(
    config_file=str(setup_file),
    speed_factor=speed_factor,
)
video(output_path=str(output_video))
video.quit()

print(json.dumps({{"output_video": str(output_video)}}))
""".strip()
    script_path.write_text(script_text + "\n", encoding="utf-8")


def _run_render_script(
    python_executable: pathlib.Path,
    script_path: pathlib.Path,
    shorts_maker_dir: pathlib.Path | None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    pythonpath_parts: list[str] = []
    if shorts_maker_dir is not None:
        pythonpath_parts.append(str(shorts_maker_dir))
    existing_pythonpath = env.get("PYTHONPATH", "").strip()
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    if pythonpath_parts:
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    return subprocess.run(
        [str(python_executable), str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(script_path.parent),
        env=env,
        check=False,
    )


def register(mcp):

    @mcp.tool()
    def create_short_video(
        description: str,
        script_text: str = "",
        title: str = "",
        style: str = "",
        tags: str = "",
        output_root: str = "",
        shorts_maker_dir: str = "",
        render: bool = True,
        speed_factor: float = 1.0,
    ) -> dict[str, Any]:
        """
        Prepare a dated short-form video project and optionally render it with ShortsMaker.
        """
        description = description.strip()
        if not description:
            raise ValueError("description cannot be empty")

        final_title = title.strip() or _derive_title(description)
        narration = script_text.strip() or description
        normalized_tags = _normalize_tags(tags)

        root = _resolve_path(output_root) if output_root.strip() else _default_output_root()
        dated_root = _date_folder(root)
        project_dir = dated_root / _slugify(final_title)
        timestamp = _dt.datetime.now().strftime("%H%M%S")
        project_dir = project_dir / timestamp
        cache_dir = project_dir / "cache"
        assets_dir = project_dir / "assets"
        setup_file = project_dir / "setup.yml"
        prompt_file = project_dir / "prompt.md"
        script_file = project_dir / "script.txt"
        manifest_file = project_dir / "manifest.json"
        render_script_file = project_dir / "_render_short.py"
        output_video = project_dir / "final.mp4"

        shorts_path = _load_shortsmaker_dir(shorts_maker_dir)
        using_repo_assets = False
        if shorts_path is not None:
            candidate_assets = shorts_path / "assets"
            if candidate_assets.exists():
                assets_dir = candidate_assets
                using_repo_assets = True
        assets_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)

        if shorts_path is not None and not using_repo_assets:
            for subfolder in ("background_videos", "background_music", "credits", "fonts"):
                (assets_dir / subfolder).mkdir(parents=True, exist_ok=True)

        setup_data = _build_setup_config(cache_dir=cache_dir, assets_dir=assets_dir)
        _write_json_text(setup_file, setup_data)
        prompt_file.write_text(
            "\n".join(
                [
                    f"# {final_title}",
                    "",
                    "## Description",
                    description,
                    "",
                    "## Style",
                    style.strip() or "Default",
                    "",
                    "## Tags",
                    ", ".join(normalized_tags) if normalized_tags else "",
                    "",
                ]
            ).rstrip()
            + "\n",
            encoding="utf-8",
        )
        script_file.write_text(narration.rstrip() + "\n", encoding="utf-8")

        manifest = {
            "title": final_title,
            "description": description,
            "style": style.strip(),
            "tags": normalized_tags,
            "script_text": narration,
            "output_root": str(root),
            "project_dir": str(project_dir),
            "setup_file": str(setup_file),
            "script_file": str(script_file),
            "output_video": str(output_video),
            "shorts_maker_dir": str(shorts_path) if shorts_path is not None else "",
            "render_requested": render,
            "created_at": _dt.datetime.now().isoformat(),
        }
        _write_json_text(manifest_file, manifest)

        result: dict[str, Any] = {
            "created": True,
            "title": final_title,
            "project_dir": str(project_dir),
            "setup_file": str(setup_file),
            "script_file": str(script_file),
            "prompt_file": str(prompt_file),
            "manifest_file": str(manifest_file),
            "output_video": str(output_video),
            "rendered": False,
            "render_stdout": "",
            "render_stderr": "",
            "render_returncode": None,
            "shorts_maker_dir": str(shorts_path) if shorts_path is not None else "",
            "speed_factor": float(speed_factor),
        }

        if not render:
            return result

        if shorts_path is None:
            result["error"] = (
                "ShortsMaker was not found. Set SHORTS_MAKER_DIR or point this tool at a local shorts_maker checkout."
            )
            return result

        python_candidates = _candidate_python_interpreters(shorts_path)
        render_script_file = render_script_file.with_suffix(".py")
        _write_render_script(
            script_path=render_script_file,
            setup_file=setup_file,
            source_text_file=script_file,
            output_video=output_video,
            speed_factor=speed_factor,
        )

        last_error: subprocess.CompletedProcess[str] | None = None
        for python_executable in python_candidates:
            completed = _run_render_script(python_executable, render_script_file, shorts_path)
            if completed.returncode == 0:
                result.update(
                    {
                        "rendered": True,
                        "render_stdout": completed.stdout.strip(),
                        "render_stderr": completed.stderr.strip(),
                        "render_returncode": completed.returncode,
                        "python_executable": str(python_executable),
                    }
                )
                return result
            last_error = completed

        if last_error is not None:
            result.update(
                {
                    "rendered": False,
                    "render_stdout": last_error.stdout.strip(),
                    "render_stderr": last_error.stderr.strip(),
                    "render_returncode": last_error.returncode,
                    "python_executable": str(python_candidates[-1]),
                    "error": "ShortsMaker rendering failed. See render_stderr for the last attempt.",
                }
            )
        return result
