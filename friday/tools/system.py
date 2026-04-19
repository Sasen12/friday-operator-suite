"""
System tools - time, environment info, shell commands, files, and processes.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import shlex
import subprocess
import time
import zipfile
from typing import Any


def _resolve_path(path: str) -> pathlib.Path:
    raw = path.strip()
    if not raw:
        raise ValueError("path cannot be empty")
    return pathlib.Path(raw).expanduser()


def _path_record(path: pathlib.Path) -> dict[str, Any]:
    exists = path.exists()
    record: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "exists": exists,
        "is_file": path.is_file() if exists else False,
        "is_dir": path.is_dir() if exists else False,
        "suffix": path.suffix,
    }
    if exists:
        stat = path.stat()
        record.update(
            {
                "size": stat.st_size,
                "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "created": datetime.datetime.fromtimestamp(stat.st_ctime).isoformat(),
            }
        )
    return record


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    if max_chars <= 20:
        return text[:max_chars], True
    return text[: max_chars - 20] + "\n...[truncated]", True


def _parse_comma_separated_values(raw: str) -> set[str]:
    values: set[str] = set()
    for item in raw.split(","):
        text = item.strip().lower()
        if text:
            values.add(text if text.startswith(".") else f".{text}")
    return values


def _is_probably_binary(path: pathlib.Path, max_bytes: int) -> bool:
    try:
        if path.stat().st_size > max_bytes:
            return True
    except OSError:
        return True
    return False


def _powershell_executable() -> str:
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


_DESTRUCTIVE_POWERSHELL_PATTERNS = (
    r"\bremove-item\b",
    r"\bstop-process\b",
    r"\brestart-computer\b",
    r"\bstop-computer\b",
    r"\bshutdown-computer\b",
    r"\bclear-disk\b",
    r"\bformat-volume\b",
    r"\bdiskpart\b",
    r"\bremove-psdrive\b",
)

_DESTRUCTIVE_COMMAND_PATTERNS = (
    r"(^|[\s;&|])del\b",
    r"(^|[\s;&|])erase\b",
    r"(^|[\s;&|])rmdir\b",
    r"(^|[\s;&|])rd\b",
    r"(^|[\s;&|])taskkill\b",
    r"(^|[\s;&|])shutdown\b",
    r"(^|[\s;&|])format\b",
    r"(^|[\s;&|])diskpart\b",
)


def _requires_confirmation(text: str, patterns: tuple[str, ...]) -> bool:
    normalized = text.strip().lower()
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)


def _run_powershell(script: str, cwd: str = "", timeout_seconds: int = 120) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    working_directory = str(_resolve_path(cwd)) if cwd.strip() else None
    return subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        cwd=working_directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )


def _json_records(stdout: str) -> list[dict[str, Any]]:
    text = stdout.strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []

    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _open_path(path: pathlib.Path) -> None:
    if hasattr(os, "startfile"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return

    if platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)], shell=False)
        return

    opener = shutil.which("xdg-open")
    if opener:
        subprocess.Popen([opener, str(path)], shell=False)
        return

    raise RuntimeError(f"No supported opener found for {path}")


def _reveal_path(path: pathlib.Path) -> None:
    if path.is_file():
        explorer = pathlib.Path(os.getenv("WINDIR", r"C:\Windows")) / "explorer.exe"
        subprocess.Popen([str(explorer), f"/select,{str(path)}"], shell=False)
        return

    _open_path(path)


def _copy_path(source: pathlib.Path, destination: pathlib.Path, overwrite: bool = False) -> None:
    if destination.exists():
        if not overwrite:
            raise FileExistsError(f"Destination already exists: {destination}")
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()

    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, destination, dirs_exist_ok=overwrite)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _move_path(source: pathlib.Path, destination: pathlib.Path, overwrite: bool = False) -> None:
    if destination.exists():
        if not overwrite:
            raise FileExistsError(f"Destination already exists: {destination}")
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))


def _start_process(
    command: str,
    arguments: str = "",
    cwd: str = "",
    hidden: bool = False,
) -> subprocess.Popen[Any]:
    args = shlex.split(arguments, posix=False) if arguments.strip() else []
    working_directory = str(_resolve_path(cwd)) if cwd.strip() else None
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if hidden:
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

    startupinfo = None
    if hidden and hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)

    return subprocess.Popen(
        [command, *args],
        cwd=working_directory,
        shell=False,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )


def register(mcp):

    @mcp.tool()
    def get_current_time() -> str:
        """Return the current date and time in ISO 8601 format."""
        return datetime.datetime.now().isoformat()

    @mcp.tool()
    def get_system_info() -> dict:
        """Return basic information about the host system."""
        return {
            "os": platform.system(),
            "os_version": platform.version(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        }

    @mcp.tool()
    def run_command(
        command: str,
        arguments: str = "",
        cwd: str = "",
        timeout_seconds: int = 120,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """
        Run a native executable or script command and capture its output.
        """
        command = command.strip()
        if not command:
            raise ValueError("command cannot be empty")

        args = shlex.split(arguments, posix=False) if arguments.strip() else []
        command_text = f"{command} {arguments}".strip()
        if not confirm and _requires_confirmation(command_text, _DESTRUCTIVE_COMMAND_PATTERNS):
            raise ValueError("Refusing to run a potentially destructive command without confirm=True")
        working_directory = str(_resolve_path(cwd)) if cwd.strip() else None

        try:
            result = subprocess.run(
                [command, *args],
                cwd=working_directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Command not found: {command}") from exc

        stdout, stdout_truncated = _truncate_text(result.stdout or "", 12000)
        stderr, stderr_truncated = _truncate_text(result.stderr or "", 12000)
        return {
            "command": command,
            "arguments": args,
            "cwd": working_directory,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "confirm": confirm,
        }

    @mcp.tool()
    def run_powershell(
        script: str,
        cwd: str = "",
        timeout_seconds: int = 120,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """
        Run a PowerShell script and capture stdout/stderr.
        """
        script = script.strip()
        if not script:
            raise ValueError("script cannot be empty")
        if not confirm and _requires_confirmation(script, _DESTRUCTIVE_POWERSHELL_PATTERNS):
            raise ValueError("Refusing to run a potentially destructive PowerShell command without confirm=True")

        result = _run_powershell(script, cwd=cwd, timeout_seconds=timeout_seconds)
        stdout, stdout_truncated = _truncate_text(result.stdout or "", 12000)
        stderr, stderr_truncated = _truncate_text(result.stderr or "", 12000)
        return {
            "command": "powershell",
            "cwd": str(_resolve_path(cwd)) if cwd.strip() else None,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "confirm": confirm,
        }

    @mcp.tool()
    def read_file(path: str, encoding: str = "utf-8", max_chars: int = 50000) -> dict[str, Any]:
        """Read a text file from disk."""
        file_path = _resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if not file_path.is_file():
            raise IsADirectoryError(f"Not a file: {file_path}")

        text = file_path.read_text(encoding=encoding, errors="replace")
        text, truncated = _truncate_text(text, max_chars)
        return {
            "path": str(file_path),
            "encoding": encoding,
            "text": text,
            "truncated": truncated,
            "metadata": _path_record(file_path),
        }

    @mcp.tool()
    def write_file(
        path: str,
        content: str,
        encoding: str = "utf-8",
        append: bool = False,
    ) -> dict[str, Any]:
        """Write text to a file, creating parent folders if needed."""
        file_path = _resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with file_path.open(mode, encoding=encoding, errors="replace") as handle:
            handle.write(content)
        return {
            "path": str(file_path),
            "bytes_written": len(content.encode(encoding, errors="replace")),
            "append": append,
            "metadata": _path_record(file_path),
        }

    @mcp.tool()
    def list_directory(path: str = ".", recursive: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        """List files and folders in a directory."""
        base = _resolve_path(path)
        if not base.exists():
            raise FileNotFoundError(f"Directory not found: {base}")
        if not base.is_dir():
            raise NotADirectoryError(f"Not a directory: {base}")

        entries: list[dict[str, Any]] = []
        iterator = base.rglob("*") if recursive else base.iterdir()
        for child in sorted(iterator, key=lambda item: str(item).lower()):
            entries.append(_path_record(child))
            if len(entries) >= max(limit, 0):
                break
        return entries

    @mcp.tool()
    def search_files(root: str = ".", query: str = "", recursive: bool = True, limit: int = 200) -> list[dict[str, Any]]:
        """Search file and folder names below a root directory."""
        base = _resolve_path(root)
        if not base.exists():
            raise FileNotFoundError(f"Root not found: {base}")
        if not base.is_dir():
            raise NotADirectoryError(f"Not a directory: {base}")

        normalized_query = query.strip().lower()
        matches: list[dict[str, Any]] = []
        iterator = base.rglob("*") if recursive else base.iterdir()
        for child in sorted(iterator, key=lambda item: str(item).lower()):
            haystack = f"{child.name} {child}".lower()
            if normalized_query and normalized_query not in haystack:
                continue
            matches.append(_path_record(child))
            if len(matches) >= max(limit, 0):
                break
        return matches

    @mcp.tool()
    def search_file_contents(
        root: str = ".",
        query: str = "",
        recursive: bool = True,
        limit: int = 100,
        case_sensitive: bool = False,
        regex: bool = False,
        extensions: str = "",
        max_file_size: int = 2_000_000,
    ) -> list[dict[str, Any]]:
        """Search text inside files below a root directory."""
        base = _resolve_path(root)
        if not base.exists():
            raise FileNotFoundError(f"Root not found: {base}")
        if not base.is_dir():
            raise NotADirectoryError(f"Not a directory: {base}")

        if not query.strip():
            return []

        allowed_extensions = _parse_comma_separated_values(extensions)
        compiled = None
        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            compiled = re.compile(query, flags=flags)
        elif not case_sensitive:
            query = query.lower()

        matches: list[dict[str, Any]] = []
        iterator = base.rglob("*") if recursive else base.iterdir()
        for child in sorted(iterator, key=lambda item: str(item).lower()):
            if not child.is_file():
                continue
            if allowed_extensions and child.suffix.lower() not in allowed_extensions:
                continue
            if _is_probably_binary(child, max_file_size):
                continue

            try:
                text = child.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive or regex else line.lower()
                if compiled is not None:
                    if compiled.search(line) is None:
                        continue
                    match_text = compiled.search(line).group(0) if compiled.search(line) else ""
                else:
                    if query not in haystack:
                        continue
                    match_text = query

                matches.append(
                    {
                        "path": str(child),
                        "line_number": line_number,
                        "line": line,
                        "match": match_text,
                        "metadata": _path_record(child),
                    }
                )
                if len(matches) >= max(limit, 0):
                    return matches
        return matches

    @mcp.tool()
    def replace_in_file(
        path: str,
        old: str,
        new: str,
        count: int = 0,
        regex: bool = False,
        case_sensitive: bool = True,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Replace text inside a file and write the changes back."""
        file_path = _resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if not file_path.is_file():
            raise IsADirectoryError(f"Not a file: {file_path}")
        if not old:
            raise ValueError("old cannot be empty")

        original = file_path.read_text(encoding=encoding, errors="replace")
        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern = re.compile(old, flags=flags)
            new_text, replacements = pattern.subn(new, original, count=count if count > 0 else 0)
        else:
            if case_sensitive:
                if count > 0:
                    replacements = min(original.count(old), count)
                    new_text = original.replace(old, new, count)
                else:
                    replacements = original.count(old)
                    new_text = original.replace(old, new)
            else:
                pattern = re.compile(re.escape(old), flags=re.IGNORECASE)
                new_text, replacements = pattern.subn(new, original, count=count if count > 0 else 0)

        if replacements <= 0:
            return {
                "path": str(file_path),
                "replacements": 0,
                "changed": False,
                "metadata": _path_record(file_path),
            }

        file_path.write_text(new_text, encoding=encoding, errors="replace")
        return {
            "path": str(file_path),
            "replacements": replacements,
            "changed": True,
            "metadata": _path_record(file_path),
        }

    @mcp.tool()
    def read_json(path: str, encoding: str = "utf-8") -> dict[str, Any]:
        """Read a JSON file from disk."""
        file_path = _resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if not file_path.is_file():
            raise IsADirectoryError(f"Not a file: {file_path}")

        text = file_path.read_text(encoding=encoding, errors="replace")
        data = json.loads(text)
        return {
            "path": str(file_path),
            "encoding": encoding,
            "data": data,
            "metadata": _path_record(file_path),
        }

    @mcp.tool()
    def write_json(
        path: str,
        data: Any,
        encoding: str = "utf-8",
        indent: int = 2,
        sort_keys: bool = True,
        ensure_ascii: bool = False,
    ) -> dict[str, Any]:
        """Write JSON data to a file, creating parent folders if needed."""
        file_path = _resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii, default=str)
        file_path.write_text(content + "\n", encoding=encoding, errors="replace")
        return {
            "path": str(file_path),
            "encoding": encoding,
            "bytes_written": len((content + "\n").encode(encoding, errors="replace")),
            "metadata": _path_record(file_path),
        }

    @mcp.tool()
    def hash_file(path: str, algorithm: str = "sha256", chunk_size: int = 1_048_576) -> dict[str, Any]:
        """Compute a content hash for a file."""
        file_path = _resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if not file_path.is_file():
            raise IsADirectoryError(f"Not a file: {file_path}")
        try:
            hasher = hashlib.new(algorithm)
        except ValueError as exc:
            raise ValueError(f"Unsupported hash algorithm: {algorithm}") from exc

        total = 0
        with file_path.open("rb") as handle:
            while True:
                chunk = handle.read(max(chunk_size, 1))
                if not chunk:
                    break
                hasher.update(chunk)
                total += len(chunk)
        return {
            "path": str(file_path),
            "algorithm": algorithm,
            "digest": hasher.hexdigest(),
            "bytes_read": total,
            "metadata": _path_record(file_path),
        }

    @mcp.tool()
    def zip_path(source: str, destination: str = "", confirm: bool = False) -> dict[str, Any]:
        """Create a ZIP archive from a file or folder."""
        src = _resolve_path(source)
        if not src.exists():
            raise FileNotFoundError(f"Source not found: {src}")

        if destination.strip():
            dst = _resolve_path(destination)
        else:
            suffix = ".zip"
            if src.is_dir():
                dst = src.parent / f"{src.name}{suffix}"
            else:
                dst = src.with_suffix(suffix)

        if dst.exists() and not confirm:
            raise ValueError("Refusing to overwrite an existing archive without confirm=True")
        dst.parent.mkdir(parents=True, exist_ok=True)

        entries = 0
        with zipfile.ZipFile(dst, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            if src.is_dir() and not src.is_symlink():
                for child in src.rglob("*"):
                    arcname = child.relative_to(src)
                    archive.write(child, arcname=str(arcname))
                    entries += 1
            else:
                archive.write(src, arcname=src.name)
                entries = 1

        return {
            "source": str(src),
            "destination": str(dst),
            "entries": entries,
            "confirm": confirm,
            "metadata": _path_record(dst),
        }

    @mcp.tool()
    def unzip_path(source: str, destination: str = "", confirm: bool = False) -> dict[str, Any]:
        """Extract a ZIP archive to a folder."""
        src = _resolve_path(source)
        if not src.exists():
            raise FileNotFoundError(f"Source not found: {src}")
        if not src.is_file():
            raise IsADirectoryError(f"Not a file: {src}")
        if destination.strip():
            dst = _resolve_path(destination)
        else:
            dst = src.parent / src.stem

        if dst.exists() and not confirm:
            raise ValueError("Refusing to overwrite an existing destination without confirm=True")
        dst.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(src, mode="r") as archive:
            members = archive.namelist()
            archive.extractall(dst)

        return {
            "source": str(src),
            "destination": str(dst),
            "members": len(members),
            "confirm": confirm,
            "metadata": _path_record(dst),
        }

    @mcp.tool()
    def make_directory(path: str) -> dict[str, Any]:
        """Create a directory and any missing parents."""
        directory = _resolve_path(path)
        directory.mkdir(parents=True, exist_ok=True)
        return _path_record(directory)

    @mcp.tool()
    def copy_path(source: str, destination: str, overwrite: bool = False, confirm: bool = False) -> dict[str, Any]:
        """Copy a file or folder. Overwriting requires confirm=True."""
        src = _resolve_path(source)
        dst = _resolve_path(destination)
        if not src.exists():
            raise FileNotFoundError(f"Source not found: {src}")
        if overwrite and dst.exists() and not confirm:
            raise ValueError("Refusing to overwrite an existing destination without confirm=True")
        _copy_path(src, dst, overwrite=overwrite)
        return {
            "source": str(src),
            "destination": str(dst),
            "overwrite": overwrite,
            "confirm": confirm,
            "destination_metadata": _path_record(dst),
        }

    @mcp.tool()
    def move_path(source: str, destination: str, overwrite: bool = False, confirm: bool = False) -> dict[str, Any]:
        """Move or rename a file or folder. Overwriting requires confirm=True."""
        src = _resolve_path(source)
        dst = _resolve_path(destination)
        if not src.exists():
            raise FileNotFoundError(f"Source not found: {src}")
        if overwrite and dst.exists() and not confirm:
            raise ValueError("Refusing to overwrite an existing destination without confirm=True")
        _move_path(src, dst, overwrite=overwrite)
        return {
            "source": str(src),
            "destination": str(dst),
            "overwrite": overwrite,
            "confirm": confirm,
            "destination_metadata": _path_record(dst),
        }

    @mcp.tool()
    def delete_path(path: str, recursive: bool = False, confirm: bool = False) -> dict[str, Any]:
        """Delete a file or folder. Set confirm=True when you are sure."""
        target = _resolve_path(path)
        if not confirm:
            raise ValueError("Refusing to delete without confirm=True")
        if not target.exists():
            raise FileNotFoundError(f"Path not found: {target}")

        if target.is_dir() and not target.is_symlink():
            if recursive:
                shutil.rmtree(target)
            else:
                target.rmdir()
        else:
            target.unlink()
        return {"deleted": str(target), "recursive": recursive}

    @mcp.tool()
    def open_path(path: str) -> dict[str, Any]:
        """Open a file or folder with the system default application."""
        target = _resolve_path(path)
        if not target.exists():
            raise FileNotFoundError(f"Path not found: {target}")
        _open_path(target)
        return {"opened": str(target)}

    @mcp.tool()
    def reveal_path(path: str) -> dict[str, Any]:
        """Reveal a file in File Explorer or open a folder directly."""
        target = _resolve_path(path)
        if not target.exists():
            raise FileNotFoundError(f"Path not found: {target}")
        _reveal_path(target)
        return {"revealed": str(target)}

    @mcp.tool()
    def start_process(
        command: str,
        arguments: str = "",
        cwd: str = "",
        hidden: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Start a process in the background and return its PID."""
        command = command.strip()
        if not command:
            raise ValueError("command cannot be empty")
        command_text = f"{command} {arguments}".strip()
        if not confirm and _requires_confirmation(command_text, _DESTRUCTIVE_COMMAND_PATTERNS):
            raise ValueError("Refusing to start a potentially destructive command without confirm=True")

        process = _start_process(command, arguments=arguments, cwd=cwd, hidden=hidden)
        return {
            "command": command,
            "arguments": shlex.split(arguments, posix=False) if arguments.strip() else [],
            "cwd": str(_resolve_path(cwd)) if cwd.strip() else None,
            "hidden": hidden,
            "pid": process.pid,
            "confirm": confirm,
        }

    @mcp.tool()
    def list_processes(limit: int = 200) -> list[dict[str, Any]]:
        """List running processes on the host."""
        script = (
            "$ErrorActionPreference = 'SilentlyContinue'; "
            f"Get-Process | Select-Object -First {max(limit, 0)} Id,ProcessName,Path,StartTime | ConvertTo-Json -Depth 3"
        )
        result = _run_powershell(script, timeout_seconds=30)
        records = _json_records(result.stdout)
        processes: list[dict[str, Any]] = []
        for item in records:
            processes.append(
                {
                    "pid": item.get("Id"),
                    "name": item.get("ProcessName", ""),
                    "path": item.get("Path", ""),
                    "start_time": item.get("StartTime", ""),
                }
            )
        return processes

    @mcp.tool()
    def search_processes(query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search running processes by name or executable path."""
        normalized_query = query.strip().lower()
        if not normalized_query:
            return []

        processes = list_processes(limit=1000)
        matches: list[dict[str, Any]] = []
        for item in processes:
            haystack = f"{item.get('name', '')} {item.get('path', '')}".lower()
            if normalized_query not in haystack:
                continue
            matches.append(item)
            if len(matches) >= max(limit, 0):
                break
        return matches

    @mcp.tool()
    def run_system_actions(
        steps: list[dict[str, Any]],
        stop_on_error: bool = True,
    ) -> list[dict[str, Any]]:
        """Execute a chain of file, shell, and process actions in order."""
        if not isinstance(steps, list):
            raise TypeError("steps must be a list of action objects")

        def _pick(step: dict[str, Any], *keys: str, default: Any = "") -> Any:
            for key in keys:
                value = step.get(key)
                if value not in (None, ""):
                    return value
            return default

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
                if action == "run_command":
                    outcome = run_command(
                        command=str(_pick(step, "command", "path", "target", default="")).strip(),
                        arguments=str(_pick(step, "arguments", "args", default="")),
                        cwd=str(_pick(step, "cwd", "workdir", default="")),
                        timeout_seconds=_as_int(_pick(step, "timeout_seconds", "timeout", default=120), 120),
                        confirm=_as_bool(_pick(step, "confirm", default=False)),
                    )
                elif action == "run_powershell":
                    outcome = run_powershell(
                        script=str(_pick(step, "script", "command", "arguments", "value", default="")).strip(),
                        cwd=str(_pick(step, "cwd", "workdir", default="")),
                        timeout_seconds=_as_int(_pick(step, "timeout_seconds", "timeout", default=120), 120),
                        confirm=_as_bool(_pick(step, "confirm", default=False)),
                    )
                elif action == "read_file":
                    outcome = read_file(
                        path=str(_pick(step, "path", "file", "target", default="")).strip(),
                        encoding=str(_pick(step, "encoding", default="utf-8")),
                        max_chars=_as_int(_pick(step, "max_chars", default=50000), 50000),
                    )
                elif action == "write_file":
                    outcome = write_file(
                        path=str(_pick(step, "path", "file", "target", default="")).strip(),
                        content=str(_pick(step, "content", "text", "value", default="")),
                        encoding=str(_pick(step, "encoding", default="utf-8")),
                        append=_as_bool(_pick(step, "append", default=False)),
                    )
                elif action == "list_directory":
                    outcome = list_directory(
                        path=str(_pick(step, "path", "root", "target", default=".")).strip() or ".",
                        recursive=_as_bool(_pick(step, "recursive", default=False)),
                        limit=_as_int(_pick(step, "limit", default=500), 500),
                    )
                elif action == "search_files":
                    outcome = search_files(
                        root=str(_pick(step, "root", "path", "target", default=".")).strip() or ".",
                        query=str(_pick(step, "query", "text", "needle", default="")),
                        recursive=_as_bool(_pick(step, "recursive", default=True)),
                        limit=_as_int(_pick(step, "limit", default=200), 200),
                    )
                elif action == "search_file_contents":
                    outcome = search_file_contents(
                        root=str(_pick(step, "root", "path", "target", default=".")).strip() or ".",
                        query=str(_pick(step, "query", "text", "needle", default="")),
                        recursive=_as_bool(_pick(step, "recursive", default=True)),
                        limit=_as_int(_pick(step, "limit", default=100), 100),
                        case_sensitive=_as_bool(_pick(step, "case_sensitive", default=False)),
                        regex=_as_bool(_pick(step, "regex", default=False)),
                        extensions=str(_pick(step, "extensions", default="")),
                        max_file_size=_as_int(_pick(step, "max_file_size", default=2_000_000), 2_000_000),
                    )
                elif action == "replace_in_file":
                    outcome = replace_in_file(
                        path=str(_pick(step, "path", "file", "target", default="")).strip(),
                        old=str(_pick(step, "old", "find", "before", default="")),
                        new=str(_pick(step, "new", "replacement", "after", default="")),
                        count=_as_int(_pick(step, "count", default=0), 0),
                        regex=_as_bool(_pick(step, "regex", default=False)),
                        case_sensitive=_as_bool(_pick(step, "case_sensitive", default=True)),
                        encoding=str(_pick(step, "encoding", default="utf-8")),
                    )
                elif action == "read_json":
                    outcome = read_json(
                        path=str(_pick(step, "path", "file", "target", default="")).strip(),
                        encoding=str(_pick(step, "encoding", default="utf-8")),
                    )
                elif action == "write_json":
                    outcome = write_json(
                        path=str(_pick(step, "path", "file", "target", default="")).strip(),
                        data=_pick(step, "data", "value", default={}),
                        encoding=str(_pick(step, "encoding", default="utf-8")),
                        indent=_as_int(_pick(step, "indent", default=2), 2),
                        sort_keys=_as_bool(_pick(step, "sort_keys", default=True)),
                        ensure_ascii=_as_bool(_pick(step, "ensure_ascii", default=False)),
                    )
                elif action == "hash_file":
                    outcome = hash_file(
                        path=str(_pick(step, "path", "file", "target", default="")).strip(),
                        algorithm=str(_pick(step, "algorithm", default="sha256")).strip(),
                        chunk_size=_as_int(_pick(step, "chunk_size", default=1_048_576), 1_048_576),
                    )
                elif action == "zip_path":
                    outcome = zip_path(
                        source=str(_pick(step, "source", "path", "target", default="")).strip(),
                        destination=str(_pick(step, "destination", "to", "output", default="")).strip(),
                        confirm=_as_bool(_pick(step, "confirm", default=False)),
                    )
                elif action == "unzip_path":
                    outcome = unzip_path(
                        source=str(_pick(step, "source", "path", "target", default="")).strip(),
                        destination=str(_pick(step, "destination", "to", "output", default="")).strip(),
                        confirm=_as_bool(_pick(step, "confirm", default=False)),
                    )
                elif action == "make_directory":
                    outcome = make_directory(path=str(_pick(step, "path", "target", default="")).strip())
                elif action == "copy_path":
                    outcome = copy_path(
                        source=str(_pick(step, "source", "from", "path", default="")).strip(),
                        destination=str(_pick(step, "destination", "to", "target", default="")).strip(),
                        overwrite=_as_bool(_pick(step, "overwrite", default=False)),
                        confirm=_as_bool(_pick(step, "confirm", default=False)),
                    )
                elif action == "move_path":
                    outcome = move_path(
                        source=str(_pick(step, "source", "from", "path", default="")).strip(),
                        destination=str(_pick(step, "destination", "to", "target", default="")).strip(),
                        overwrite=_as_bool(_pick(step, "overwrite", default=False)),
                        confirm=_as_bool(_pick(step, "confirm", default=False)),
                    )
                elif action == "delete_path":
                    outcome = delete_path(
                        path=str(_pick(step, "path", "target", default="")).strip(),
                        recursive=_as_bool(_pick(step, "recursive", default=False)),
                        confirm=_as_bool(_pick(step, "confirm", default=False)),
                    )
                elif action == "open_path":
                    outcome = open_path(path=str(_pick(step, "path", "target", default="")).strip())
                elif action == "reveal_path":
                    outcome = reveal_path(path=str(_pick(step, "path", "target", default="")).strip())
                elif action == "start_process":
                    outcome = start_process(
                        command=str(_pick(step, "command", "path", "target", default="")).strip(),
                        arguments=str(_pick(step, "arguments", "args", default="")),
                        cwd=str(_pick(step, "cwd", "workdir", default="")),
                        hidden=_as_bool(_pick(step, "hidden", default=False)),
                        confirm=_as_bool(_pick(step, "confirm", default=False)),
                    )
                elif action == "list_processes":
                    outcome = list_processes(limit=_as_int(_pick(step, "limit", default=200), 200))
                elif action == "search_processes":
                    outcome = search_processes(
                        query=str(_pick(step, "query", "text", "needle", default="")),
                        limit=_as_int(_pick(step, "limit", default=50), 50),
                    )
                elif action == "kill_process":
                    outcome = kill_process(
                        pid=_as_int(_pick(step, "pid", "id", default=0), 0),
                        force=_as_bool(_pick(step, "force", default=False)),
                        confirm=_as_bool(_pick(step, "confirm", default=False)),
                    )
                elif action == "wait":
                    seconds = max(_as_float(_pick(step, "seconds", "delay", default=0.0), 0.0), 0.0)
                    time.sleep(seconds)
                    outcome = {"slept": seconds}
                else:
                    raise ValueError(f"Unknown system action: {action!r}")

                results.append({"index": index, "action": action, "ok": True, "result": outcome})
            except Exception as exc:
                results.append({"index": index, "action": action, "ok": False, "error": str(exc)})
                if stop_on_error and not _as_bool(step.get("ignore_error", False)):
                    break

        return results

    @mcp.tool()
    def kill_process(pid: int, force: bool = False, confirm: bool = False) -> dict[str, Any]:
        """Stop a running process by PID."""
        if pid <= 0:
            raise ValueError("pid must be positive")
        if not confirm:
            raise ValueError("Refusing to kill a process without confirm=True")

        script = f"Stop-Process -Id {int(pid)}" + (" -Force" if force else "")
        result = _run_powershell(script, timeout_seconds=30)
        return {
            "pid": int(pid),
            "force": force,
            "confirm": confirm,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
