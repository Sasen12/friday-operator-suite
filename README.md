# F.R.I.D.A.Y. - Tony Stark Demo

F.R.I.D.A.Y. is a local AI control stack for Windows that combines voice, browser, desktop, file, and workflow automation in one place.

This repo is a remix of the original `SAGAR-TAMANG/friday-tony-stark-demo` project.

It has three main pieces:

| Component | What it does |
|-----------|--------------|
| MCP server (`friday`) | Exposes tools over SSE so the assistant can act on the machine, files, browser, and workflows. |
| Local voice runtime (`local_friday.py`) | Runs fully on-device with Whisper, Piper, Ollama, and the MCP tools. |
| Desktop shell (`run-friday-app.ps1`) | Opens a local control deck and launches the on-device voice/runtime loop. |

`agent_friday.py` is still here as a legacy LiveKit-based mode, but you do not need it for the local stack.

---

## What Friday can do

- Control Windows apps, windows, shortcuts, and common system surfaces
- Open and focus apps, search installed programs, and move or resize windows
- Type, click, scroll, drag, and use OCR when a control is not directly accessible
- Work with files and folders, including read, write, move, copy, hash, zip, unzip, search, and delete actions
- Run PowerShell, shell commands, and process management tasks
- Search inside files and replace text in place
- Open Obsidian, Firefox, and File Explorer through dedicated workflows
- Work with Gmail and Google Calendar through browser-backed flows
- Automate websites with Playwright
- Save, list, run, and delete reusable macros
- Turn a prompt into a short-form vertical video project and render it locally when a ShortsMaker checkout is available

---

## Project Layout

```text
friday-operator-suite/
|-- server.py            # MCP server entry point
|-- agent_friday.py      # Legacy LiveKit voice agent entry point
|-- local_friday.py      # Local on-device voice entry point
|-- desktop-app/         # Electron control deck
|-- friday/
|   |-- config.py
|   |-- prompts/
|   |-- resources/
|   |-- speech.py
|   |-- tools/
|   |   |-- browser.py
|   |   |-- desktop.py
|   |   |-- macros.py
|   |   |-- office.py
|   |   |-- shorts.py
|   |   |-- system.py
|   |   |-- utils.py
|   |   |-- web.py
|   |   `-- workflows.py
|-- FRIDAY_CHEAT_SHEET.md
|-- run-friday.ps1
|-- run-friday-app.ps1
|-- pyproject.toml
`-- .env.example
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11 or newer
- `uv`
- Ollama with `gemma4` pulled locally

### 2. Clone and install

```powershell
git clone https://github.com/Sasen12/friday-operator-suite.git
cd friday-operator-suite
uv sync
```

### 3. Set up environment

```powershell
Copy-Item .env.example .env
```

The local mode works with the defaults in `.env.example`. You only need to edit it if you want different model names, cache paths, or the legacy LiveKit mode.

### 4. Run Friday

For the quickest path on Windows:

```powershell
.\run-friday.ps1
```

For the local desktop shell:

```powershell
.\run-friday-app.ps1
```

You can also run the pieces separately:

```powershell
uv run python server.py
uv run python local_friday.py
```

Or use the text console instead of the microphone:

```powershell
uv run python local_friday.py console
```

By default, the local voice session responds directly to speech. If you want wake-word gating back, set `FRIDAY_WAKE_WORD_MODE=1` before launching.

Friday now supports a fully local speech stack through Whisper and Piper. If you keep the defaults, the first run may download the speech models once and then run locally after that. To force the local path, set `FRIDAY_SPEECH_PROVIDER=local`.

The desktop shell and launch scripts now start the Python modules directly instead of the generated `friday.exe` / `friday_voice.exe` shims, which avoids the Windows file-lock issue you were seeing.

To use a local Ollama model for Friday's reasoning, set `FRIDAY_LLM_PROVIDER=ollama`, `OLLAMA_BASE_URL=http://127.0.0.1:11434/v1/`, and `OLLAMA_LLM_MODEL=gemma4`.

If you still want the old browser-based LiveKit mode, you can keep the `LIVEKIT_*` variables and run `agent_friday.py` directly. That path is now legacy.

---

## Short-Form Video Workflow

Friday can build a short-form video project from a plain description or script and save it into a dated folder under `C:/Edits/AI Vids` by default.

- If `SHORTS_MAKER_DIR` points to a local `shorts_maker` checkout, Friday will try to render the final MP4 locally.
- If the checkout is missing, Friday still prepares the project folder with the title, prompt, script, manifest, and render notes so you can review or upload later.
- You can override the output root with `FRIDAY_SHORTS_OUTPUT_ROOT` if you want a different folder.

Example prompt:

```text
Make a YouTube Short about the 3 biggest mistakes people make with productivity.
Keep it punchy, vertical, and save the final project locally.
```

---

## Environment Variables

| Variable | Required | Notes |
|----------|----------|-------|
| `LIVEKIT_URL` | legacy | Your LiveKit Cloud websocket URL, only for the old LiveKit mode |
| `LIVEKIT_API_KEY` | legacy | LiveKit API key for the old LiveKit mode |
| `LIVEKIT_API_SECRET` | legacy | LiveKit API secret for the old LiveKit mode |
| `OPENAI_API_KEY` | legacy | Only needed for the old cloud speech/LLM path |
| `FRIDAY_SPEECH_PROVIDER` | no | `local` or `openai` |
| `FRIDAY_STT_PROVIDER` | no | Overrides speech recognition only; defaults to `FRIDAY_SPEECH_PROVIDER` |
| `FRIDAY_TTS_PROVIDER` | no | Overrides text-to-speech only; defaults to `FRIDAY_SPEECH_PROVIDER` |
| `FRIDAY_LOCAL_STT_MODEL` | no | Whisper model name or local path, for example `base.en` |
| `FRIDAY_LOCAL_STT_DEVICE` | no | Whisper device, usually `cpu` |
| `FRIDAY_LOCAL_STT_COMPUTE_TYPE` | no | Whisper compute type, usually `int8` on CPU |
| `FRIDAY_LOCAL_STT_LANGUAGE` | no | Whisper language hint, usually `en` |
| `FRIDAY_LOCAL_STT_DOWNLOAD_ROOT` | no | Cache directory for Whisper models |
| `FRIDAY_LOCAL_STT_OFFLINE` | no | Set to `1` to require local cached Whisper files only |
| `FRIDAY_LOCAL_TTS_MODEL` | no | Piper voice preset or local `.onnx` file, for example `en_US-lessac-medium` |
| `FRIDAY_LOCAL_TTS_CONFIG` | no | Optional Piper `.onnx.json` config path for a custom voice |
| `FRIDAY_LOCAL_TTS_DOWNLOAD_DIR` | no | Cache directory for Piper voice downloads |
| `FRIDAY_LOCAL_TTS_USE_CUDA` | no | Set to `1` to let Piper use CUDA if available |
| `FRIDAY_LOCAL_TTS_VOLUME` | no | Piper volume multiplier, default `1.0` |
| `FRIDAY_LLM_PROVIDER` | no | `openai` or `ollama` |
| `OLLAMA_BASE_URL` | no | Ollama OpenAI-compatible endpoint, usually `http://127.0.0.1:11434/v1/` |
| `OLLAMA_LLM_MODEL` | no | Ollama model name, for example `gemma4` |
| `FRIDAY_SHORTS_OUTPUT_ROOT` | no | Default output folder for short-form video projects |
| `SHORTS_MAKER_DIR` | no | Path to your local `shorts_maker` checkout |

The voice side can now run fully local with Whisper and Piper. If you want to keep everything offline after the first model download, leave `FRIDAY_SPEECH_PROVIDER=local` enabled and set `FRIDAY_LOCAL_STT_OFFLINE=1` once the Whisper model is cached.

---

## Safety

Friday asks for confirmation before destructive actions, overwriting files, force-closing programs, and sending email.

When a GUI is not needed, it should prefer file, shell, and workflow tools over manual clicking.

---

## Tech Stack

- FastMCP
- LiveKit Agents (legacy)
- Playwright
- Electron
- Python 3.11+

---

## License

MIT
