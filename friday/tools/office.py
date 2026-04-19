"""
Office tools - browser-backed Gmail and Google Calendar workflows.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import re
from typing import Any
from urllib.parse import quote_plus, urlencode

from . import browser as browser_tools


GMAIL_INBOX_URL = "https://mail.google.com/mail/u/0/#inbox"
GMAIL_SEARCH_URL = "https://mail.google.com/mail/u/0/#search/"
CALENDAR_HOME_URL = "https://calendar.google.com/calendar/u/0/r"
CALENDAR_EVENT_URL = "https://calendar.google.com/calendar/r/eventedit"


async def _open_or_reuse_page(url: str, match_terms: list[str], new_tab: bool = True):
    async with browser_tools._browser_lock():
        context = await browser_tools._ensure_context()
        pages = browser_tools._active_pages(context)
        for index, page in enumerate(pages):
            title = ""
            with contextlib.suppress(Exception):
                title = (await page.title()).strip()
            haystack = f"{page.url} {title}".lower()
            if any(term.lower() in haystack for term in match_terms):
                with contextlib.suppress(Exception):
                    await page.bring_to_front()
                return page, index, len(pages)

        page = await context.new_page() if new_tab or not pages else pages[-1]
        await page.goto(url, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            await page.bring_to_front()
        pages = browser_tools._active_pages(context)
        return page, pages.index(page), len(pages)


async def _page_summary(page: Any, index: int, total: int) -> dict[str, Any]:
    return await browser_tools._page_summary(page, index, total)


async def _gmail_page(new_tab: bool = True):
    return await _open_or_reuse_page(GMAIL_INBOX_URL, ["mail.google.com", "gmail"], new_tab=new_tab)


async def _calendar_page(new_tab: bool = True):
    return await _open_or_reuse_page(CALENDAR_HOME_URL, ["calendar.google.com", "calendar"], new_tab=new_tab)


async def _gmail_search_messages_impl(query: str, max_results: int = 10, new_tab: bool = True) -> dict[str, Any]:
    raw_query = query.strip()
    if not raw_query:
        raise ValueError("query cannot be empty")

    search_url = f"{GMAIL_SEARCH_URL}{quote_plus(raw_query)}"
    page, index, total = await _open_or_reuse_page(
        search_url,
        ["mail.google.com", "gmail"],
        new_tab=new_tab,
    )
    with contextlib.suppress(Exception):
        await page.wait_for_load_state("domcontentloaded", timeout=10_000)
    with contextlib.suppress(Exception):
        await page.wait_for_timeout(1500)

    results = await _gmail_search_rows(page, limit=max_results)
    summary = await _page_summary(page, index, total)
    summary.update(
        {
            "query": raw_query,
            "search_url": search_url,
            "results": results,
        }
    )
    return summary


async def _gmail_send_current_draft_impl(confirm: bool = False, new_tab: bool = False) -> dict[str, Any]:
    if not confirm:
        raise ValueError("Refusing to send email without confirm=True")

    page, index, total = await _gmail_page(new_tab=new_tab)
    send_candidates = [
        page.get_by_role("button", name=re.compile(r"^Send$", re.I)),
        page.get_by_text(re.compile(r"^Send$", re.I), exact=True),
        page.locator('div[role="button"][aria-label*="Send"]'),
    ]
    sent = await _click_first(send_candidates)
    if not sent:
        with contextlib.suppress(Exception):
            await page.keyboard.press("Control+Enter")
            sent = True
    if sent:
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=10_000)
        with contextlib.suppress(Exception):
            await page.wait_for_timeout(1200)

    summary = await _page_summary(page, index, total)
    summary.update({"sent": sent, "confirm": confirm})
    return summary


async def _fill_first(locator_candidates: list[Any], value: str) -> bool:
    for locator in locator_candidates:
        if locator is None:
            continue
        try:
            target = locator.first if hasattr(locator, "first") else locator
            await target.fill(value, timeout=5_000)
            return True
        except Exception:
            continue
    return False


async def _click_first(locator_candidates: list[Any]) -> bool:
    for locator in locator_candidates:
        if locator is None:
            continue
        try:
            target = locator.first if hasattr(locator, "first") else locator
            await target.click(timeout=5_000)
            return True
        except Exception:
            continue
    return False


async def _gmail_search_rows(page: Any, limit: int = 10) -> list[dict[str, Any]]:
    selectors = [
        'tr[role="row"]',
        'table[role="grid"] tr',
        '[role="main"] tr',
        '[role="main"] [role="link"]',
    ]
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        if not count:
            continue
        for index in range(min(count, max(limit * 3, limit))):
            item = locator.nth(index)
            text = ""
            with contextlib.suppress(Exception):
                text = (await item.inner_text(timeout=2_000)).strip()
            if not text:
                continue
            normalized = " ".join(text.split())
            if len(normalized) < 3 or normalized in seen:
                continue
            seen.add(normalized)
            rows.append({"index": len(rows), "text": normalized[:600]})
            if len(rows) >= limit:
                return rows
        if rows:
            break
    return rows


def _parse_iso_datetime(value: str) -> _dt.datetime:
    text = value.strip()
    if not text:
        raise ValueError("datetime value cannot be empty")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = _dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)
    return parsed


def _format_calendar_timestamp(value: str) -> str:
    return _parse_iso_datetime(value).astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _calendar_event_url(
    title: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    timezone_name: str = "Australia/Sydney",
) -> str:
    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": f"{_format_calendar_timestamp(start)}/{_format_calendar_timestamp(end)}",
        "details": description,
        "location": location,
        "stz": timezone_name,
        "etz": timezone_name,
    }
    cleaned = {key: value for key, value in params.items() if str(value).strip()}
    return f"{CALENDAR_EVENT_URL}?{urlencode(cleaned, quote_via=quote_plus)}"


def register(mcp):

    @mcp.tool()
    async def gmail_open_inbox(new_tab: bool = True) -> dict[str, Any]:
        """Open the Gmail inbox in the shared Playwright browser session."""
        page, index, total = await _gmail_page(new_tab=new_tab)
        return await _page_summary(page, index, total)

    @mcp.tool()
    async def gmail_search_messages(query: str, max_results: int = 10, new_tab: bool = True) -> dict[str, Any]:
        """Search Gmail and return a compact list of matching message rows."""
        return await _gmail_search_messages_impl(query, max_results=max_results, new_tab=new_tab)

    @mcp.tool()
    async def gmail_open_message(query: str, index: int = 0, max_results: int = 10, new_tab: bool = True) -> dict[str, Any]:
        """
        Search Gmail and open one matching message thread.
        """
        search = await _gmail_search_messages_impl(query, max_results=max_results, new_tab=new_tab)
        page, page_index, total = await _gmail_page(new_tab=False)
        if not search.get("results"):
            return {**search, "opened": False, "error": f"No Gmail messages matched {query!r}."}

        rows = search["results"]
        chosen = rows[min(max(index, 0), len(rows) - 1)]
        selector_candidates = [
            'tr[role="row"]',
            'table[role="grid"] tr',
            '[role="main"] tr',
        ]
        clicked = False
        for selector in selector_candidates:
            locator = page.locator(selector)
            try:
                count = await locator.count()
            except Exception:
                continue
            if count <= 0:
                continue
            target_index = min(chosen["index"], count - 1)
            target = locator.nth(target_index)
            try:
                await target.click(timeout=5_000)
                clicked = True
                break
            except Exception:
                link = target.locator("a").first
                try:
                    await link.click(timeout=5_000)
                    clicked = True
                    break
                except Exception:
                    continue

        if clicked:
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            with contextlib.suppress(Exception):
                await page.wait_for_timeout(1200)

        body_text = ""
        with contextlib.suppress(Exception):
            body_text = await browser_tools._page_text(page, '[role="main"]')
        if body_text and len(body_text) > 12000:
            body_text = body_text[:12000] + "..."

        summary = await _page_summary(page, page_index, total)
        summary.update(
            {
                "query": query,
                "opened": clicked,
                "selected": chosen,
                "body_text": body_text,
            }
        )
        return summary

    @mcp.tool()
    async def gmail_read_current_message(max_chars: int = 12000) -> dict[str, Any]:
        """Read the currently open Gmail thread."""
        page, index, total = await _gmail_page(new_tab=False)
        body_text = ""
        with contextlib.suppress(Exception):
            body_text = await browser_tools._page_text(page, '[role="main"]')
        if max_chars > 0 and len(body_text) > max_chars:
            body_text = body_text[:max_chars] + "..."

        summary = await _page_summary(page, index, total)
        summary.update({"body_text": body_text})
        return summary

    @mcp.tool()
    async def gmail_create_draft(
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        new_tab: bool = True,
    ) -> dict[str, Any]:
        """Create a Gmail draft in the shared browser session without sending it."""
        page, index, total = await _gmail_page(new_tab=new_tab)

        compose_candidates = [
            page.get_by_role("button", name=re.compile(r"^Compose$", re.I)),
            page.get_by_text(re.compile(r"Compose", re.I), exact=True),
        ]
        if not await _click_first(compose_candidates):
            await page.keyboard.press("c")
        with contextlib.suppress(Exception):
            await page.wait_for_timeout(1000)

        to_candidates = [
            page.get_by_role("textbox", name=re.compile(r"^To$", re.I)),
            page.get_by_label(re.compile(r"^To$", re.I)),
            page.locator('input[aria-label*="To"]'),
        ]
        if not await _fill_first(to_candidates, to):
            raise RuntimeError("Could not find the Gmail To field.")

        if cc.strip():
            await _click_first(
                [
                    page.get_by_role("button", name=re.compile(r"^Cc$", re.I)),
                    page.get_by_text(re.compile(r"^Cc$", re.I), exact=True),
                ]
            )
            await _fill_first(
                [
                    page.get_by_role("textbox", name=re.compile(r"^Cc$", re.I)),
                    page.get_by_label(re.compile(r"^Cc$", re.I)),
                    page.locator('input[aria-label*="Cc"]'),
                ],
                cc,
            )

        if bcc.strip():
            await _click_first(
                [
                    page.get_by_role("button", name=re.compile(r"^Bcc$", re.I)),
                    page.get_by_text(re.compile(r"^Bcc$", re.I), exact=True),
                ]
            )
            await _fill_first(
                [
                    page.get_by_role("textbox", name=re.compile(r"^Bcc$", re.I)),
                    page.get_by_label(re.compile(r"^Bcc$", re.I)),
                    page.locator('input[aria-label*="Bcc"]'),
                ],
                bcc,
            )

        subject_candidates = [
            page.get_by_role("textbox", name=re.compile(r"^Subject$", re.I)),
            page.get_by_label(re.compile(r"^Subject$", re.I)),
            page.locator('input[name="subjectbox"]'),
        ]
        if not await _fill_first(subject_candidates, subject):
            raise RuntimeError("Could not find the Gmail Subject field.")

        body_candidates = [
            page.locator('div[aria-label="Message Body"]'),
            page.locator('div[role="textbox"][aria-label*="Message Body"]'),
            page.get_by_role("textbox", name=re.compile(r"Message Body", re.I)),
        ]
        if not await _fill_first(body_candidates, body):
            body_locator = page.locator('div[aria-label="Message Body"]').first
            await body_locator.click(timeout=5_000)
            await page.keyboard.type(body)

        with contextlib.suppress(Exception):
            await page.wait_for_timeout(1000)

        summary = await _page_summary(page, index, total)
        summary.update(
            {
                "draft_open": True,
                "to": to,
                "cc": cc,
                "bcc": bcc,
                "subject": subject,
                "body_length": len(body),
            }
        )
        return summary

    @mcp.tool()
    async def gmail_send_current_draft(confirm: bool = False, new_tab: bool = False) -> dict[str, Any]:
        """Send the currently open Gmail draft. Requires confirm=True."""
        return await _gmail_send_current_draft_impl(confirm=confirm, new_tab=new_tab)

    @mcp.tool()
    async def calendar_open(view: str = "week", new_tab: bool = True) -> dict[str, Any]:
        """Open Google Calendar in the shared browser session."""
        view = view.strip().lower() or "week"
        calendar_url = f"{CALENDAR_HOME_URL}/{view}"
        page, index, total = await _open_or_reuse_page(
            calendar_url,
            ["calendar.google.com", "calendar"],
            new_tab=new_tab,
        )
        return await _page_summary(page, index, total)

    @mcp.tool()
    async def calendar_search_events(query: str, max_results: int = 10, new_tab: bool = True) -> dict[str, Any]:
        """
        Search Google Calendar and return the visible page text plus a compact item list.
        """
        raw_query = query.strip()
        if not raw_query:
            raise ValueError("query cannot be empty")

        page, index, total = await _calendar_page(new_tab=new_tab)
        search_candidates = [
            page.get_by_role("textbox", name=re.compile(r"Search", re.I)),
            page.get_by_label(re.compile(r"Search", re.I)),
            page.locator('input[placeholder*="Search"]'),
        ]
        if not await _fill_first(search_candidates, raw_query):
            raise RuntimeError("Could not find the Calendar search field.")
        await page.keyboard.press("Enter")
        with contextlib.suppress(Exception):
            await page.wait_for_timeout(1500)

        body_text = ""
        with contextlib.suppress(Exception):
            body_text = await browser_tools._page_text(page, "body")
        if max_results > 0 and len(body_text) > 12000:
            body_text = body_text[:12000] + "..."

        items = []
        seen: set[str] = set()
        for selector in ['[role="main"] [role="button"]', '[role="main"] [role="link"]', '[role="main"] [role="gridcell"]']:
            locator = page.locator(selector)
            try:
                count = await locator.count()
            except Exception:
                continue
            if not count:
                continue
            for item_index in range(min(count, max_results * 4 if max_results > 0 else count)):
                item = locator.nth(item_index)
                text = ""
                with contextlib.suppress(Exception):
                    text = (await item.inner_text(timeout=2_000)).strip()
                if not text:
                    continue
                normalized = " ".join(text.split())
                if normalized in seen or len(normalized) < 3:
                    continue
                seen.add(normalized)
                items.append({"index": len(items), "text": normalized[:500]})
                if len(items) >= max_results:
                    break
            if items:
                break

        summary = await _page_summary(page, index, total)
        summary.update(
            {
                "query": raw_query,
                "items": items,
                "body_text": body_text,
            }
        )
        return summary

    @mcp.tool()
    async def calendar_create_event(
        title: str,
        start: str,
        end: str,
        description: str = "",
        location: str = "",
        timezone_name: str = "Australia/Sydney",
        auto_save: bool = True,
        new_tab: bool = True,
    ) -> dict[str, Any]:
        """Open a prefilled Google Calendar event editor and optionally save it."""
        event_url = _calendar_event_url(
            title=title,
            start=start,
            end=end,
            description=description,
            location=location,
            timezone_name=timezone_name,
        )
        page, index, total = await _open_or_reuse_page(
            event_url,
            ["calendar.google.com", "eventedit"],
            new_tab=new_tab,
        )
        with contextlib.suppress(Exception):
            await page.wait_for_timeout(1500)

        saved = False
        if auto_save:
            save_candidates = [
                page.get_by_role("button", name=re.compile(r"^Save$", re.I)),
                page.get_by_text(re.compile(r"^Save$", re.I), exact=True),
            ]
            saved = await _click_first(save_candidates)
            if saved:
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                with contextlib.suppress(Exception):
                    await page.wait_for_timeout(1200)

        summary = await _page_summary(page, index, total)
        summary.update(
            {
                "event_url": event_url,
                "saved": saved,
                "title": title,
                "start": start,
                "end": end,
                "description": description,
                "location": location,
                "timezone_name": timezone_name,
            }
        )
        return summary
