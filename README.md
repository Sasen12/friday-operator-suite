# F.R.I.D.A.Y. - Tony Stark Demo

F.R.I.D.A.Y. is a local AI control stack for Windows that combines voice, browser, desktop, file, and workflow automation in one place.

This repo is a remix of the original `SAGAR-TAMANG/friday-tony-stark-demo` project.

It has three main pieces:

| Component | What it does |
|-----------|--------------|
| MCP server (`friday`) | Exposes tools over SSE so the assistant can act on the machine, files, browser, and workflows. |
| Voice agent (`friday_voice`) | Connects to LiveKit, listens to your microphone, reasons with an LLM, and calls the MCP tools in real time. |
| Desktop shell (`run-friday-app.ps1`) | Opens a local control deck with the embedded LiveKit view and quick actions. |

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
|-- agent_friday.py      # LiveKit voice agent entry point
|-- desktop-app/         # Electron control deck
|-- friday/
|   |-- config.py
|   |-- prompts/
|   |-- resources/
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
- A LiveKit Cloud project

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

Fill in the keys in `.env` before starting the app.

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
friday
friday_voice dev
```

Or use the text console instead of the LiveKit browser session:

```powershell
friday_voice console --text
```

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
| `LIVEKIT_URL` | yes | Your LiveKit Cloud websocket URL |
| `LIVEKIT_API_KEY` | yes | LiveKit API key |
| `LIVEKIT_API_SECRET` | yes | LiveKit API secret |
| `OPENAI_API_KEY` | yes | Used for the LLM, Whisper STT, and TTS |
| `FRIDAY_SHORTS_OUTPUT_ROOT` | no | Default output folder for short-form video projects |
| `SHORTS_MAKER_DIR` | no | Path to your local `shorts_maker` checkout |

The current voice stack is OpenAI-only for speech and language, so the voice side only needs the LiveKit keys plus `OPENAI_API_KEY`.

---

## Safety

Friday asks for confirmation before destructive actions, overwriting files, force-closing programs, and sending email.

When a GUI is not needed, it should prefer file, shell, and workflow tools over manual clicking.

---

## Tech Stack

- FastMCP
- LiveKit Agents
- Playwright
- Electron
- Python 3.11+

---

## License

MIT
