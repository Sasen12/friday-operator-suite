"""
Saved macro tools for FRIDAY.

These macros let the assistant save and replay repeatable desktop, system,
browser, and app-workflow routines.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json
import os
import pathlib
from typing import Any

from . import browser, desktop, system, workflows


_ALLOWED_RUNNERS = {"desktop", "system", "workflow", "browser", "mixed"}


def _macro_store_path() -> pathlib.Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        return pathlib.Path(appdata) / "Friday" / "macros.json"
    return pathlib.Path.home() / ".friday" / "macros.json"


def _normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _load_store() -> dict[str, Any]:
    path = _macro_store_path()
    if not path.exists():
        return {"macros": []}

    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {"macros": []}

    if not isinstance(data, dict):
        return {"macros": []}
    macros = data.get("macros", [])
    if not isinstance(macros, list):
        macros = []
    return {"macros": [item for item in macros if isinstance(item, dict)]}


def _save_store(store: dict[str, Any]) -> None:
    path = _macro_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _find_macro(store: dict[str, Any], name: str) -> dict[str, Any] | None:
    normalized = _normalize_name(name)
    for macro in store.get("macros", []):
        if _normalize_name(str(macro.get("name", ""))) == normalized:
            return macro
    return None


def _snapshot_macro(macro: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": macro.get("name", ""),
        "runner": macro.get("runner", ""),
        "default_runner": macro.get("default_runner", ""),
        "description": macro.get("description", ""),
        "step_count": len(macro.get("steps", [])) if isinstance(macro.get("steps", []), list) else 0,
        "created_at": macro.get("created_at", ""),
        "updated_at": macro.get("updated_at", ""),
    }


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
            except BaseException as inner_exc:  # pragma: no cover - defensive
                error.append(inner_exc)

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()
        if error:
            raise error[0]
        return result.get("value")


def _coerce_bool(value: Any, default: bool = False) -> bool:
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


def register(mcp):

    @mcp.tool()
    def list_macros() -> list[dict[str, Any]]:
        """List saved macros."""
        store = _load_store()
        macros = [_snapshot_macro(macro) for macro in store.get("macros", [])]
        macros.sort(key=lambda item: item.get("name", "").lower())
        return macros

    @mcp.tool()
    def save_macro(
        name: str,
        steps: list[dict[str, Any]],
        runner: str = "mixed",
        description: str = "",
        default_runner: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Save a reusable macro to disk."""
        raw_name = name.strip()
        if not raw_name:
            raise ValueError("name cannot be empty")
        if runner.strip().lower() not in _ALLOWED_RUNNERS:
            raise ValueError(f"runner must be one of: {', '.join(sorted(_ALLOWED_RUNNERS))}")
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must be a non-empty list")

        normalized_default = default_runner.strip().lower()
        if normalized_default and normalized_default not in {"desktop", "system", "workflow", "browser"}:
            raise ValueError("default_runner must be desktop, system, workflow, browser, or empty")

        store = _load_store()
        existing = _find_macro(store, raw_name)
        if existing is not None and not overwrite:
            raise ValueError(f"Macro {raw_name!r} already exists. Use overwrite=True to replace it.")

        timestamp = _dt.datetime.now().isoformat()
        record = {
            "name": raw_name,
            "runner": runner.strip().lower(),
            "default_runner": normalized_default,
            "description": description.strip(),
            "steps": json.loads(json.dumps(steps, default=str)),
            "created_at": existing.get("created_at", timestamp) if existing else timestamp,
            "updated_at": timestamp,
        }

        if existing is None:
            store["macros"].append(record)
        else:
            existing_index = store["macros"].index(existing)
            store["macros"][existing_index] = record

        _save_store(store)
        return {"saved": True, "macro": _snapshot_macro(record), "path": str(_macro_store_path())}

    @mcp.tool()
    def delete_macro(name: str, confirm: bool = False) -> dict[str, Any]:
        """Delete a saved macro."""
        if not confirm:
            raise ValueError("Refusing to delete a macro without confirm=True")

        store = _load_store()
        macro = _find_macro(store, name)
        if macro is None:
            raise FileNotFoundError(f"Macro not found: {name}")

        store["macros"].remove(macro)
        _save_store(store)
        return {"deleted": True, "name": macro.get("name", ""), "path": str(_macro_store_path())}

    @mcp.tool()
    def run_macro(name: str, stop_on_error: bool = True) -> dict[str, Any]:
        """Run a saved macro."""
        store = _load_store()
        macro = _find_macro(store, name)
        if macro is None:
            raise FileNotFoundError(f"Macro not found: {name}")

        runner = str(macro.get("runner", "")).strip().lower()
        steps = macro.get("steps", [])
        default_runner = str(macro.get("default_runner", "")).strip().lower()

        if runner == "desktop":
            result = desktop.run_desktop_actions(steps=steps, stop_on_error=stop_on_error)
        elif runner == "system":
            result = system.run_system_actions(steps=steps, stop_on_error=stop_on_error)
        elif runner == "workflow":
            result = workflows.run_workflow_actions(steps=steps, stop_on_error=stop_on_error)
        elif runner == "browser":
            result = _run_async(browser.browser_run_actions(steps=steps, stop_on_error=stop_on_error))
        elif runner == "mixed":
            results: list[dict[str, Any]] = []
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    results.append({"index": index, "ok": False, "error": "Each step must be an object"})
                    if stop_on_error:
                        break
                    continue

                step_runner = str(step.get("runner", step.get("engine", default_runner)) or "").strip().lower()
                if not step_runner:
                    results.append({"index": index, "ok": False, "error": "Step is missing a runner"})
                    if stop_on_error:
                        break
                    continue

                step_payload = dict(step)
                step_payload.pop("runner", None)
                step_payload.pop("engine", None)
                try:
                    if step_runner == "desktop":
                        outcome = desktop.run_desktop_actions(steps=[step_payload], stop_on_error=True)
                    elif step_runner == "system":
                        outcome = system.run_system_actions(steps=[step_payload], stop_on_error=True)
                    elif step_runner == "workflow":
                        outcome = workflows.run_workflow_actions(steps=[step_payload], stop_on_error=True)
                    elif step_runner == "browser":
                        outcome = _run_async(browser.browser_run_actions(steps=[step_payload], stop_on_error=True))
                    else:
                        raise ValueError(f"Unknown step runner: {step_runner!r}")
                    results.append({"index": index, "runner": step_runner, "ok": True, "result": outcome})
                except Exception as exc:
                    results.append({"index": index, "runner": step_runner, "ok": False, "error": str(exc)})
                    if stop_on_error and not _coerce_bool(step.get("ignore_error", False)):
                        break
            result = results
        else:
            raise ValueError(f"Unknown macro runner: {runner!r}")

        return {
            "name": macro.get("name", name),
            "runner": runner,
            "default_runner": default_runner,
            "description": macro.get("description", ""),
            "result": result,
        }
