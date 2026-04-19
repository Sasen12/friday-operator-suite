"""
Desktop tools - open apps, control the mouse and keyboard, inspect windows, and grab screenshots.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import io
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import webbrowser
from difflib import SequenceMatcher
from typing import Any

try:
    from PIL import ImageGrab
    import win32clipboard
    import win32con
    import win32gui
    import win32process
    from pywinauto import Desktop
    from pywinauto import keyboard
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from winrtocr import WinRTOCR

    _DESKTOP_IMPORT_ERROR = None
except Exception as exc:
    ImageGrab = None
    win32clipboard = None
    win32con = None
    win32gui = None
    win32process = None
    Desktop = None
    keyboard = None
    WinRTOCR = None
    _DESKTOP_IMPORT_ERROR = exc


_APP_INDEX_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_APP_INDEX_TTL_SECONDS = 300.0


def _require_desktop_stack() -> None:
    if _DESKTOP_IMPORT_ERROR is not None:
        raise RuntimeError(
            "Desktop control tools are unavailable in the current environment."
        ) from _DESKTOP_IMPORT_ERROR


def _to_int(value: Any) -> int:
    return int(value)


def _rect_to_dict(rect: Any) -> dict[str, int]:
    return {
        "left": _to_int(getattr(rect, "left", 0)),
        "top": _to_int(getattr(rect, "top", 0)),
        "right": _to_int(getattr(rect, "right", 0)),
        "bottom": _to_int(getattr(rect, "bottom", 0)),
    }


def _escape_send_keys_text(text: str) -> str:
    """Escape text for WScript/SendKeys style input."""
    replacements = {
        "+": "{+}",
        "^": "{^}",
        "%": "{%}",
        "~": "{~}",
        "{": "{{}",
        "}": "{}}",
        "(": "{(}",
        ")": "{)}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def _format_sendkeys_key(key: str) -> str:
    normalized = key.strip().lower()
    special_keys = {
        "backspace": "{BACKSPACE}",
        "bksp": "{BACKSPACE}",
        "del": "{DEL}",
        "delete": "{DEL}",
        "down": "{DOWN}",
        "end": "{END}",
        "enter": "{ENTER}",
        "esc": "{ESC}",
        "escape": "{ESC}",
        "home": "{HOME}",
        "insert": "{INS}",
        "left": "{LEFT}",
        "pagedown": "{PGDN}",
        "pageup": "{PGUP}",
        "prtsc": "{PRTSC}",
        "printscreen": "{PRTSC}",
        "return": "{ENTER}",
        "right": "{RIGHT}",
        "space": "{SPACE}",
        "spacebar": "{SPACE}",
        "tab": "{TAB}",
        "up": "{UP}",
    }
    if normalized in special_keys:
        return special_keys[normalized]

    if re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", normalized):
        return f"{{{normalized.upper()}}}"

    if len(normalized) == 1:
        return _escape_send_keys_text(normalized)

    return _escape_send_keys_text(key)


def _set_clipboard_text(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        with contextlib.suppress(Exception):
            win32clipboard.CloseClipboard()


def _get_clipboard_text() -> str | None:
    try:
        win32clipboard.OpenClipboard()
    except Exception:
        return None

    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    finally:
        with contextlib.suppress(Exception):
            win32clipboard.CloseClipboard()
    return None


def _grab_desktop_image():
    """Capture the desktop image, preferring all monitors when available."""
    try:
        return ImageGrab.grab(all_screens=True)
    except TypeError:
        return ImageGrab.grab()


def _capture_desktop_screenshot(path: str = "") -> pathlib.Path:
    image = _grab_desktop_image()
    if path.strip():
        output_path = pathlib.Path(path).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = pathlib.Path(tempfile.gettempdir()) / "friday_screenshots"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = output_dir / f"screen-{timestamp}.png"

    image.save(output_path)
    return output_path


def _ocr_engine():
    _require_desktop_stack()
    if WinRTOCR is None:
        raise RuntimeError("OCR is unavailable in the current environment.") from _DESKTOP_IMPORT_ERROR
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return WinRTOCR()


def _ocr_lookup(item: Any, *names: str) -> Any:
    if isinstance(item, dict):
        for name in names:
            value = item.get(name)
            if value not in (None, ""):
                return value

    for name in names:
        value = getattr(item, name, None)
        if value not in (None, ""):
            return value

    return None


def _ocr_records(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []

    if isinstance(result, str):
        text = result.strip()
        return [{"text": text}] if text else []

    if isinstance(result, (list, tuple)):
        if len(result) >= 1 and isinstance(result[0], str):
            record: dict[str, Any] = {"text": result[0].strip()}
            if len(result) > 1:
                record["bounding_box"] = result[1]
            if len(result) > 2:
                record["confidence"] = result[2]
            return [record] if record["text"] or len(record) > 1 else []

        records: list[dict[str, Any]] = []
        for item in result:
            records.extend(_ocr_records(item))
        return records

    if isinstance(result, dict):
        nested = _ocr_lookup(result, "lines", "items", "output_lines", "words")
        if nested is not None:
            records: list[dict[str, Any]] = []
            for item in nested or []:
                records.extend(_ocr_records(item))
            return records

        text = _ocr_lookup(result, "text", "content", "value", "line_text", "word")
        record: dict[str, Any] = {"text": str(text).strip() if text is not None else ""}
        for key in ("confidence", "bounding_box", "bbox", "box", "rect"):
            value = result.get(key)
            if value is not None:
                record[key] = value
        return [record] if record["text"] or len(record) > 1 else []

    nested = _ocr_lookup(result, "lines", "items", "output_lines", "words")
    if nested is not None:
        records: list[dict[str, Any]] = []
        for item in nested or []:
            records.extend(_ocr_records(item))
        return records

    text = _ocr_lookup(result, "text", "content", "value", "line_text", "word")
    if text is None:
        text = str(result)

    record = {"text": str(text).strip()}
    for key in ("confidence", "bounding_box", "bbox", "box", "rect"):
        value = _ocr_lookup(result, key)
        if value is not None:
            record[key] = value
    return [record] if record["text"] or len(record) > 1 else []


def _ocr_summary(result: Any) -> dict[str, Any]:
    records = _ocr_records(result)
    text = "\n".join(record["text"] for record in records if record.get("text"))
    return {"text": text, "lines": records}


async def _run_ocr(engine: Any, image_path: str, lang: str, detail_level: str) -> Any:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return await engine.ocr(image_path, lang=lang, detail_level=detail_level)


async def _ocr_image_impl(path: str, lang: str, detail_level: str) -> dict[str, Any]:
    image_path = pathlib.Path(path).expanduser()
    if not image_path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    engine = _ocr_engine()
    result = await _run_ocr(engine, str(image_path), lang, detail_level)
    summary = _ocr_summary(result)
    summary.update(
        {
            "path": str(image_path),
            "language": lang,
            "detail_level": detail_level,
        }
    )
    return summary


def _send_keys(keys: str) -> None:
    keyboard.send_keys(keys, pause=0.01)


def _paste_text(text: str, restore_clipboard: bool = True) -> None:
    previous = _get_clipboard_text() if restore_clipboard else None
    _set_clipboard_text(text)
    _send_keys("^v")
    time.sleep(0.1)
    if restore_clipboard and previous is not None:
        _set_clipboard_text(previous)


def _current_foreground_hwnd() -> int:
    return int(win32gui.GetForegroundWindow())


def _window_info(hwnd: int) -> dict[str, Any]:
    title = win32gui.GetWindowText(hwnd)
    class_name = win32gui.GetClassName(hwnd)
    rect = _rect_to_dict(win32gui.GetWindowRect(hwnd))
    thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
    return {
        "handle": hwnd,
        "title": title,
        "class_name": class_name,
        "rect": rect,
        "thread_id": int(thread_id),
        "process_id": int(process_id),
        "visible": bool(win32gui.IsWindowVisible(hwnd)),
        "enabled": bool(win32gui.IsWindowEnabled(hwnd)),
        "minimized": bool(win32gui.IsIconic(hwnd)),
    }


def _visible_windows() -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []

    def _callback(hwnd: int, _: int) -> None:
        title = win32gui.GetWindowText(hwnd)
        if not title or not win32gui.IsWindowVisible(hwnd):
            return
        windows.append(_window_info(hwnd))

    win32gui.EnumWindows(_callback, 0)
    return windows


def _find_windows(query: str) -> list[dict[str, Any]]:
    query_lower = query.lower().strip()
    if not query_lower:
        return []

    scored = []
    for info in _visible_windows():
        score = _window_score(query_lower, info)
        if score > 0:
            scored.append((score, info))

    scored.sort(key=lambda item: (-item[0], item[1].get("title", "").lower()))
    return [info for _, info in scored]


def _foreground_app_window() -> Any:
    hwnd = _current_foreground_hwnd()
    if hwnd == 0:
        raise RuntimeError("No foreground window is active.")

    return Desktop(backend="uia").window(handle=hwnd)


def _window_score(query: str, info: dict[str, Any]) -> int:
    normalized_query = _normalize_app_query(query)
    if not normalized_query:
        return 0

    query_tokens = _tokenize(normalized_query)
    candidate_values = [
        info.get("title", ""),
        info.get("class_name", ""),
        str(info.get("process_id", "")),
    ]

    best = 0
    for raw_value in candidate_values:
        value = _normalize_app_query(str(raw_value))
        if not value:
            continue
        if value == normalized_query:
            return 100
        if normalized_query in value:
            best = max(best, 92)
        if value in normalized_query:
            best = max(best, 85)

        overlap = len(query_tokens & _tokenize(value))
        if overlap:
            best = max(best, 62 + overlap * 12)

        ratio = SequenceMatcher(None, normalized_query, value).ratio()
        best = max(best, int(ratio * 85))

    if info.get("minimized"):
        best = max(best - 2, 0)

    return best


def _window_wrapper_from_info(info: dict[str, Any]) -> Any:
    hwnd = int(info.get("handle", 0) or 0)
    if hwnd == 0:
        raise RuntimeError("Window handle is missing.")
    return Desktop(backend="uia").window(handle=hwnd)


def _control_capabilities(control: Any) -> dict[str, bool]:
    return {
        name: callable(getattr(control, name, None))
        for name in (
            "invoke",
            "click_input",
            "set_edit_text",
            "type_keys",
            "select",
            "toggle",
            "expand",
            "collapse",
            "set_focus",
            "scroll_into_view",
        )
    }


def _control_summary(control: Any, index: int | None = None) -> dict[str, Any]:
    info = control.element_info
    rectangle = getattr(info, "rectangle", None)
    summary: dict[str, Any] = {
        "name": (getattr(info, "name", "") or "").strip(),
        "control_type": (getattr(info, "control_type", "") or "").strip(),
        "automation_id": (getattr(info, "automation_id", "") or "").strip(),
        "class_name": (getattr(info, "class_name", "") or "").strip(),
        "handle": int(getattr(info, "handle", 0) or 0),
        "enabled": bool(getattr(info, "enabled", True)),
        "visible": bool(getattr(info, "visible", True)),
        "rectangle": _rect_to_dict(rectangle) if rectangle else None,
        "actions": _control_capabilities(control),
    }
    if index is not None:
        summary["index"] = index
    return summary


def _control_score(query: str, summary: dict[str, Any], exact: bool = False) -> int:
    normalized_query = _normalize_app_query(query)
    if not normalized_query:
        return 0

    query_tokens = _tokenize(normalized_query)
    candidate_values = [
        summary.get("name", ""),
        summary.get("automation_id", ""),
        summary.get("class_name", ""),
        summary.get("control_type", ""),
        str(summary.get("handle", "")),
    ]

    best = 0
    for raw_value in candidate_values:
        value = _normalize_app_query(str(raw_value))
        if not value:
            continue
        if value == normalized_query:
            return 100
        if exact:
            continue
        if normalized_query in value:
            best = max(best, 92)
        if value in normalized_query:
            best = max(best, 85)

        overlap = len(query_tokens & _tokenize(value))
        if overlap:
            best = max(best, 62 + overlap * 12)

        ratio = SequenceMatcher(None, normalized_query, value).ratio()
        best = max(best, int(ratio * 88))

    if summary.get("visible"):
        best = min(best + 4, 100)
    if summary.get("enabled"):
        best = min(best + 2, 100)

    return best


def _search_window_controls(
    window: Any,
    query: str = "",
    control_type: str = "",
    limit: int = 20,
    exact: bool = False,
) -> list[tuple[int, dict[str, Any], Any]]:
    query = query.strip()
    type_query = _normalize_app_query(control_type)
    matches: list[tuple[int, dict[str, Any], Any]] = []

    for index, control in enumerate(window.descendants()):
        summary = _control_summary(control, index)
        if not any(
            summary.get(key)
            for key in ("name", "control_type", "automation_id", "class_name")
        ):
            continue

        if type_query:
            haystack = " ".join(
                _normalize_app_query(str(summary.get(key, "")))
                for key in ("name", "control_type", "automation_id", "class_name")
            )
            if type_query not in haystack:
                continue

        if query:
            score = _control_score(query, summary, exact=exact)
            if score <= 0:
                continue
        else:
            score = 50

        if type_query:
            score = min(score + 8, 100)

        matches.append((score, summary, control))

    matches.sort(key=lambda item: (-item[0], item[1].get("index", 0)))
    return matches[: max(limit, 0)]


def _resolve_window_target(query: str = "") -> tuple[dict[str, Any], Any]:
    query = query.strip()
    if not query:
        hwnd = _current_foreground_hwnd()
        if hwnd == 0:
            raise RuntimeError("No foreground window is active.")
        info = _window_info(hwnd)
        return info, _window_wrapper_from_info(info)

    matches = _find_windows(query)
    if not matches:
        raise RuntimeError(f"No visible window matched {query!r}.")
    info = matches[0]
    return info, _window_wrapper_from_info(info)


def _force_foreground_hwnd(hwnd: int) -> None:
    if hwnd == 0:
        return

    ui_window = None
    with contextlib.suppress(Exception):
        ui_window = Desktop(backend="uia").window(handle=hwnd)

    current_thread = None
    target_thread = None
    try:
        import win32api

        current_thread = int(win32api.GetCurrentThreadId())
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        if target_thread and target_thread != current_thread:
            with contextlib.suppress(Exception):
                win32process.AttachThreadInput(current_thread, target_thread, True)

        with contextlib.suppress(Exception):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        with contextlib.suppress(Exception):
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOP,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
        with contextlib.suppress(Exception):
            win32gui.BringWindowToTop(hwnd)
        with contextlib.suppress(Exception):
            win32gui.SetForegroundWindow(hwnd)
        with contextlib.suppress(Exception):
            win32gui.SetActiveWindow(hwnd)
        if ui_window is not None:
            with contextlib.suppress(Exception):
                ui_window.set_focus()
    finally:
        if current_thread and target_thread and target_thread != current_thread:
            with contextlib.suppress(Exception):
                win32process.AttachThreadInput(current_thread, target_thread, False)


def _wait_for_window(query_hints: list[str], timeout: float = 8.0, interval: float = 0.25) -> dict[str, Any] | None:
    cleaned_hints = [hint.strip() for hint in query_hints if hint and hint.strip()]
    if not cleaned_hints:
        return None

    deadline = time.monotonic() + max(timeout, 0.0)
    best_info: dict[str, Any] | None = None
    best_score = 0

    while time.monotonic() < deadline:
        for hint in cleaned_hints:
            matches = _find_windows(hint)
            if not matches:
                continue
            info = matches[0]
            score = _window_score(hint, info)
            if score > best_score:
                best_score = score
                best_info = info
        if best_info is not None:
            return best_info
        time.sleep(interval)

    return None


def _coerce_number(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _ocr_box_to_rect(value: Any) -> dict[str, int] | None:
    if value is None:
        return None

    if isinstance(value, dict):
        left = _coerce_number(value.get("left", value.get("x1", value.get("x"))))
        top = _coerce_number(value.get("top", value.get("y1", value.get("y"))))
        right = _coerce_number(value.get("right", value.get("x2")))
        bottom = _coerce_number(value.get("bottom", value.get("y2")))
        width = _coerce_number(value.get("width"))
        height = _coerce_number(value.get("height"))

        if left is not None and right is None and width is not None:
            right = left + width
        if top is not None and bottom is None and height is not None:
            bottom = top + height

        if left is not None and top is not None and right is not None and bottom is not None:
            return {
                "left": int(round(left)),
                "top": int(round(top)),
                "right": int(round(right)),
                "bottom": int(round(bottom)),
            }

        for key in ("points", "bbox", "box", "rect", "rectangle", "polygon"):
            nested = value.get(key)
            if nested is not None:
                rect = _ocr_box_to_rect(nested)
                if rect is not None:
                    return rect
        return None

    if isinstance(value, (list, tuple)):
        if len(value) == 4 and all(_coerce_number(item) is not None for item in value[:4]):
            left, top, third, fourth = [float(item) for item in value[:4]]
            if third >= left and fourth >= top:
                return {
                    "left": int(round(left)),
                    "top": int(round(top)),
                    "right": int(round(third)),
                    "bottom": int(round(fourth)),
                }
            return {
                "left": int(round(left)),
                "top": int(round(top)),
                "right": int(round(left + third)),
                "bottom": int(round(top + fourth)),
            }

        points: list[tuple[float, float]] = []
        for item in value:
            if isinstance(item, dict):
                x = _coerce_number(item.get("x", item.get("left")))
                y = _coerce_number(item.get("y", item.get("top")))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                x = _coerce_number(item[0])
                y = _coerce_number(item[1])
            else:
                continue
            if x is not None and y is not None:
                points.append((x, y))

        if points:
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            return {
                "left": int(round(min(xs))),
                "top": int(round(min(ys))),
                "right": int(round(max(xs))),
                "bottom": int(round(max(ys))),
            }

    return None


def _rect_center(rect: dict[str, int]) -> tuple[int, int]:
    return (
        int((rect["left"] + rect["right"]) / 2),
        int((rect["top"] + rect["bottom"]) / 2),
    )


def _score_ocr_text(query: str, candidate: str, exact: bool = False) -> int:
    normalized_query = _normalize_app_query(query)
    normalized_candidate = _normalize_app_query(candidate)
    if not normalized_query or not normalized_candidate:
        return 0

    if normalized_query == normalized_candidate:
        return 100
    if exact:
        return 0

    query_tokens = _tokenize(normalized_query)
    candidate_tokens = _tokenize(normalized_candidate)
    best = 0
    if normalized_query in normalized_candidate:
        best = max(best, 92)
    if normalized_candidate in normalized_query:
        best = max(best, 85)

    overlap = len(query_tokens & candidate_tokens)
    if overlap:
        best = max(best, 62 + overlap * 12)

    ratio = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
    best = max(best, int(ratio * 88))
    return best


def _click_point(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    import win32api

    button = button.strip().lower()
    if button not in {"left", "right", "middle"}:
        raise ValueError("button must be left, right, or middle")

    down_flag = {
        "left": win32con.MOUSEEVENTF_LEFTDOWN,
        "right": win32con.MOUSEEVENTF_RIGHTDOWN,
        "middle": win32con.MOUSEEVENTF_MIDDLEDOWN,
    }[button]
    up_flag = {
        "left": win32con.MOUSEEVENTF_LEFTUP,
        "right": win32con.MOUSEEVENTF_RIGHTUP,
        "middle": win32con.MOUSEEVENTF_MIDDLEUP,
    }[button]

    win32api.SetCursorPos((int(x), int(y)))
    for _ in range(max(int(clicks), 1)):
        win32api.mouse_event(down_flag, 0, 0, 0, 0)
        win32api.mouse_event(up_flag, 0, 0, 0, 0)
        time.sleep(0.05)
    return f"Clicked {button} at ({int(x)}, {int(y)})."


def _prepare_control(control: Any) -> None:
    for method_name in ("scroll_into_view", "set_focus"):
        method = getattr(control, method_name, None)
        if callable(method):
            with contextlib.suppress(Exception):
                method()


def _click_control(control: Any, summary: dict[str, Any], clicks: int = 1) -> str:
    _prepare_control(control)

    if int(clicks) <= 1:
        invoke = getattr(control, "invoke", None)
        if callable(invoke):
            with contextlib.suppress(Exception):
                invoke()
                return "invoke"

    click_input = getattr(control, "click_input", None)
    if callable(click_input):
        for _ in range(max(int(clicks), 1)):
            with contextlib.suppress(Exception):
                click_input()
                time.sleep(0.05)
        return "click_input"

    rectangle = summary.get("rectangle")
    if rectangle:
        x, y = _rect_center(rectangle)
        return _click_point(x, y, clicks=clicks)

    raise RuntimeError("Matched control cannot be clicked.")


def _set_control_text(
    control: Any,
    text: str,
    *,
    method: str = "paste",
    replace: bool = True,
) -> str:
    _prepare_control(control)
    method = method.strip().lower()
    if method not in {"paste", "type"}:
        raise ValueError("method must be 'paste' or 'type'")

    set_edit_text = getattr(control, "set_edit_text", None)
    if replace and callable(set_edit_text):
        with contextlib.suppress(Exception):
            set_edit_text(text)
            return "set_edit_text"

    click_input = getattr(control, "click_input", None)
    if callable(click_input):
        with contextlib.suppress(Exception):
            click_input()

    if replace:
        _send_keys("^a{BACKSPACE}")

    if method == "paste":
        _paste_text(text)
    else:
        _send_keys(_escape_send_keys_text(text))
    return "keyboard"


def _normalize_app_query(text: str) -> str:
    cleaned = text.strip().lower()
    cleaned = re.sub(
        r"^(?:please\s+)?(?:(?:open|launch|start|run|bring up|switch to|go to(?: the)?|open the|open up|show me|find|search for|focus on|activate)\s+)+",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\b(?:app|application|window|program)\b$", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _powershell_json(script: str, timeout_seconds: int = 20) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except Exception:
        return []

    if result.returncode != 0:
        return []

    stdout = result.stdout.strip()
    if not stdout:
        return []

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _read_start_apps() -> list[dict[str, Any]]:
    script = (
        "$OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Depth 3"
    )
    apps: list[dict[str, Any]] = []
    for item in _powershell_json(script, timeout_seconds=20):
        name = str(item.get("Name", "") or "").strip()
        app_id = str(item.get("AppID", "") or "").strip()
        if not name or not app_id:
            continue
        apps.append(
            {
                "name": name,
                "kind": "start_app",
                "source": "Get-StartApps",
                "launch_kind": "shell_uri" if not re.search(r"[\\/]", app_id) else "shell_uri_candidate",
                "launch_value": app_id,
                "app_id": app_id,
                "aliases": [name, app_id, pathlib.Path(app_id).stem],
            }
        )
    return apps


def _read_shortcut(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        import win32com.client
    except Exception:
        return None

    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(path))
        target_path = str(getattr(shortcut, "TargetPath", "") or "").strip()
        arguments = str(getattr(shortcut, "Arguments", "") or "").strip()
        working_directory = str(getattr(shortcut, "WorkingDirectory", "") or "").strip()
    except Exception:
        return None

    shortcut_name = path.stem
    target_name = pathlib.Path(target_path).stem if target_path else ""
    aliases = [shortcut_name, target_name, target_path, str(path)]

    return {
        "name": shortcut_name,
        "kind": "shortcut",
        "source": "Start Menu",
        "launch_kind": "shortcut",
        "launch_value": str(path),
        "shortcut_path": str(path),
        "target_path": target_path,
        "arguments": arguments,
        "working_directory": working_directory,
        "aliases": [alias for alias in aliases if alias],
    }


def _read_start_menu_shortcuts() -> list[dict[str, Any]]:
    roots = []
    appdata = os.getenv("APPDATA")
    program_data = os.getenv("ProgramData")
    if appdata:
        roots.append(pathlib.Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if program_data:
        roots.append(pathlib.Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")

    entries: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for shortcut_path in root.rglob("*.lnk"):
            record = _read_shortcut(shortcut_path)
            if record is not None:
                entries.append(record)
    return entries


def _read_registry_app_paths() -> list[dict[str, Any]]:
    try:
        import winreg
    except Exception:
        return []

    roots = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
    ]

    entries: list[dict[str, Any]] = []
    flags = [0]
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        flags.append(winreg.KEY_WOW64_64KEY)
    if hasattr(winreg, "KEY_WOW64_32KEY"):
        flags.append(winreg.KEY_WOW64_32KEY)

    seen: set[tuple[str, str]] = set()
    for root, base_path in roots:
        for view_flag in flags:
            try:
                with winreg.OpenKey(root, base_path, 0, winreg.KEY_READ | view_flag) as base_key:
                    subkey_count = winreg.QueryInfoKey(base_key)[0]
                    for index in range(subkey_count):
                        try:
                            subkey_name = winreg.EnumKey(base_key, index)
                        except OSError:
                            continue
                        try:
                            with winreg.OpenKey(base_key, subkey_name) as app_key:
                                target_path, _ = winreg.QueryValueEx(app_key, None)
                                try:
                                    working_directory, _ = winreg.QueryValueEx(app_key, "Path")
                                except OSError:
                                    working_directory = ""
                        except OSError:
                            continue

                        target_path = str(target_path or "").strip()
                        if not target_path:
                            continue

                        display_name = pathlib.Path(subkey_name).stem
                        key = (display_name.lower(), target_path.lower())
                        if key in seen:
                            continue
                        seen.add(key)
                        entries.append(
                            {
                                "name": display_name,
                                "kind": "app_path",
                                "source": "App Paths",
                                "launch_kind": "exe",
                                "launch_value": target_path,
                                "target_path": target_path,
                                "working_directory": str(working_directory or "").strip(),
                                "aliases": [display_name, pathlib.Path(target_path).stem, subkey_name, target_path],
                            }
                        )
            except OSError:
                continue

    return entries


def _installed_app_entries() -> list[dict[str, Any]]:
    global _APP_INDEX_CACHE
    now = time.monotonic()
    if _APP_INDEX_CACHE is not None and now - _APP_INDEX_CACHE[0] < _APP_INDEX_TTL_SECONDS:
        return _APP_INDEX_CACHE[1]

    entries: list[dict[str, Any]] = []
    entries.extend(_read_start_apps())
    entries.extend(_read_start_menu_shortcuts())
    entries.extend(_read_registry_app_paths())
    _APP_INDEX_CACHE = (now, entries)
    return entries


def _app_score(query: str, entry: dict[str, Any]) -> int:
    normalized_query = _normalize_app_query(query)
    if not normalized_query:
        return 0

    query_tokens = _tokenize(normalized_query)
    candidate_values = [
        entry.get("name", ""),
        entry.get("source", ""),
        entry.get("app_id", ""),
        entry.get("target_path", ""),
        entry.get("launch_value", ""),
    ]
    candidate_values.extend(entry.get("aliases", []))

    best = 0
    for raw_value in candidate_values:
        value = _normalize_app_query(str(raw_value))
        if not value:
            continue
        if value == normalized_query:
            return 100
        if normalized_query in value:
            best = max(best, 92)
        if value in normalized_query:
            best = max(best, 85)

        overlap = len(query_tokens & _tokenize(value))
        if overlap:
            best = max(best, 62 + overlap * 12)

        ratio = SequenceMatcher(None, normalized_query, value).ratio()
        best = max(best, int(ratio * 85))

    if entry.get("kind") == "shortcut":
        best += 4
    elif entry.get("kind") == "app_path":
        best += 2

    return min(best, 100)


def _search_installed_apps(query: str, limit: int = 20) -> list[dict[str, Any]]:
    entries = _installed_app_entries()
    scored = []
    for entry in entries:
        score = _app_score(query, entry)
        if score <= 0:
            continue
        scored.append((score, entry))

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].get("kind") != "shortcut",
            item[1].get("kind") != "app_path",
            item[1].get("kind") != "start_app",
            item[1].get("name", "").lower(),
        )
    )

    results: list[dict[str, Any]] = []
    for score, entry in scored[: max(limit, 0)]:
        results.append(
            {
                "name": entry.get("name", ""),
                "kind": entry.get("kind", ""),
                "source": entry.get("source", ""),
                "score": score,
                "launch_kind": entry.get("launch_kind", ""),
                "launch_value": entry.get("launch_value", ""),
                "app_id": entry.get("app_id", ""),
                "target_path": entry.get("target_path", ""),
                "arguments": entry.get("arguments", ""),
                "working_directory": entry.get("working_directory", ""),
            }
        )
    return results


def _launch_app_entry(entry: dict[str, Any], extra_arguments: list[str] | None = None) -> str:
    extra_arguments = extra_arguments or []
    launch_kind = entry.get("launch_kind", "")
    launch_value = str(entry.get("launch_value", "") or "").strip()
    working_directory = str(entry.get("working_directory", "") or "").strip() or None

    if launch_kind == "shortcut":
        shortcut_path = pathlib.Path(launch_value)
        target_path = str(entry.get("target_path", "") or "").strip()
        shortcut_arguments = str(entry.get("arguments", "") or "").strip()

        if target_path:
            command = [target_path]
            if shortcut_arguments:
                command.extend(shlex.split(shortcut_arguments, posix=False))
            if extra_arguments:
                command.extend(extra_arguments)
            subprocess.Popen(command, cwd=working_directory, shell=False)
        else:
            os.startfile(str(shortcut_path))
        return str(shortcut_path)

    if launch_kind == "exe":
        command = [launch_value]
        shortcut_arguments = str(entry.get("arguments", "") or "").strip()
        if shortcut_arguments:
            command.extend(shlex.split(shortcut_arguments, posix=False))
        if extra_arguments:
            command.extend(extra_arguments)
        subprocess.Popen(command, cwd=working_directory, shell=False)
        return launch_value

    if launch_kind == "shell_uri":
        shell_uri = f"shell:AppsFolder\\{launch_value}"
        os.startfile(shell_uri)
        return shell_uri

    if launch_kind == "shell_uri_candidate":
        try:
            shell_uri = f"shell:AppsFolder\\{launch_value}"
            os.startfile(shell_uri)
            return shell_uri
        except Exception:
            pass

    raise RuntimeError(f"Don't know how to launch {entry.get('name', launch_value)!r}.")


def _launch_window_hints(command: str, entry: dict[str, Any] | None = None) -> list[str]:
    hints: list[str] = [command]
    with contextlib.suppress(Exception):
        command_stem = pathlib.Path(command).stem
        if command_stem and command_stem not in hints:
            hints.append(command_stem)
    if entry is None:
        return hints

    for key in ("name", "app_id", "target_path", "shortcut_path", "launch_value"):
        raw_value = str(entry.get(key, "") or "").strip()
        if not raw_value:
            continue
        hints.append(raw_value)
        with contextlib.suppress(Exception):
            stem = pathlib.Path(raw_value).stem
            if stem and stem not in hints:
                hints.append(stem)

    for alias in entry.get("aliases", []) or []:
        alias_text = str(alias or "").strip()
        if alias_text and alias_text not in hints:
            hints.append(alias_text)

    return hints


def _focus_launched_window(command: str, entry: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any] | None:
    hints = _launch_window_hints(command, entry)
    info = _wait_for_window(hints, timeout=timeout)
    if info is None:
        return None
    _force_foreground_hwnd(int(info.get("handle", 0) or 0))
    return info


def _open_url_in_firefox(url: str, focus: bool = True, timeout: float = 8.0) -> bool:
    matches = _search_installed_apps("Firefox", limit=5)
    if not matches:
        return False

    last_error: Exception | None = None
    for entry in matches:
        try:
            _launch_app_entry(entry, [url])
            if focus:
                _focus_launched_window("Firefox", entry, timeout=timeout)
            return True
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise RuntimeError("Firefox was found, but could not be launched for the URL.") from last_error
    return False


def _looks_like_uri(command: str) -> bool:
    raw = command.strip()
    if not raw:
        return False
    if re.match(r"^[A-Za-z]:[\\/]", raw):
        return False
    return re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*:", raw) is not None


def _set_window_state(
    window_query: str = "",
    state: str = "toggle",
    confirm_close: bool = False,
    x: int = -1,
    y: int = -1,
    width: int = -1,
    height: int = -1,
) -> dict[str, Any]:
    import win32api

    window_info, _ = _resolve_window_target(window_query)
    hwnd = int(window_info.get("handle", 0) or 0)
    if hwnd == 0:
        raise RuntimeError("Window handle is missing.")

    normalized = state.strip().lower().replace(" ", "_").replace("-", "_")
    result: dict[str, Any] = {
        "window": window_info,
        "state": normalized,
    }

    if normalized in {"close", "quit"}:
        if not confirm_close:
            raise ValueError("Refusing to close a window without confirm_close=True")
        with contextlib.suppress(Exception):
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        result["closed"] = True
        return result

    if normalized in {"topmost", "always_on_top", "pin_top"}:
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
        result["state"] = "topmost"
    elif normalized in {"notopmost", "not_topmost", "unpin_top"}:
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
        result["state"] = "notopmost"
    if normalized in {"maximize", "max", "fullscreen", "full_screen"}:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    elif normalized in {"minimize", "min"}:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    elif normalized in {"restore", "normal"}:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    elif normalized in {"toggle", "toggle_maximize", "toggle_fullscreen"}:
        if win32gui.IsZoomed(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            normalized = "restore"
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            normalized = "maximize"
        result["state"] = normalized
    elif normalized in {"move", "resize", "move_resize", "position", "center", "snap_left", "snap_right", "snap_top", "snap_bottom"}:
        rect = win32gui.GetWindowRect(hwnd)
        current_left, current_top, current_right, current_bottom = map(int, rect)
        current_width = current_right - current_left
        current_height = current_bottom - current_top
        screen_width = int(win32api.GetSystemMetrics(0))
        screen_height = int(win32api.GetSystemMetrics(1))
        new_left = current_left if x < 0 else int(x)
        new_top = current_top if y < 0 else int(y)
        new_width = current_width if width < 0 else int(width)
        new_height = current_height if height < 0 else int(height)

        if normalized == "center":
            new_left = max((screen_width - new_width) // 2, 0)
            new_top = max((screen_height - new_height) // 2, 0)
        elif normalized == "snap_left":
            new_left = 0
            new_top = 0
            new_width = screen_width // 2
            new_height = screen_height
        elif normalized == "snap_right":
            new_left = screen_width // 2
            new_top = 0
            new_width = screen_width - new_left
            new_height = screen_height
        elif normalized == "snap_top":
            new_left = 0
            new_top = 0
            new_width = screen_width
            new_height = screen_height // 2
        elif normalized == "snap_bottom":
            new_left = 0
            new_top = screen_height // 2
            new_width = screen_width
            new_height = screen_height - new_top

        flags = (
            win32con.SWP_NOOWNERZORDER
            | win32con.SWP_NOZORDER
            | win32con.SWP_SHOWWINDOW
        )
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOP,
            int(new_left),
            int(new_top),
            int(new_width),
            int(new_height),
            flags,
        )
        result.update(
            {
                "state": normalized,
                "geometry": {
                    "left": int(new_left),
                    "top": int(new_top),
                    "width": int(new_width),
                    "height": int(new_height),
                },
            }
        )
    else:
        raise ValueError(f"Unknown window state: {state!r}")

    with contextlib.suppress(Exception):
        _force_foreground_hwnd(hwnd)

    result["active"] = get_active_window_info().get("active")
    return result


def register(mcp):

    @mcp.tool()
    def open_url(url: str) -> str:
        """Open a URL in the default browser on the Windows host."""
        _require_desktop_stack()
        url = url.strip()
        if not url:
            raise ValueError("url cannot be empty")
        if not _open_url_in_firefox(url):
            webbrowser.open(url)
        return f"Opened {url}"

    @mcp.tool()
    def open_app(command: str, arguments: str = "") -> str:
        """Launch an app, shortcut, or file path on the Windows host, then focus it."""
        _require_desktop_stack()
        command = command.strip()
        if not command:
            raise ValueError("command cannot be empty")

        if re.match(r"^https?://", command, flags=re.IGNORECASE):
            if not _open_url_in_firefox(command):
                webbrowser.open(command)
            return f"Opened {command}"

        if _looks_like_uri(command):
            os.startfile(command)
            return f"Opened {command}"

        args = shlex.split(arguments, posix=False) if arguments else []
        target = pathlib.Path(command)

        if target.exists():
            subprocess.Popen([str(target), *args], shell=False)
            focused = _focus_launched_window(command, timeout=8.0)
            if focused is not None:
                return f"Opened {target} and focused {focused['title']}."
            return f"Opened {target}."

        query = _normalize_app_query(command)
        matches = _search_installed_apps(query, limit=5)
        if matches:
            last_error: Exception | None = None
            for best in matches:
                try:
                    launch_ref = _launch_app_entry(best, args)
                    focused = _focus_launched_window(command, best, timeout=8.0)
                    if focused is not None:
                        return f"Opened {best['name']} from installed apps via {launch_ref} and focused {focused['title']}."
                    return f"Opened {best['name']} from installed apps via {launch_ref}."
                except Exception as exc:
                    last_error = exc
                    continue
            if last_error is not None:
                raise RuntimeError(
                    f"Found installed app matches for {command!r}, but none launched successfully."
                ) from last_error

        resolved = shutil.which(command)
        if resolved:
            subprocess.Popen([resolved, *args], shell=False)
            focused = _focus_launched_window(command, timeout=8.0)
            if focused is not None:
                return f"Started {command} and focused {focused['title']}."
            return f"Started {command}."

        try:
            if args:
                subprocess.Popen([command, *args], shell=False)
            else:
                subprocess.Popen([command], shell=False)
            focused = _focus_launched_window(command, timeout=8.0)
            if focused is not None:
                return f"Started {command} and focused {focused['title']}."
            return f"Started {command}."
        except Exception:
            os.startfile(command)
            focused = _focus_launched_window(command, timeout=8.0)
            if focused is not None:
                return f"Opened {command} and focused {focused['title']}."
            return f"Opened {command}"

    @mcp.tool()
    def search_installed_apps(query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search installed Windows apps, shortcuts, and App Paths entries by name."""
        _require_desktop_stack()
        query = query.strip()
        if not query:
            return []
        return _search_installed_apps(query, limit=limit)

    @mcp.tool()
    def focus_window(query: str) -> str:
        """Bring a matching window to the foreground."""
        _require_desktop_stack()
        matches = _find_windows(query)
        if not matches:
            return f"No visible window matched {query!r}."

        info = matches[0]
        _force_foreground_hwnd(int(info["handle"]))
        return f"Focused window: {info['title']}"

    @mcp.tool()
    def open_or_focus_app(command: str, arguments: str = "", control_limit: int = 20) -> dict[str, Any]:
        """
        Open an app if needed, then focus it and return a control summary for the active window.
        """
        _require_desktop_stack()
        command = command.strip()
        if not command:
            raise ValueError("command cannot be empty")

        opened = False
        try:
            focus_window(command)
        except Exception:
            open_app(command, arguments=arguments)
            opened = True
            time.sleep(0.5)
            with contextlib.suppress(Exception):
                focus_window(command)

        active = get_active_window_info().get("active")
        controls = inspect_active_window(limit=control_limit).get("controls", [])
        return {
            "opened": opened,
            "active": active,
            "controls": controls,
        }

    @mcp.tool()
    def open_system_surface(target: str) -> dict[str, Any]:
        """
        Open a common Windows surface like Start, Run, Settings, Task Manager, Explorer, or Terminal.
        """
        _require_desktop_stack()
        target_text = target.strip()
        if not target_text:
            raise ValueError("target cannot be empty")

        normalized = _normalize_app_query(target_text)
        action = normalized
        if normalized in {"start", "start menu", "windows start"}:
            action = "win"
        elif normalized in {"run", "run dialog"}:
            action = "win+r"
        elif normalized in {"settings", "windows settings"}:
            action = "win+i"
        elif normalized in {"search", "windows search"}:
            action = "win+s"
        elif normalized in {"clipboard history", "clipboard"}:
            action = "win+v"
        elif normalized in {"emoji picker", "emoji"}:
            action = "win+."
        elif normalized in {"quick settings", "quick settings panel"}:
            action = "win+a"
        elif normalized in {"notifications", "notification center"}:
            action = "win+n"
        elif normalized in {"snipping tool", "screen snip", "screenshot tool"}:
            action = "win+shift+s"
        elif normalized in {"power user menu", "power menu", "win+x menu"}:
            action = "win+x"
        elif normalized in {"lock screen", "lock"}:
            action = "win+l"
        elif normalized in {"game bar"}:
            action = "win+g"
        elif normalized in {"explorer", "file explorer"}:
            action = "win+e"
        elif normalized in {"task manager"}:
            action = "ctrl+shift+esc"
        elif normalized in {"task view"}:
            action = "win+tab"
        elif normalized in {"desktop", "show desktop"}:
            action = "win+d"
        elif normalized in {"terminal", "windows terminal"}:
            action = "wt"
        elif normalized in {"control panel"}:
            action = "control"
        elif normalized in {"powershell"}:
            action = "powershell"
        elif normalized in {"command prompt", "cmd"}:
            action = "cmd"
        elif normalized in {"registry editor"}:
            action = "regedit"
        elif normalized in {"services"}:
            action = "services.msc"
        elif normalized in {"event viewer"}:
            action = "eventvwr.msc"
        elif normalized in {"task scheduler"}:
            action = "taskschd.msc"
        elif normalized in {"device manager"}:
            action = "devmgmt.msc"
        elif normalized in {"disk management"}:
            action = "diskmgmt.msc"
        elif normalized in {"computer management"}:
            action = "compmgmt.msc"
        elif normalized in {"resource monitor"}:
            action = "resmon"
        elif normalized in {"performance monitor"}:
            action = "perfmon"
        elif normalized in {"system information"}:
            action = "msinfo32"
        elif normalized in {"system properties"}:
            action = "sysdm.cpl"
        elif normalized in {"firewall", "advanced firewall"}:
            action = "wf.msc"
        elif normalized in {"group policy editor", "local group policy"}:
            action = "gpedit.msc"
        elif normalized in {"local security policy", "security policy"}:
            action = "secpol.msc"
        elif normalized in {"local users and groups", "users and groups"}:
            action = "lusrmgr.msc"
        elif normalized in {"programs and features", "add remove programs"}:
            action = "appwiz.cpl"
        elif normalized in {"network connections", "adapters"}:
            action = "ncpa.cpl"
        elif normalized in {"internet options", "internet properties"}:
            action = "inetcpl.cpl"
        elif normalized in {"sound control panel", "sound panel"}:
            action = "mmsys.cpl"
        elif normalized in {"mouse settings", "mouse control panel"}:
            action = "main.cpl"
        elif normalized in {"date and time control panel", "date time control panel"}:
            action = "timedate.cpl"
        elif normalized in {"power options", "power settings"}:
            action = "powercfg.cpl"
        elif normalized in {"display control panel", "desktop personalization"}:
            action = "desk.cpl"
        elif normalized in {"region settings", "region and language"}:
            action = "intl.cpl"
        elif normalized in {"color management", "color profile"}:
            action = "colorcpl"
        elif normalized in {"display settings", "brightness settings", "screen brightness", "brightness"}:
            action = "ms-settings:display"
        elif normalized in {"sound settings"}:
            action = "ms-settings:sound"
        elif normalized in {"bluetooth settings"}:
            action = "ms-settings:bluetooth"
        elif normalized in {"network settings", "wifi settings"}:
            action = "ms-settings:network-status"
        elif normalized in {"personalization settings"}:
            action = "ms-settings:personalization"
        elif normalized in {"apps settings"}:
            action = "ms-settings:appsfeatures"
        elif normalized in {"default apps"}:
            action = "ms-settings:defaultapps"
        elif normalized in {"accounts settings"}:
            action = "ms-settings:yourinfo"
        elif normalized in {"time settings", "date and time"}:
            action = "ms-settings:dateandtime"
        elif normalized in {"privacy settings"}:
            action = "ms-settings:privacy"
        elif normalized in {"windows update", "update settings"}:
            action = "ms-settings:windowsupdate"
        elif normalized in {"accessibility settings"}:
            action = "ms-settings:easeofaccess"
        elif normalized in {"about this pc", "about"}:
            action = "ms-settings:about"
        elif normalized in {"downloads", "downloads folder"}:
            action = "shell:Downloads"
        elif normalized in {"documents", "documents folder"}:
            action = "shell:Personal"
        elif normalized in {"pictures", "pictures folder"}:
            action = "shell:My Pictures"
        elif normalized in {"music", "music folder"}:
            action = "shell:My Music"
        elif normalized in {"videos", "videos folder"}:
            action = "shell:My Video"
        elif normalized in {"desktop folder"}:
            action = "shell:Desktop"
        elif normalized in {"recent", "recent items"}:
            action = "shell:Recent"
        elif normalized in {"startup", "startup folder"}:
            action = "shell:Startup"
        elif normalized in {"sendto", "send to"}:
            action = "shell:SendTo"
        elif normalized in {"appdata"}:
            action = "shell:AppData"
        elif normalized in {"fonts", "font folder"}:
            action = "shell:Fonts"
        elif normalized in {"printers", "devices and printers"}:
            action = "shell:PrintersFolder"
        elif normalized in {"temp", "temporary files"}:
            action = str(pathlib.Path(tempfile.gettempdir()))
        elif normalized in {"this pc", "my computer", "computer"}:
            action = "shell:MyComputerFolder"
        elif normalized in {"recycle bin", "recycle"}:
            action = "shell:RecycleBinFolder"

        if action == "win":
            press_hotkey("win")
            time.sleep(0.2)
            return {"target": target_text, "action": action, "active": get_active_window_info().get("active")}

        if re.fullmatch(r"(?:win|windows|ctrl|control|alt|shift|cmd|command|super|meta|tab|enter|esc|escape|del|delete|space|spacebar|up|down|left|right|home|end|pageup|pagedown|f(?:[1-9]|1[0-9]|2[0-4]))(?:[+-](?:win|windows|ctrl|control|alt|shift|cmd|command|super|meta|tab|enter|esc|escape|del|delete|space|spacebar|up|down|left|right|home|end|pageup|pagedown|f(?:[1-9]|1[0-9]|2[0-4])|[a-z0-9.]))*",
                       action,
                       flags=re.IGNORECASE):
            press_hotkey(action)
            time.sleep(0.2)
            return {"target": target_text, "action": action, "active": get_active_window_info().get("active")}

        if action.startswith("win+") or action.startswith("ctrl+") or action.startswith("alt+") or action.startswith("shift+"):
            press_hotkey(action)
            time.sleep(0.2)
            return {"target": target_text, "action": action, "active": get_active_window_info().get("active")}

        if action == "wt":
            result = open_app("wt")
            return {"target": target_text, "action": action, "result": result, "active": get_active_window_info().get("active")}

        if action in {"powershell", "cmd", "control", "regedit"}:
            result = open_app(action)
            return {"target": target_text, "action": action, "result": result, "active": get_active_window_info().get("active")}

        if action in {
            "services.msc",
            "eventvwr.msc",
            "taskschd.msc",
            "devmgmt.msc",
            "diskmgmt.msc",
            "compmgmt.msc",
            "resmon",
            "perfmon",
            "msinfo32",
            "sysdm.cpl",
            "wf.msc",
            "gpedit.msc",
            "secpol.msc",
            "lusrmgr.msc",
            "appwiz.cpl",
            "ncpa.cpl",
            "inetcpl.cpl",
            "mmsys.cpl",
            "main.cpl",
            "timedate.cpl",
            "powercfg.cpl",
            "desk.cpl",
            "intl.cpl",
            "colorcpl",
            "ms-settings:display",
            "ms-settings:sound",
            "ms-settings:bluetooth",
            "ms-settings:network-status",
            "ms-settings:personalization",
            "ms-settings:appsfeatures",
            "ms-settings:defaultapps",
            "ms-settings:yourinfo",
            "ms-settings:dateandtime",
            "ms-settings:privacy",
            "ms-settings:windowsupdate",
            "ms-settings:easeofaccess",
            "ms-settings:about",
            "shell:Downloads",
            "shell:Personal",
            "shell:My Pictures",
            "shell:My Music",
            "shell:My Video",
            "shell:Desktop",
            "shell:Recent",
            "shell:Startup",
            "shell:SendTo",
            "shell:AppData",
            "shell:Fonts",
            "shell:PrintersFolder",
            "shell:MyComputerFolder",
            "shell:RecycleBinFolder",
        }:
            result = open_app(action)
            return {"target": target_text, "action": action, "result": result, "active": get_active_window_info().get("active")}

        result = open_app(target_text)
        return {"target": target_text, "action": action, "result": result, "active": get_active_window_info().get("active")}

    @mcp.tool()
    def search_start_menu(query: str, press_enter: bool = True, wait_seconds: float = 0.4) -> dict[str, Any]:
        """Open the Start menu search box, type a query, and optionally launch the top result."""
        _require_desktop_stack()
        query = query.strip()
        if not query:
            raise ValueError("query cannot be empty")

        press_hotkey("win")
        time.sleep(max(wait_seconds, 0.1))
        type_text(query, method="paste", restore_clipboard=True)
        if press_enter:
            time.sleep(0.1)
            press_hotkey("enter")
        time.sleep(0.4)
        return {
            "query": query,
            "press_enter": press_enter,
            "active": get_active_window_info().get("active"),
        }

    @mcp.tool()
    def window_state(
        window_query: str = "",
        state: str = "toggle",
        confirm_close: bool = False,
        x: int = -1,
        y: int = -1,
        width: int = -1,
        height: int = -1,
    ) -> dict[str, Any]:
        """
        Change a window's state or geometry.

        state supports maximize, minimize, restore, toggle, close, move, resize, center, snap_left, snap_right, snap_top, and snap_bottom.
        """
        _require_desktop_stack()
        return _set_window_state(
            window_query=window_query,
            state=state,
            confirm_close=confirm_close,
            x=x,
            y=y,
            width=width,
            height=height,
        )

    @mcp.tool()
    def wait_for_window(query: str, timeout_seconds: float = 10.0, interval_seconds: float = 0.25) -> dict[str, Any]:
        """Wait for a matching window to appear and bring it forward."""
        _require_desktop_stack()
        query = query.strip()
        if not query:
            raise ValueError("query cannot be empty")

        info = _wait_for_window([query], timeout=max(timeout_seconds, 0.0), interval=max(interval_seconds, 0.05))
        if info is None:
            return {"found": False, "query": query, "timeout_seconds": timeout_seconds}

        _force_foreground_hwnd(int(info.get("handle", 0) or 0))
        return {"found": True, "query": query, "timeout_seconds": timeout_seconds, "window": info}

    @mcp.tool()
    def get_active_window_info() -> dict[str, Any]:
        """Return metadata for the currently focused window."""
        _require_desktop_stack()
        hwnd = _current_foreground_hwnd()
        if hwnd == 0:
            return {"active": None}
        return {"active": _window_info(hwnd)}

    @mcp.tool()
    def list_windows(limit: int = 20) -> list[dict[str, Any]]:
        """List visible top-level windows on the desktop."""
        _require_desktop_stack()
        return _visible_windows()[: max(limit, 0)]

    @mcp.tool()
    def inspect_active_window(limit: int = 50) -> dict[str, Any]:
        """
        Return a structured summary of the active window and its accessible controls.
        Useful for figuring out what is on screen before clicking.
        """
        _require_desktop_stack()
        hwnd = _current_foreground_hwnd()
        if hwnd == 0:
            return {"active": None, "controls": []}

        try:
            window = _foreground_app_window()
        except Exception as exc:
            return {"active": _window_info(hwnd), "controls": [], "error": str(exc)}
        controls = [
            summary
            for _, summary, _ in _search_window_controls(window, limit=limit)
        ]

        return {
            "active": _window_info(hwnd),
            "controls": controls,
        }

    @mcp.tool()
    def find_window_controls(
        control_query: str,
        window_query: str = "",
        control_type: str = "",
        limit: int = 20,
        exact: bool = False,
    ) -> dict[str, Any]:
        """
        Search the controls inside a window by label, automation id, class name, or control type.
        """
        _require_desktop_stack()
        window_info, window = _resolve_window_target(window_query)
        matches = _search_window_controls(
            window,
            query=control_query,
            control_type=control_type,
            limit=limit,
            exact=exact,
        )
        return {
            "window": window_info,
            "query": control_query,
            "control_type": control_type,
            "matches": [summary for _, summary, _ in matches],
        }

    @mcp.tool()
    def click_window_control(
        control_query: str,
        window_query: str = "",
        control_type: str = "",
        clicks: int = 1,
        exact: bool = False,
    ) -> dict[str, Any]:
        """
        Click or invoke a control inside a window by matching its label or metadata.
        """
        _require_desktop_stack()
        window_info, window = _resolve_window_target(window_query)
        matches = _search_window_controls(
            window,
            query=control_query,
            control_type=control_type,
            limit=20,
            exact=exact,
        )
        if not matches:
            return {
                "window": window_info,
                "query": control_query,
                "control_type": control_type,
                "matched": None,
                "error": f"No control matched {control_query!r}.",
            }

        score, summary, control = matches[0]
        action = _click_control(control, summary, clicks=clicks)
        return {
            "window": window_info,
            "query": control_query,
            "control_type": control_type,
            "matched": summary,
            "score": score,
            "action": action,
        }

    @mcp.tool()
    def set_window_text(
        control_query: str,
        text: str,
        window_query: str = "",
        control_type: str = "",
        exact: bool = False,
        method: str = "paste",
        replace: bool = True,
    ) -> dict[str, Any]:
        """
        Enter text into a control inside a window by matching its label or metadata.
        """
        _require_desktop_stack()
        window_info, window = _resolve_window_target(window_query)
        matches = _search_window_controls(
            window,
            query=control_query,
            control_type=control_type,
            limit=20,
            exact=exact,
        )
        if not matches:
            return {
                "window": window_info,
                "query": control_query,
                "control_type": control_type,
                "matched": None,
                "error": f"No control matched {control_query!r}.",
            }

        score, summary, control = matches[0]
        action = _set_control_text(control, text, method=method, replace=replace)
        return {
            "window": window_info,
            "query": control_query,
            "control_type": control_type,
            "matched": summary,
            "score": score,
            "action": action,
            "text_length": len(text),
        }

    @mcp.tool()
    def take_screenshot(path: str = "") -> dict[str, str]:
        """Capture the full desktop and save it to a PNG file."""
        _require_desktop_stack()
        output_path = _capture_desktop_screenshot(path)
        return {"path": str(output_path)}

    @mcp.tool()
    async def ocr_image(path: str, lang: str = "en-US", detail_level: str = "line") -> dict[str, Any]:
        """
        Read text from an image on disk using Windows Runtime OCR.
        The result includes both the combined text and the raw OCR lines.
        """
        _require_desktop_stack()
        return await _ocr_image_impl(path, lang, detail_level)

    @mcp.tool()
    async def read_screen_text(lang: str = "en-US", detail_level: str = "line") -> dict[str, Any]:
        """
        Capture the full desktop and OCR the screenshot so the agent can read on-screen text.
        """
        _require_desktop_stack()
        output_path = _capture_desktop_screenshot("")
        return await _ocr_image_impl(str(output_path), lang, detail_level)

    @mcp.tool()
    async def click_screen_text(
        text: str,
        button: str = "left",
        clicks: int = 1,
        exact: bool = False,
        index: int = 0,
        lang: str = "en-US",
        detail_level: str = "line",
    ) -> dict[str, Any]:
        """
        OCR the desktop and click a line of visible text on screen.
        """
        _require_desktop_stack()
        screenshot_path = _capture_desktop_screenshot("")
        summary = await _ocr_image_impl(str(screenshot_path), lang, detail_level)

        matches: list[tuple[int, str, dict[str, int], dict[str, Any]]] = []
        for line in summary.get("lines", []):
            if not isinstance(line, dict):
                continue
            candidate_text = str(line.get("text", "") or "").strip()
            if not candidate_text:
                continue
            score = _score_ocr_text(text, candidate_text, exact=exact)
            if score <= 0:
                continue
            rect = _ocr_box_to_rect(
                line.get("bounding_box")
                or line.get("bbox")
                or line.get("box")
                or line.get("rect")
            )
            if rect is None:
                continue
            matches.append((score, candidate_text, rect, line))

        if not matches:
            return {
                "ok": False,
                "query": text,
                "error": f"No OCR text matched {text!r}.",
                "screenshot": str(screenshot_path),
                "ocr": summary,
            }

        matches.sort(key=lambda item: (-item[0], len(item[1]), item[1].lower()))
        chosen = matches[min(max(index, 0), len(matches) - 1)]
        score, candidate_text, rect, line = chosen
        x, y = _rect_center(rect)
        click_result = _click_point(x, y, button=button, clicks=clicks)
        return {
            "ok": True,
            "query": text,
            "matched_text": candidate_text,
            "score": score,
            "rectangle": rect,
            "point": {"x": x, "y": y},
            "button": button,
            "clicks": clicks,
            "click_result": click_result,
            "line": line,
            "screenshot": str(screenshot_path),
            "ocr": summary,
        }

    @mcp.tool()
    def copy_to_clipboard(text: str) -> str:
        """Replace the Windows clipboard with plain text."""
        _require_desktop_stack()
        _set_clipboard_text(text)
        return "Copied text to clipboard."

    @mcp.tool()
    def read_clipboard() -> str:
        """Read plain text from the Windows clipboard."""
        _require_desktop_stack()
        text = _get_clipboard_text()
        if text is None:
            return ""
        return text

    @mcp.tool()
    def type_text(text: str, method: str = "paste", restore_clipboard: bool = True) -> str:
        """
        Type or paste text into the active window.

        method: "paste" is safest for long text or special characters. "type" uses SendKeys.
        """
        _require_desktop_stack()
        method = method.strip().lower()
        if method not in {"paste", "type"}:
            raise ValueError("method must be 'paste' or 'type'")

        if method == "paste":
            _paste_text(text, restore_clipboard=restore_clipboard)
        else:
            _send_keys(_escape_send_keys_text(text))
        return f"Typed {len(text)} characters using {method}."

    @mcp.tool()
    def press_hotkey(keys: str) -> str:
        """Send a keyboard shortcut to the active window, e.g. ctrl+l or alt+tab."""
        _require_desktop_stack()
        normalized = keys.strip().lower().replace(" ", "")
        if not normalized:
            raise ValueError("keys cannot be empty")

        parts = re.split(r"[+,-]", normalized)
        modifiers = ""
        key = ""
        use_win = False
        for part in parts:
            if part in {"ctrl", "control"}:
                modifiers += "^"
            elif part in {"alt"}:
                modifiers += "%"
            elif part in {"shift"}:
                modifiers += "+"
            elif part in {"win", "windows", "super", "meta", "cmd", "command"}:
                use_win = True
            elif part:
                key = part

        if not key and not use_win:
            raise ValueError("keys must include a non-modifier key, like ctrl+l")

        if use_win:
            import win32api

            win32api.keybd_event(win32con.VK_LWIN, 0, 0, 0)
            try:
                if key:
                    sendkeys_value = modifiers + _format_sendkeys_key(key)
                    _send_keys(sendkeys_value)
                else:
                    time.sleep(0.05)
            finally:
                win32api.keybd_event(win32con.VK_LWIN, 0, win32con.KEYEVENTF_KEYUP, 0)
        else:
            sendkeys_value = modifiers + _format_sendkeys_key(key)
            _send_keys(sendkeys_value)
        return f"Sent hotkey {keys}."

    @mcp.tool()
    def click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        """Click a screen coordinate."""
        _require_desktop_stack()
        return _click_point(int(x), int(y), button=button, clicks=clicks)

    @mcp.tool()
    def move_mouse(x: int, y: int) -> str:
        """Move the mouse cursor to a screen coordinate."""
        _require_desktop_stack()
        import win32api

        win32api.SetCursorPos((int(x), int(y)))
        return f"Moved mouse to ({int(x)}, {int(y)})."

    @mcp.tool()
    def drag_mouse(
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.3,
    ) -> str:
        """Drag the mouse from one point to another."""
        _require_desktop_stack()
        import win32api

        start = (int(start_x), int(start_y))
        end = (int(end_x), int(end_y))
        win32api.SetCursorPos(start)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)

        steps = max(int(duration * 60), 1)
        for step in range(1, steps + 1):
            ratio = step / steps
            x = int(start[0] + (end[0] - start[0]) * ratio)
            y = int(start[1] + (end[1] - start[1]) * ratio)
            win32api.SetCursorPos((x, y))
            time.sleep(duration / steps)

        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return f"Dragged mouse from {start} to {end}."

    @mcp.tool()
    def scroll(amount: int) -> str:
        """Scroll the mouse wheel. Positive values scroll up, negative scroll down."""
        _require_desktop_stack()
        import win32api

        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(amount) * 120, 0)
        return f"Scrolled by {int(amount)} wheel steps."

    @mcp.tool()
    def run_desktop_actions(
        steps: list[dict[str, Any]],
        default_window_query: str = "",
        stop_on_error: bool = True,
        confirm_close: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Execute a chain of desktop actions in order.

        Supported step actions include open_app, open_url, open_system_surface, open_or_focus_app,
        focus_window, window_state, inspect_active_window, list_windows, find_window_controls,
        click_window_control, set_window_text, read_screen_text, click_screen_text, ocr_image,
        take_screenshot, copy_to_clipboard, read_clipboard, type_text, press_hotkey, click,
        move_mouse, drag_mouse, scroll, and wait.
        """
        _require_desktop_stack()
        if not isinstance(steps, list):
            raise TypeError("steps must be a list of action objects")

        def _pick(step: dict[str, Any], *keys: str, default: Any = "") -> Any:
            for key in keys:
                value = step.get(key)
                if value not in (None, ""):
                    return value
            return default

        def _run_async(coro: Any) -> Any:
            try:
                return asyncio.run(coro)
            except RuntimeError as exc:
                if "asyncio.run() cannot be called from a running event loop" not in str(exc):
                    raise
                import threading

                result: dict[str, Any] = {}
                error: list[BaseException] = []

                def _runner() -> None:
                    try:
                        result["value"] = asyncio.run(coro)
                    except BaseException as thread_exc:  # noqa: BLE001
                        error.append(thread_exc)

                thread = threading.Thread(target=_runner, daemon=True)
                thread.start()
                thread.join()
                if error:
                    raise error[0]
                return result.get("value")

        results: list[dict[str, Any]] = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                error = "Each step must be an object"
                results.append({"index": index, "ok": False, "error": error})
                if stop_on_error:
                    break
                continue

            action = str(step.get("action", step.get("tool", "")) or "").strip().lower()
            if not action:
                error = "Step is missing an action name"
                results.append({"index": index, "ok": False, "error": error, "step": step})
                if stop_on_error:
                    break
                continue

            try:
                if action in {"wait", "sleep", "pause"}:
                    seconds = float(_pick(step, "seconds", "delay", "duration", default=0.0))
                    time.sleep(max(seconds, 0.0))
                    outcome: Any = {"slept": seconds}
                elif action == "open_app":
                    outcome = open_app(
                        str(_pick(step, "command", "target", "app", default="")).strip(),
                        arguments=str(_pick(step, "arguments", "args", default="")),
                    )
                elif action == "open_url":
                    outcome = open_url(str(_pick(step, "url", "target", default="")).strip())
                elif action == "open_system_surface":
                    outcome = open_system_surface(str(_pick(step, "target", "surface", default="")).strip())
                elif action == "search_start_menu":
                    outcome = search_start_menu(
                        query=str(_pick(step, "query", "target", default="")).strip(),
                        press_enter=bool(_pick(step, "press_enter", default=True)),
                        wait_seconds=float(_pick(step, "wait_seconds", "delay", default=0.4)),
                    )
                elif action == "open_or_focus_app":
                    outcome = open_or_focus_app(
                        str(_pick(step, "command", "target", "app", default="")).strip(),
                        arguments=str(_pick(step, "arguments", "args", default="")),
                        control_limit=int(_pick(step, "control_limit", default=20)),
                    )
                elif action == "focus_window":
                    outcome = {"focused": focus_window(str(_pick(step, "window_query", "query", "target", default=default_window_query)).strip())}
                elif action == "wait_for_window":
                    outcome = wait_for_window(
                        query=str(_pick(step, "query", "window_query", "target", default=default_window_query)).strip(),
                        timeout_seconds=float(_pick(step, "timeout_seconds", "timeout", default=10.0)),
                        interval_seconds=float(_pick(step, "interval_seconds", "interval", default=0.25)),
                    )
                elif action == "window_state":
                    outcome = window_state(
                        window_query=str(_pick(step, "window_query", "query", "target", default=default_window_query)).strip(),
                        state=str(_pick(step, "state", default="toggle")).strip(),
                        confirm_close=bool(_pick(step, "confirm_close", default=confirm_close)),
                        x=int(_pick(step, "x", default=-1)),
                        y=int(_pick(step, "y", default=-1)),
                        width=int(_pick(step, "width", default=-1)),
                        height=int(_pick(step, "height", default=-1)),
                    )
                elif action == "inspect_active_window":
                    outcome = inspect_active_window(limit=int(_pick(step, "limit", default=50)))
                elif action == "list_windows":
                    outcome = list_windows(limit=int(_pick(step, "limit", default=20)))
                elif action == "find_window_controls":
                    outcome = find_window_controls(
                        control_query=str(_pick(step, "control_query", "query", default="")).strip(),
                        window_query=str(_pick(step, "window_query", "window", "target", default=default_window_query)).strip(),
                        control_type=str(_pick(step, "control_type", default="")),
                        limit=int(_pick(step, "limit", default=20)),
                        exact=bool(_pick(step, "exact", default=False)),
                    )
                elif action == "click_window_control":
                    outcome = click_window_control(
                        control_query=str(_pick(step, "control_query", "query", default="")).strip(),
                        window_query=str(_pick(step, "window_query", "window", "target", default=default_window_query)).strip(),
                        control_type=str(_pick(step, "control_type", default="")),
                        clicks=int(_pick(step, "clicks", default=1)),
                        exact=bool(_pick(step, "exact", default=False)),
                    )
                elif action == "set_window_text":
                    outcome = set_window_text(
                        control_query=str(_pick(step, "control_query", "query", default="")).strip(),
                        text=str(_pick(step, "text", default="")),
                        window_query=str(_pick(step, "window_query", "window", "target", default=default_window_query)).strip(),
                        control_type=str(_pick(step, "control_type", default="")),
                        exact=bool(_pick(step, "exact", default=False)),
                        method=str(_pick(step, "method", default="paste")),
                        replace=bool(_pick(step, "replace", default=True)),
                    )
                elif action == "read_screen_text":
                    outcome = _run_async(
                        read_screen_text(
                            lang=str(_pick(step, "lang", default="en-US")),
                            detail_level=str(_pick(step, "detail_level", default="line")),
                        )
                    )
                elif action == "ocr_image":
                    outcome = _run_async(
                        ocr_image(
                            path=str(_pick(step, "path", default="")).strip(),
                            lang=str(_pick(step, "lang", default="en-US")),
                            detail_level=str(_pick(step, "detail_level", default="line")),
                        )
                    )
                elif action == "click_screen_text":
                    outcome = _run_async(
                        click_screen_text(
                            text=str(_pick(step, "text", default="")).strip(),
                            button=str(_pick(step, "button", default="left")),
                            clicks=int(_pick(step, "clicks", default=1)),
                            exact=bool(_pick(step, "exact", default=False)),
                            index=int(_pick(step, "index", default=0)),
                            lang=str(_pick(step, "lang", default="en-US")),
                            detail_level=str(_pick(step, "detail_level", default="line")),
                        )
                    )
                elif action == "take_screenshot":
                    outcome = take_screenshot(path=str(_pick(step, "path", default="")).strip())
                elif action == "copy_to_clipboard":
                    outcome = copy_to_clipboard(str(_pick(step, "text", default="")))
                elif action == "read_clipboard":
                    outcome = read_clipboard()
                elif action == "type_text":
                    outcome = type_text(
                        text=str(_pick(step, "text", default="")),
                        method=str(_pick(step, "method", default="paste")),
                        restore_clipboard=bool(_pick(step, "restore_clipboard", default=True)),
                    )
                elif action == "press_hotkey":
                    outcome = press_hotkey(str(_pick(step, "keys", "hotkey", default="")).strip())
                elif action == "click":
                    outcome = click(
                        x=int(_pick(step, "x", default=0)),
                        y=int(_pick(step, "y", default=0)),
                        button=str(_pick(step, "button", default="left")),
                        clicks=int(_pick(step, "clicks", default=1)),
                    )
                elif action == "move_mouse":
                    outcome = move_mouse(
                        x=int(_pick(step, "x", default=0)),
                        y=int(_pick(step, "y", default=0)),
                    )
                elif action == "drag_mouse":
                    outcome = drag_mouse(
                        start_x=int(_pick(step, "start_x", default=0)),
                        start_y=int(_pick(step, "start_y", default=0)),
                        end_x=int(_pick(step, "end_x", default=0)),
                        end_y=int(_pick(step, "end_y", default=0)),
                        duration=float(_pick(step, "duration", default=0.3)),
                    )
                elif action == "scroll":
                    outcome = scroll(amount=int(_pick(step, "amount", default=0)))
                else:
                    raise ValueError(f"Unknown desktop action: {action!r}")

                results.append(
                    {
                        "index": index,
                        "action": action,
                        "ok": True,
                        "result": outcome,
                    }
                )
            except Exception as exc:
                entry = {
                    "index": index,
                    "action": action,
                    "ok": False,
                    "error": str(exc),
                }
                if step.get("ignore_error", False):
                    entry["ignored"] = True
                results.append(entry)
                if stop_on_error and not step.get("ignore_error", False):
                    break

        return results
