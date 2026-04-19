"""
Playwright browser tools - richer website automation for FRIDAY.
"""

from __future__ import annotations

import atexit
import asyncio
import contextlib
import os
import pathlib
import re
import tempfile
import time
from typing import Any
from urllib.parse import quote_plus

try:
    from playwright.async_api import async_playwright

    _BROWSER_IMPORT_ERROR = None
except Exception as exc:
    async_playwright = None
    _BROWSER_IMPORT_ERROR = exc


_BROWSER_PLAYWRIGHT = None
_BROWSER_CONTEXT = None
_BROWSER_LOCK: asyncio.Lock | None = None
_BROWSER_PROFILE_DIR = pathlib.Path(tempfile.gettempdir()) / f"friday_playwright_profile_{os.getpid()}"


def _require_browser_stack() -> None:
    if _BROWSER_IMPORT_ERROR is not None:
        raise RuntimeError(
            "Browser automation tools are unavailable in the current environment."
        ) from _BROWSER_IMPORT_ERROR


def _browser_lock() -> asyncio.Lock:
    global _BROWSER_LOCK
    if _BROWSER_LOCK is None:
        _BROWSER_LOCK = asyncio.Lock()
    return _BROWSER_LOCK


def _active_pages(context) -> list[Any]:
    return [page for page in context.pages if not page.is_closed()]


async def _page_summary(page: Any, index: int, total: int) -> dict[str, Any]:
    title = ""
    with contextlib.suppress(Exception):
        title = (await page.title()).strip()

    return {
        "index": index,
        "active": index == total - 1,
        "title": title,
        "url": page.url,
    }


def _shutdown_browser() -> None:
    global _BROWSER_CONTEXT, _BROWSER_PLAYWRIGHT

    if _BROWSER_CONTEXT is not None:
        with contextlib.suppress(Exception):
            asyncio.run(_BROWSER_CONTEXT.close())
        _BROWSER_CONTEXT = None

    if _BROWSER_PLAYWRIGHT is not None:
        with contextlib.suppress(Exception):
            asyncio.run(_BROWSER_PLAYWRIGHT.stop())
        _BROWSER_PLAYWRIGHT = None


atexit.register(_shutdown_browser)


def _normalize_url(url: str) -> str:
    url = url.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*:", url):
        return url
    if not url:
        return url
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", url):
        return f"https://{url}"
    return url


async def _ensure_context():
    global _BROWSER_PLAYWRIGHT, _BROWSER_CONTEXT
    _require_browser_stack()

    if _BROWSER_CONTEXT is not None:
        try:
            _ = _BROWSER_CONTEXT.pages
            return _BROWSER_CONTEXT
        except Exception:
            _BROWSER_CONTEXT = None

    if _BROWSER_PLAYWRIGHT is None:
        if async_playwright is None:
            raise RuntimeError("Playwright is unavailable in the current environment.") from _BROWSER_IMPORT_ERROR
        _BROWSER_PLAYWRIGHT = await async_playwright().start()

    _BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(_BROWSER_PROFILE_DIR),
            "headless": False,
            "accept_downloads": True,
            "ignore_https_errors": True,
        }
        _BROWSER_CONTEXT = await _BROWSER_PLAYWRIGHT.firefox.launch_persistent_context(**launch_kwargs)
        _BROWSER_CONTEXT.set_default_timeout(10_000)
        _BROWSER_CONTEXT.set_default_navigation_timeout(20_000)
    except Exception as exc:
        raise RuntimeError(
            "FRIDAY could not launch Firefox through Playwright. "
            "Make sure Firefox is installed and try again."
        ) from exc

    if not _BROWSER_CONTEXT.pages:
        await _BROWSER_CONTEXT.new_page()

    return _BROWSER_CONTEXT


async def _get_page(tab: int = -1):
    async with _browser_lock():
        context = await _ensure_context()
        pages = _active_pages(context)
        if not pages:
            page = await context.new_page()
            await page.bring_to_front()
            return page, 0, 1

        if tab < 0:
            index = len(pages) - 1
        else:
            if tab >= len(pages):
                raise IndexError(f"Tab index {tab} is out of range for {len(pages)} open tabs.")
            index = tab

        page = pages[index]
        with contextlib.suppress(Exception):
            await page.bring_to_front()
        return page, index, len(pages)


async def _page_text(page: Any, selector: str) -> str:
    locator = page.locator(selector)
    text = await locator.inner_text(timeout=10_000)
    return text.strip()


async def _page_snapshot(page: Any, selector: str) -> str:
    locator = page.locator(selector)
    return await locator.aria_snapshot(timeout=10_000)


async def _page_links(page: Any, limit: int = 100) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    locators = await page.locator("a").all()
    for index, link in enumerate(locators[: max(limit, 0)]):
        href = None
        with contextlib.suppress(Exception):
            href = await link.get_attribute("href", timeout=2_000)
        label = ""
        with contextlib.suppress(Exception):
            label = (await link.inner_text(timeout=2_000)).strip()
        title = None
        with contextlib.suppress(Exception):
            title = await link.get_attribute("title", timeout=2_000)
        results.append(
            {
                "index": index,
                "text": label,
                "href": href,
                "title": title,
            }
        )
    return results


def register(mcp):

    @mcp.tool()
    async def browser_open_url(url: str, new_tab: bool = True) -> dict[str, Any]:
        """Open a URL in a Playwright-controlled Firefox session."""
        raw_url = url.strip()
        if not raw_url:
            raise ValueError("url cannot be empty")
        url = _normalize_url(raw_url)

        async with _browser_lock():
            context = await _ensure_context()
            page = await context.new_page() if new_tab or not _active_pages(context) else _active_pages(context)[-1]
            await page.goto(url, wait_until="domcontentloaded")
            with contextlib.suppress(Exception):
                await page.bring_to_front()
            index = _active_pages(context).index(page)
            return await _page_summary(page, index, len(_active_pages(context)))

    @mcp.tool()
    async def browser_list_tabs() -> list[dict[str, Any]]:
        """List the tabs in the Playwright browser session."""
        async with _browser_lock():
            context = await _ensure_context()
            pages = _active_pages(context)
            return [await _page_summary(page, index, len(pages)) for index, page in enumerate(pages)]

    @mcp.tool()
    async def browser_focus_tab(tab: int = -1) -> dict[str, Any]:
        """Bring a tab to the foreground."""
        page, index, total = await _get_page(tab)
        with contextlib.suppress(Exception):
            await page.bring_to_front()
        return await _page_summary(page, index, total)

    @mcp.tool()
    async def browser_describe_page(tab: int = -1, max_chars: int = 5000) -> dict[str, Any]:
        """Return the page title, URL, visible text, aria snapshot, and links."""
        page, index, total = await _get_page(tab)
        title = ""
        with contextlib.suppress(Exception):
            title = (await page.title()).strip()

        text = ""
        with contextlib.suppress(Exception):
            text = await _page_text(page, "body")
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars] + "..."

        snapshot = ""
        with contextlib.suppress(Exception):
            snapshot = await _page_snapshot(page, "body")

        links = await _page_links(page, limit=25)
        summary = await _page_summary(page, index, total)
        summary.update(
            {
                "body_text": text,
                "aria_snapshot": snapshot,
                "links": links,
            }
        )
        summary["title"] = title or summary["title"]
        return summary

    @mcp.tool()
    async def browser_read_text(selector: str = "body", tab: int = -1, max_chars: int = 12000) -> dict[str, Any]:
        """Read visible text from a CSS selector on the current page."""
        page, index, total = await _get_page(tab)
        text = await _page_text(page, selector)
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars] + "..."

        summary = await _page_summary(page, index, total)
        summary.update({"selector": selector, "text": text})
        return summary

    @mcp.tool()
    async def browser_snapshot(selector: str = "body", tab: int = -1) -> dict[str, Any]:
        """Capture the aria snapshot for a selector on the current page."""
        page, index, total = await _get_page(tab)
        snapshot = await _page_snapshot(page, selector)
        summary = await _page_summary(page, index, total)
        summary.update({"selector": selector, "snapshot": snapshot})
        return summary

    @mcp.tool()
    async def browser_click_text(text: str, tab: int = -1, exact: bool = False) -> str:
        """Click the first matching text node or link."""
        page, _, _ = await _get_page(tab)
        locator = page.get_by_text(text, exact=exact).first
        await locator.click(timeout=10_000)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
        return f"Clicked text {text!r}."

    @mcp.tool()
    async def browser_click_role(role: str, name: str = "", tab: int = -1, exact: bool = False) -> str:
        """Click the first element that matches an ARIA role."""
        page, _, _ = await _get_page(tab)
        role = role.strip()
        if not role:
            raise ValueError("role cannot be empty")

        locator = page.get_by_role(role, name=name, exact=exact) if name else page.get_by_role(role)
        await locator.first.click(timeout=10_000)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
        return f"Clicked role {role!r}."

    @mcp.tool()
    async def browser_fill(selector: str, value: str, tab: int = -1, clear: bool = True) -> str:
        """Fill an input, textarea, or contenteditable element by CSS selector."""
        page, _, _ = await _get_page(tab)
        locator = page.locator(selector).first
        if clear:
            await locator.fill(value, timeout=10_000)
        else:
            await locator.type(value, timeout=10_000)
        return f"Filled selector {selector!r}."

    @mcp.tool()
    async def browser_fill_label(label: str, value: str, tab: int = -1, exact: bool = False) -> str:
        """Fill an input by its accessible label."""
        page, _, _ = await _get_page(tab)
        locator = page.get_by_label(label, exact=exact).first
        await locator.fill(value, timeout=10_000)
        return f"Filled label {label!r}."

    @mcp.tool()
    async def browser_press(keys: str, tab: int = -1) -> str:
        """Send keyboard input to the current page, for example Control+L or Enter."""
        page, _, _ = await _get_page(tab)
        await page.keyboard.press(keys)
        return f"Pressed {keys!r}."

    @mcp.tool()
    async def browser_extract_links(tab: int = -1, limit: int = 100) -> list[dict[str, Any]]:
        """Return a list of links visible on the page."""
        page, _, _ = await _get_page(tab)
        return await _page_links(page, limit=limit)

    @mcp.tool()
    async def browser_go_back(tab: int = -1) -> dict[str, Any]:
        """Navigate back in browser history."""
        page, index, total = await _get_page(tab)
        with contextlib.suppress(Exception):
            await page.go_back(wait_until="domcontentloaded")
        return await _page_summary(page, index, total)

    @mcp.tool()
    async def browser_go_forward(tab: int = -1) -> dict[str, Any]:
        """Navigate forward in browser history."""
        page, index, total = await _get_page(tab)
        with contextlib.suppress(Exception):
            await page.go_forward(wait_until="domcontentloaded")
        return await _page_summary(page, index, total)

    @mcp.tool()
    async def browser_reload(tab: int = -1) -> dict[str, Any]:
        """Reload the current page."""
        page, index, total = await _get_page(tab)
        with contextlib.suppress(Exception):
            await page.reload(wait_until="domcontentloaded")
        return await _page_summary(page, index, total)

    @mcp.tool()
    async def browser_screenshot(path: str = "", tab: int = -1, full_page: bool = True) -> dict[str, str]:
        """Capture a screenshot of the current page."""
        page, _, _ = await _get_page(tab)
        if path.strip():
            output_path = pathlib.Path(path).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            output_dir = pathlib.Path(tempfile.gettempdir()) / "friday_browser_screenshots"
            output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            output_path = output_dir / f"page-{timestamp}.png"

        await page.screenshot(path=str(output_path), full_page=full_page)
        return {"path": str(output_path)}

    @mcp.tool()
    async def browser_close_tab(tab: int = -1) -> str:
        """Close a tab in the Playwright browser session."""
        page, _, _ = await _get_page(tab)
        title = ""
        with contextlib.suppress(Exception):
            title = (await page.title()).strip()
        await page.close()
        return f"Closed tab {title or page.url!r}."

    @mcp.tool()
    async def browser_search_web(query: str, new_tab: bool = True) -> dict[str, Any]:
        """Search the web in the Playwright browser session."""
        raw_query = query.strip()
        if not raw_query:
            raise ValueError("query cannot be empty")
        search_url = f"https://www.google.com/search?q={quote_plus(raw_query)}"
        summary = await browser_open_url(search_url, new_tab=new_tab)
        summary.update({"query": raw_query, "search_url": search_url})
        return summary

    @mcp.tool()
    async def browser_run_actions(
        steps: list[dict[str, Any]],
        default_tab: int = -1,
        stop_on_error: bool = True,
    ) -> list[dict[str, Any]]:
        """Execute a chain of browser actions in order."""
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

            tab = int(_pick(step, "tab", default=default_tab))
            new_tab = bool(_pick(step, "new_tab", default=True))
            try:
                if action == "open_url":
                    outcome = await browser_open_url(str(_pick(step, "url", "target", default="")).strip(), new_tab=new_tab)
                elif action == "search_web":
                    outcome = await browser_search_web(str(_pick(step, "query", "target", default="")).strip(), new_tab=new_tab)
                elif action == "list_tabs":
                    outcome = await browser_list_tabs()
                elif action == "focus_tab":
                    outcome = await browser_focus_tab(tab=tab)
                elif action == "describe_page":
                    outcome = await browser_describe_page(tab=tab, max_chars=int(_pick(step, "max_chars", default=5000)))
                elif action == "read_text":
                    outcome = await browser_read_text(
                        selector=str(_pick(step, "selector", default="body")),
                        tab=tab,
                        max_chars=int(_pick(step, "max_chars", default=12000)),
                    )
                elif action == "snapshot":
                    outcome = await browser_snapshot(
                        selector=str(_pick(step, "selector", default="body")),
                        tab=tab,
                    )
                elif action == "click_text":
                    outcome = await browser_click_text(
                        text=str(_pick(step, "text", default="")).strip(),
                        tab=tab,
                        exact=bool(_pick(step, "exact", default=False)),
                    )
                elif action == "click_role":
                    outcome = await browser_click_role(
                        role=str(_pick(step, "role", default="")).strip(),
                        name=str(_pick(step, "name", default="")),
                        tab=tab,
                        exact=bool(_pick(step, "exact", default=False)),
                    )
                elif action == "fill":
                    outcome = await browser_fill(
                        selector=str(_pick(step, "selector", default="")).strip(),
                        value=str(_pick(step, "value", default="")),
                        tab=tab,
                        clear=bool(_pick(step, "clear", default=True)),
                    )
                elif action == "fill_label":
                    outcome = await browser_fill_label(
                        label=str(_pick(step, "label", default="")).strip(),
                        value=str(_pick(step, "value", default="")),
                        tab=tab,
                        exact=bool(_pick(step, "exact", default=False)),
                    )
                elif action == "press":
                    outcome = await browser_press(
                        keys=str(_pick(step, "keys", "hotkey", default="")).strip(),
                        tab=tab,
                    )
                elif action == "extract_links":
                    outcome = await browser_extract_links(tab=tab, limit=int(_pick(step, "limit", default=100)))
                elif action == "go_back":
                    outcome = await browser_go_back(tab=tab)
                elif action == "go_forward":
                    outcome = await browser_go_forward(tab=tab)
                elif action == "reload":
                    outcome = await browser_reload(tab=tab)
                elif action == "screenshot":
                    outcome = await browser_screenshot(
                        path=str(_pick(step, "path", default="")).strip(),
                        tab=tab,
                        full_page=bool(_pick(step, "full_page", default=True)),
                    )
                elif action == "close_tab":
                    outcome = await browser_close_tab(tab=tab)
                elif action == "wait":
                    await asyncio.sleep(max(float(_pick(step, "seconds", "delay", default=0.0)), 0.0))
                    outcome = {"slept": float(_pick(step, "seconds", "delay", default=0.0))}
                elif action == "scroll":
                    page, _, _ = await _get_page(tab)
                    await page.mouse.wheel(
                        int(_pick(step, "delta_x", default=0)),
                        int(_pick(step, "delta_y", "amount", default=0)),
                    )
                    outcome = {"scrolled": True}
                else:
                    raise ValueError(f"Unknown browser action: {action!r}")

                results.append({"index": index, "action": action, "ok": True, "result": outcome})
            except Exception as exc:
                results.append({"index": index, "action": action, "ok": False, "error": str(exc)})
                if stop_on_error and not step.get("ignore_error", False):
                    break

        return results
