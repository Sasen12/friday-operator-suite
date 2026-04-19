# FRIDAY Cheat Sheet

FRIDAY is a desktop operator. It can control Windows, work with files, automate browser tabs, handle Gmail and Calendar, manage Obsidian notes, and replay saved macros.

## What FRIDAY Can Do

- Open, focus, move, resize, snap, and close windows.
- Search installed apps and launch system surfaces like Start, Run, Settings, Explorer, Terminal, Task Manager, Device Manager, Services, and more.
- Inspect the active window, find controls by label, click controls, type into fields, take screenshots, and OCR what is on screen.
- Read, write, search, and replace text in files.
- Read and write JSON.
- Hash files, zip folders, and unzip archives.
- Search filenames and search inside file contents.
- Run shell commands, PowerShell scripts, and background processes.
- List, search, and stop running processes.
- Open URLs, search the web, read page text, click links and buttons, fill forms, and batch browser steps.
- Open Gmail and Calendar, search messages, open threads, draft mail, search events, and create calendar events.
- Open Obsidian vaults, search notes, search note contents, create notes, append to notes, and replace text inside notes.
- Open and search folders in File Explorer.
- Save, list, run, and delete reusable macros.
- Build short-form video projects from a description or script, save them in dated folders under `C:/Edits/AI Vids` by default, and render them locally when the ShortsMaker checkout is available.
- Fetch world news and open the world monitor dashboard.

## Best Ways To Ask

- "Open Obsidian and create a new note called Meeting Notes. Put this text in it: ..."
- "Search my Downloads folder for anything with invoice in the name."
- "Find the text API key inside my notes."
- "Open Firefox and go to this website."
- "Search Gmail for receipts from OpenAI."
- "Create a calendar event for Friday at 3 PM."
- "Open Settings and show me Wi-Fi."
- "Zip this folder for me."
- "Run my cleanup macro."

## Good Prompt Patterns

- Use direct action language: open, search, create, append, replace, launch, reveal, zip, hash, run, focus.
- Include the target, the content, and the result you want.
- If you want multiple steps, say them in order.
- If you want Friday to reuse a routine later, ask to save it as a macro.

## Examples

- "Open Obsidian, create a note named Project Plan, and write the text below."
- "Search my vault for notes about Friday automation."
- "Open Firefox to YouTube and search for live coding streams."
- "Open File Explorer in Downloads and reveal the newest PDF."
- "Read the current Gmail thread and summarize it."
- "Create a calendar event called Dentist Appointment tomorrow from 2 PM to 3 PM."
- "Search my files for the phrase livekit cloud."
- "Zip the folder called archive-me."
- "Hash this file with sha256."
- "Make me a YouTube Short from this idea and save the finished video in today's folder."

## Batch And Macro Power

- Use `run_desktop_actions` for multi-step GUI work.
- Use `run_system_actions` for file, shell, and process sequences.
- Use `run_workflow_actions` for Obsidian, Firefox, and File Explorer sequences.
- Use `browser_run_actions` for multi-step browser routines.
- Use `save_macro` when a routine is worth repeating.
- Use `run_macro` to replay a saved routine later.
- Use the shorts tools to turn an idea into a dated, upload-ready short-form video project.

## Limits To Know

- Deleting, overwriting, force-closing, and sending email still ask for confirmation.
- Some actions depend on installed apps or logged-in accounts.
- Browser automation works best when the page is accessible and stable.

## Short Version

- If it involves your laptop, Friday can probably do it.
- If it is repeatable, Friday can batch it.
- If it is worth repeating often, Friday can save it as a macro.
