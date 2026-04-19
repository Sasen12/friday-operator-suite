"""
High-level app workflows for FRIDAY.

These helpers stitch together the lower-level desktop and system tools into
task-oriented flows for Obsidian, Firefox, and File Explorer.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import pathlib
import re
import subprocess
import time
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote_plus

from . import desktop, system


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _score_match(query: str, candidate: str) -> int:
    normalized_query = _normalize_text(query)
    normalized_candidate = _normalize_text(candidate)
    if not normalized_query or not normalized_candidate:
        return 0
    if normalized_query == normalized_candidate:
        return 100
    if normalized_query in normalized_candidate:
        return 92
    if normalized_candidate in normalized_query:
        return 85

    query_tokens = set(re.findall(r"[a-z0-9]+", normalized_query))
    candidate_tokens = set(re.findall(r"[a-z0-9]+", normalized_candidate))
    overlap = len(query_tokens & candidate_tokens)
    score = 0
    if overlap:
        score = max(score, 62 + overlap * 12)

    ratio = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
    score = max(score, int(ratio * 88))
    return score


def _safe_filename(title: str, fallback: str = "Untitled") -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub(" ", title or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(". ")
    return cleaned or fallback


def _resolve_path(text: str) -> pathlib.Path:
    raw = text.strip()
    if not raw:
        raise ValueError("path cannot be empty")
    return pathlib.Path(raw).expanduser()


def _launch_installed_app(query: str, extra_arguments: list[str] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    extra_arguments = extra_arguments or []
    matches = desktop._search_installed_apps(query, limit=5)
    if not matches:
        raise RuntimeError(f"No installed app matched {query!r}.")

    last_error: Exception | None = None
    for entry in matches:
        try:
            launch_ref = desktop._launch_app_entry(entry, extra_arguments=extra_arguments)
            focused = desktop._focus_launched_window(query, entry, timeout=timeout)
            return {
                "entry": entry,
                "launch_ref": launch_ref,
                "focused_window": focused,
            }
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise RuntimeError(f"Found installed app matches for {query!r}, but none launched successfully.") from last_error
    raise RuntimeError(f"Could not launch {query!r}.")


def _obsidian_config_path() -> pathlib.Path:
    appdata = os.getenv("APPDATA", "")
    if not appdata:
        raise RuntimeError("APPDATA is not set.")
    return pathlib.Path(appdata) / "Obsidian" / "obsidian.json"


def _obsidian_vault_records() -> list[dict[str, Any]]:
    config_path = _obsidian_config_path()
    if not config_path.exists():
        return []

    try:
        data = json.loads(config_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []

    vaults = data.get("vaults", {}) if isinstance(data, dict) else {}
    records: list[dict[str, Any]] = []
    if isinstance(vaults, dict):
        for vault_id, payload in vaults.items():
            if not isinstance(payload, dict):
                continue
            raw_path = str(payload.get("path", "") or "").strip()
            if not raw_path:
                continue
            vault_path = pathlib.Path(raw_path).expanduser()
            records.append(
                {
                    "id": str(vault_id),
                    "path": str(vault_path),
                    "exists": vault_path.exists(),
                    "open": bool(payload.get("open", False)),
                    "name": vault_path.name,
                }
            )

    records.sort(key=lambda item: (not item.get("open", False), not item.get("exists", False), item.get("name", "").lower()))
    return records


def _preferred_obsidian_vault(vault_path: str = "") -> pathlib.Path:
    if vault_path.strip():
        candidate = _resolve_path(vault_path)
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"Vault not found: {candidate}")

    records = _obsidian_vault_records()
    for record in records:
        candidate = pathlib.Path(record["path"])
        if candidate.exists():
            return candidate

    raise RuntimeError("No Obsidian vault could be found on this machine.")


def _obsidian_note_matches(root: pathlib.Path, query: str, limit: int = 20) -> list[dict[str, Any]]:
    normalized_query = _normalize_text(query)
    if not root.exists() or not root.is_dir():
        return []

    candidates: list[tuple[int, pathlib.Path]] = []
    for path in root.rglob("*.md"):
        candidate_text = " ".join(
            part for part in (
                path.stem,
                str(path.relative_to(root).parent),
                path.name,
            )
            if part
        )
        score = _score_match(normalized_query, candidate_text)
        if score > 0 or not normalized_query:
            candidates.append((score if normalized_query else 50, path))

    candidates.sort(key=lambda item: (-item[0], str(item[1]).lower()))
    results: list[dict[str, Any]] = []
    for score, path in candidates[: max(limit, 0)]:
        results.append(
            {
                **system._path_record(path),
                "score": score,
                "relative_path": str(path.relative_to(root)),
            }
        )
    return results


def _obsidian_note_path(
    vault_root: pathlib.Path,
    title_or_path: str,
    folder: str = "",
    create: bool = False,
) -> pathlib.Path:
    raw = title_or_path.strip()
    if not raw:
        raise ValueError("title_or_path cannot be empty")

    candidate = pathlib.Path(raw).expanduser()
    if candidate.is_absolute() and candidate.exists():
        return candidate

    if any(sep in raw for sep in ("/", "\\")) or raw.lower().endswith(".md"):
        candidate = (vault_root / raw).expanduser()
        if candidate.exists() or create:
            return candidate

    search_root = vault_root / folder if folder.strip() else vault_root
    matches = _obsidian_note_matches(search_root, raw, limit=20)
    if matches:
        return pathlib.Path(matches[0]["path"])

    if not create:
        raise FileNotFoundError(f"No note matched {title_or_path!r} in {search_root}")

    base_folder = search_root
    base_folder.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(raw, fallback=f"Note-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}")
    if not filename.lower().endswith(".md"):
        filename = f"{filename}.md"
    return base_folder / filename


def _unique_note_path(note_path: pathlib.Path) -> pathlib.Path:
    if not note_path.exists():
        return note_path

    stem = note_path.stem
    suffix = note_path.suffix
    parent = note_path.parent
    for index in range(2, 1000):
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return parent / f"{stem} {timestamp}{suffix}"


def _open_obsidian_with_path(path: pathlib.Path) -> dict[str, Any]:
    launch = _launch_installed_app("Obsidian", extra_arguments=[str(path)], timeout=12.0)
    with contextlib.suppress(Exception):
        desktop.focus_window("Obsidian")
    return launch


def _firefox_entry() -> dict[str, Any]:
    for query in ("Mozilla Firefox", "Firefox"):
        matches = desktop._search_installed_apps(query, limit=5)
        if matches:
            return matches[0]
    raise RuntimeError("Firefox is not installed or not discoverable.")


def _open_firefox_with_args(args: list[str]) -> dict[str, Any]:
    entry = _firefox_entry()
    launch_ref = desktop._launch_app_entry(entry, extra_arguments=args)
    focused = desktop._focus_launched_window("Firefox", entry, timeout=12.0)
    return {
        "entry": entry,
        "launch_ref": launch_ref,
        "focused_window": focused,
    }


def _explorer_executable() -> str:
    windir = os.getenv("WINDIR", r"C:\Windows")
    return str(pathlib.Path(windir) / "explorer.exe")


def _open_explorer_path(path: pathlib.Path) -> dict[str, Any]:
    explorer = _explorer_executable()
    if path.is_file():
        subprocess.Popen([explorer, f"/select,{str(path)}"], shell=False)
        focused = desktop._focus_launched_window("File Explorer", timeout=8.0)
        return {"opened": str(path), "mode": "reveal", "focused_window": focused}

    subprocess.Popen([explorer, str(path)], shell=False)
    focused = desktop._focus_launched_window("File Explorer", timeout=8.0)
    return {"opened": str(path), "mode": "folder", "focused_window": focused}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def register(mcp):

    @mcp.tool()
    def obsidian_list_vaults() -> list[dict[str, Any]]:
        """List Obsidian vaults discovered from Obsidian's config."""
        return _obsidian_vault_records()

    @mcp.tool()
    def obsidian_open_vault(vault_path: str = "", focus: bool = True) -> dict[str, Any]:
        """Open an Obsidian vault in the Obsidian app."""
        vault = _preferred_obsidian_vault(vault_path)
        launch = _open_obsidian_with_path(vault)
        if focus:
            with contextlib.suppress(Exception):
                desktop.focus_window("Obsidian")
        launch.update({"vault_path": str(vault)})
        return launch

    @mcp.tool()
    def obsidian_search_notes(query: str, vault_path: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Search note filenames inside an Obsidian vault."""
        vault = _preferred_obsidian_vault(vault_path)
        return _obsidian_note_matches(vault, query, limit=limit)

    @mcp.tool()
    def obsidian_open_note(
        title_or_path: str,
        vault_path: str = "",
        folder: str = "",
        create_if_missing: bool = False,
        focus: bool = True,
    ) -> dict[str, Any]:
        """Open a note in Obsidian, optionally creating it if it doesn't exist."""
        vault = _preferred_obsidian_vault(vault_path)
        try:
            note_path = _obsidian_note_path(vault, title_or_path, folder=folder, create=create_if_missing)
        except FileNotFoundError as exc:
            return {
                "vault_path": str(vault),
                "requested": title_or_path,
                "opened": False,
                "error": str(exc),
                "matches": _obsidian_note_matches(vault / folder if folder.strip() else vault, title_or_path, limit=10),
            }

        if not note_path.exists() and create_if_missing:
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.touch()

        launch = _open_obsidian_with_path(note_path)
        if focus:
            with contextlib.suppress(Exception):
                desktop.focus_window("Obsidian")
        launch.update({"vault_path": str(vault), "note_path": str(note_path), "opened": True})
        return launch

    @mcp.tool()
    def obsidian_create_note(
        title: str,
        body: str = "",
        vault_path: str = "",
        folder: str = "",
        open_in_obsidian: bool = True,
    ) -> dict[str, Any]:
        """Create a markdown note in the selected Obsidian vault."""
        vault = _preferred_obsidian_vault(vault_path)
        note_path = _unique_note_path(_obsidian_note_path(vault, title, folder=folder, create=True))
        note_path.parent.mkdir(parents=True, exist_ok=True)

        parts = [f"# {_safe_filename(title, fallback='Untitled')}"]
        if body.strip():
            parts.append("")
            parts.append(body.rstrip())
        content = "\n".join(parts).rstrip() + "\n"
        note_path.write_text(content, encoding="utf-8")

        launch: dict[str, Any] | None = None
        if open_in_obsidian:
            launch = _open_obsidian_with_path(note_path)
            with contextlib.suppress(Exception):
                desktop.focus_window("Obsidian")

        result = {
            "vault_path": str(vault),
            "note_path": str(note_path),
            "created": True,
            "bytes_written": len(content.encode("utf-8")),
        }
        if launch is not None:
            result.update(launch)
        return result

    @mcp.tool()
    def obsidian_append_to_note(
        title_or_path: str,
        text: str,
        vault_path: str = "",
        folder: str = "",
        create_if_missing: bool = False,
        open_in_obsidian: bool = True,
    ) -> dict[str, Any]:
        """Append text to an existing Obsidian note, or create it first."""
        vault = _preferred_obsidian_vault(vault_path)
        try:
            note_path = _obsidian_note_path(vault, title_or_path, folder=folder, create=create_if_missing)
        except FileNotFoundError as exc:
            return {
                "vault_path": str(vault),
                "requested": title_or_path,
                "appended": False,
                "error": str(exc),
            }

        if not note_path.exists():
            if not create_if_missing:
                return {
                    "vault_path": str(vault),
                    "requested": title_or_path,
                    "appended": False,
                    "error": f"No note matched {title_or_path!r}.",
                }
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.touch()

        with note_path.open("a", encoding="utf-8") as handle:
            if note_path.stat().st_size > 0:
                handle.write("\n")
            handle.write(text.rstrip() + "\n")

        launch: dict[str, Any] | None = None
        if open_in_obsidian:
            launch = _open_obsidian_with_path(note_path)
            with contextlib.suppress(Exception):
                desktop.focus_window("Obsidian")

        result = {
            "vault_path": str(vault),
            "note_path": str(note_path),
            "appended": True,
            "text_length": len(text),
        }
        if launch is not None:
            result.update(launch)
        return result

    @mcp.tool()
    def obsidian_search_note_contents(
        query: str,
        vault_path: str = "",
        folder: str = "",
        limit: int = 50,
        case_sensitive: bool = False,
        regex: bool = False,
    ) -> list[dict[str, Any]]:
        """Search the text inside markdown notes in an Obsidian vault."""
        vault = _preferred_obsidian_vault(vault_path)
        search_root = vault / folder if folder.strip() else vault
        if not search_root.exists():
            raise FileNotFoundError(f"Search root not found: {search_root}")
        if not search_root.is_dir():
            raise NotADirectoryError(f"Not a directory: {search_root}")

        return system.search_file_contents(
            root=str(search_root),
            query=query,
            recursive=True,
            limit=limit,
            case_sensitive=case_sensitive,
            regex=regex,
            extensions=".md",
        )

    @mcp.tool()
    def obsidian_replace_in_note(
        title_or_path: str,
        old: str,
        new: str,
        vault_path: str = "",
        folder: str = "",
        create_if_missing: bool = False,
        regex: bool = False,
        case_sensitive: bool = True,
        count: int = 0,
    ) -> dict[str, Any]:
        """Replace text inside an Obsidian note."""
        vault = _preferred_obsidian_vault(vault_path)
        note_path = _obsidian_note_path(vault, title_or_path, folder=folder, create=create_if_missing)
        if not note_path.exists():
            if not create_if_missing:
                raise FileNotFoundError(f"No note matched {title_or_path!r}")
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.touch()

        result = system.replace_in_file(
            path=str(note_path),
            old=old,
            new=new,
            count=count,
            regex=regex,
            case_sensitive=case_sensitive,
        )
        result.update(
            {
                "vault_path": str(vault),
                "note_path": str(note_path),
            }
        )
        return result

    @mcp.tool()
    def firefox_focus() -> dict[str, Any]:
        """Open or focus Firefox."""
        return _open_firefox_with_args([])

    @mcp.tool()
    def firefox_open_url(url: str, focus: bool = True, new_window: bool = False) -> dict[str, Any]:
        """Open a URL in Firefox."""
        raw_url = url.strip()
        if not raw_url:
            raise ValueError("url cannot be empty")
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", raw_url):
            target_url = raw_url
        elif re.match(r"^[\w.-]+\.[a-zA-Z]{2,}([/:?#].*)?$", raw_url):
            target_url = f"https://{raw_url}"
        else:
            target_url = f"https://www.google.com/search?q={quote_plus(raw_url)}"

        args: list[str] = []
        if new_window:
            args.append("--new-window")
        args.append(target_url)
        launch = _open_firefox_with_args(args)
        if focus:
            with contextlib.suppress(Exception):
                desktop.focus_window("Firefox")
        launch.update({"url": target_url, "focus": focus, "new_window": new_window})
        return launch

    @mcp.tool()
    def firefox_search_web(query: str, focus: bool = True, new_window: bool = False) -> dict[str, Any]:
        """Search the web in Firefox."""
        raw_query = query.strip()
        if not raw_query:
            raise ValueError("query cannot be empty")
        return firefox_open_url(raw_query, focus=focus, new_window=new_window)

    @mcp.tool()
    def chrome_focus() -> dict[str, Any]:
        """Compatibility alias for Firefox focus."""
        return firefox_focus()

    @mcp.tool()
    def chrome_open_url(url: str, focus: bool = True, new_window: bool = False) -> dict[str, Any]:
        """Compatibility alias for Firefox URL opening."""
        return firefox_open_url(url, focus=focus, new_window=new_window)

    @mcp.tool()
    def chrome_search_web(query: str, focus: bool = True, new_window: bool = False) -> dict[str, Any]:
        """Compatibility alias for Firefox search."""
        return firefox_search_web(query, focus=focus, new_window=new_window)

    @mcp.tool()
    def file_explorer_open(path: str = "", focus: bool = True) -> dict[str, Any]:
        """Open File Explorer at a folder or file location."""
        target = _resolve_path(path) if path.strip() else pathlib.Path.home()
        if not target.exists():
            raise FileNotFoundError(f"Path not found: {target}")
        result = _open_explorer_path(target)
        if focus:
            with contextlib.suppress(Exception):
                desktop.focus_window("File Explorer")
        result.update({"path": str(target), "focus": focus})
        return result

    @mcp.tool()
    def file_explorer_reveal(path: str, focus: bool = True) -> dict[str, Any]:
        """Reveal a file in File Explorer."""
        target = _resolve_path(path)
        if not target.exists():
            raise FileNotFoundError(f"Path not found: {target}")
        result = _open_explorer_path(target)
        if focus:
            with contextlib.suppress(Exception):
                desktop.focus_window("File Explorer")
        result.update({"path": str(target), "focus": focus})
        return result

    @mcp.tool()
    def file_explorer_search(
        query: str,
        root: str = "",
        limit: int = 20,
        open_first: bool = True,
    ) -> dict[str, Any]:
        """Search for files and folders, then optionally reveal the best match in File Explorer."""
        raw_query = query.strip()
        if not raw_query:
            raise ValueError("query cannot be empty")

        search_root = _resolve_path(root) if root.strip() else pathlib.Path.home()
        if not search_root.exists():
            raise FileNotFoundError(f"Search root not found: {search_root}")
        if not search_root.is_dir():
            raise NotADirectoryError(f"Not a directory: {search_root}")

        matches: list[dict[str, Any]] = []
        iterator = search_root.rglob("*")
        for path in sorted(iterator, key=lambda item: str(item).lower()):
            haystack = " ".join((path.name, str(path.relative_to(search_root))))
            score = _score_match(raw_query, haystack)
            if score <= 0:
                continue
            matches.append(
                {
                    **system._path_record(path),
                    "score": score,
                    "relative_path": str(path.relative_to(search_root)),
                }
            )
            if len(matches) >= max(limit, 0):
                break

        result: dict[str, Any] = {
            "root": str(search_root),
            "query": raw_query,
            "matches": matches,
        }

        if open_first and matches:
            first = pathlib.Path(matches[0]["path"])
            if first.exists():
                result["opened"] = _open_explorer_path(first)

        return result

    @mcp.tool()
    def run_workflow_actions(
        steps: list[dict[str, Any]],
        stop_on_error: bool = True,
    ) -> list[dict[str, Any]]:
        """Execute a chain of app workflow actions in order."""
        if not isinstance(steps, list):
            raise TypeError("steps must be a list of action objects")

        def _pick(step: dict[str, Any], *keys: str, default: Any = "") -> Any:
            for key in keys:
                value = step.get(key)
                if value not in (None, ""):
                    return value
            return default

        results: list[dict[str, Any]] = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                results.append({"index": index, "ok": False, "error": "Each step must be an object"})
                if stop_on_error:
                    break
                continue

            action = str(step.get("action", step.get("tool", "")) or "").strip().lower()
            if not action:
                results.append({"index": index, "ok": False, "error": "Step is missing an action name", "step": step})
                if stop_on_error:
                    break
                continue

            try:
                if action == "obsidian_list_vaults":
                    outcome = obsidian_list_vaults()
                elif action == "obsidian_open_vault":
                    outcome = obsidian_open_vault(
                        vault_path=str(_pick(step, "vault_path", "path", default="")).strip(),
                        focus=_as_bool(_pick(step, "focus", default=True)),
                    )
                elif action == "obsidian_search_notes":
                    outcome = obsidian_search_notes(
                        query=str(_pick(step, "query", "text", default="")),
                        vault_path=str(_pick(step, "vault_path", "path", default="")).strip(),
                        limit=_as_int(_pick(step, "limit", default=20), 20),
                    )
                elif action == "obsidian_search_note_contents":
                    outcome = obsidian_search_note_contents(
                        query=str(_pick(step, "query", "text", default="")),
                        vault_path=str(_pick(step, "vault_path", "path", default="")).strip(),
                        folder=str(_pick(step, "folder", default="")).strip(),
                        limit=_as_int(_pick(step, "limit", default=50), 50),
                        case_sensitive=_as_bool(_pick(step, "case_sensitive", default=False)),
                        regex=_as_bool(_pick(step, "regex", default=False)),
                    )
                elif action == "obsidian_open_note":
                    outcome = obsidian_open_note(
                        title_or_path=str(_pick(step, "title_or_path", "path", "title", default="")).strip(),
                        vault_path=str(_pick(step, "vault_path", "path", default="")).strip(),
                        folder=str(_pick(step, "folder", default="")).strip(),
                        create_if_missing=_as_bool(_pick(step, "create_if_missing", default=False)),
                        focus=_as_bool(_pick(step, "focus", default=True)),
                    )
                elif action == "obsidian_create_note":
                    outcome = obsidian_create_note(
                        title=str(_pick(step, "title", default="")).strip(),
                        body=str(_pick(step, "body", "text", default="")),
                        vault_path=str(_pick(step, "vault_path", "path", default="")).strip(),
                        folder=str(_pick(step, "folder", default="")).strip(),
                        open_in_obsidian=_as_bool(_pick(step, "open_in_obsidian", default=True)),
                    )
                elif action == "obsidian_append_to_note":
                    outcome = obsidian_append_to_note(
                        title_or_path=str(_pick(step, "title_or_path", "path", "title", default="")).strip(),
                        text=str(_pick(step, "text", "body", default="")),
                        vault_path=str(_pick(step, "vault_path", "path", default="")).strip(),
                        folder=str(_pick(step, "folder", default="")).strip(),
                        create_if_missing=_as_bool(_pick(step, "create_if_missing", default=False)),
                        open_in_obsidian=_as_bool(_pick(step, "open_in_obsidian", default=True)),
                    )
                elif action == "obsidian_replace_in_note":
                    outcome = obsidian_replace_in_note(
                        title_or_path=str(_pick(step, "title_or_path", "path", "title", default="")).strip(),
                        old=str(_pick(step, "old", "find", "before", default="")),
                        new=str(_pick(step, "new", "replacement", "after", default="")),
                        vault_path=str(_pick(step, "vault_path", "path", default="")).strip(),
                        folder=str(_pick(step, "folder", default="")).strip(),
                        create_if_missing=_as_bool(_pick(step, "create_if_missing", default=False)),
                        regex=_as_bool(_pick(step, "regex", default=False)),
                        case_sensitive=_as_bool(_pick(step, "case_sensitive", default=True)),
                        count=_as_int(_pick(step, "count", default=0), 0),
                    )
                elif action == "firefox_focus":
                    outcome = firefox_focus()
                elif action == "firefox_open_url":
                    outcome = firefox_open_url(
                        url=str(_pick(step, "url", "target", default="")).strip(),
                        focus=_as_bool(_pick(step, "focus", default=True)),
                        new_window=_as_bool(_pick(step, "new_window", default=False)),
                    )
                elif action == "firefox_search_web":
                    outcome = firefox_search_web(
                        query=str(_pick(step, "query", "target", default="")).strip(),
                        focus=_as_bool(_pick(step, "focus", default=True)),
                        new_window=_as_bool(_pick(step, "new_window", default=False)),
                    )
                elif action == "chrome_focus":
                    outcome = chrome_focus()
                elif action == "chrome_open_url":
                    outcome = chrome_open_url(
                        url=str(_pick(step, "url", "target", default="")).strip(),
                        focus=_as_bool(_pick(step, "focus", default=True)),
                        new_window=_as_bool(_pick(step, "new_window", default=False)),
                    )
                elif action == "chrome_search_web":
                    outcome = chrome_search_web(
                        query=str(_pick(step, "query", "target", default="")).strip(),
                        focus=_as_bool(_pick(step, "focus", default=True)),
                        new_window=_as_bool(_pick(step, "new_window", default=False)),
                    )
                elif action == "file_explorer_open":
                    outcome = file_explorer_open(
                        path=str(_pick(step, "path", "target", default="")).strip(),
                        focus=_as_bool(_pick(step, "focus", default=True)),
                    )
                elif action == "file_explorer_reveal":
                    outcome = file_explorer_reveal(
                        path=str(_pick(step, "path", "target", default="")).strip(),
                        focus=_as_bool(_pick(step, "focus", default=True)),
                    )
                elif action == "file_explorer_search":
                    outcome = file_explorer_search(
                        query=str(_pick(step, "query", "target", default="")).strip(),
                        root=str(_pick(step, "root", "path", default="")).strip(),
                        limit=_as_int(_pick(step, "limit", default=20), 20),
                        open_first=_as_bool(_pick(step, "open_first", default=True)),
                    )
                elif action == "wait":
                    seconds = max(_as_float(_pick(step, "seconds", "delay", default=0.0), 0.0), 0.0)
                    time.sleep(seconds)
                    outcome = {"slept": seconds}
                else:
                    raise ValueError(f"Unknown workflow action: {action!r}")

                results.append({"index": index, "action": action, "ok": True, "result": outcome})
            except Exception as exc:
                results.append({"index": index, "action": action, "ok": False, "error": str(exc)})
                if stop_on_error and not _as_bool(step.get("ignore_error", False)):
                    break

        return results
