"""
browser.py — controls a real Chrome window with Playwright.

WHY A SEPARATE CHROME PROFILE?
We launch Chrome with its own profile folder (browser_profile/). The first time,
you log in to Handshake / Indeed / etc. in that window yourself. Playwright saves
the cookies in that folder, so next run you're still logged in. Your normal
Chrome profile and passwords are never touched (and Chrome blocks automating
your main profile anyway).

The agent never types your passwords — you do the logging in.
"""

from __future__ import annotations

from contextlib import contextmanager

PROFILE_DIR = "browser_profile"


@contextmanager
def open_browser(profile_dir: str = PROFILE_DIR, headless: bool = False):
    """Usage:
        with open_browser() as ctx:
            page = ctx.pages[0]
            page.goto("https://example.com")
    The window closes automatically when the `with` block ends.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        opts = dict(headless=headless, no_viewport=True, args=["--start-maximized"])
        try:
            ctx = p.chromium.launch_persistent_context(profile_dir, channel="chrome", **opts)  # your installed Chrome
        except Exception:
            ctx = p.chromium.launch_persistent_context(profile_dir, **opts)  # Playwright's bundled Chromium
        try:
            if not ctx.pages:
                ctx.new_page()
            yield ctx
        finally:
            ctx.close()


def current_page(ctx):
    """The tab you were last looking at (the most recently opened one)."""
    return ctx.pages[-1]


def read_page(page) -> tuple[str, list[dict]]:
    """Return (all visible text, list of links) for the page."""
    page.wait_for_load_state("domcontentloaded")
    text = page.inner_text("body")
    links = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(a => ({text: (a.innerText || a.getAttribute('aria-label') || '').trim(), href: a.href}))",
    )
    seen, clean = set(), []
    for l in links:
        if l["href"].startswith("http") and l["href"] not in seen:
            seen.add(l["href"])
            clean.append({"text": " ".join(l["text"].split())[:150], "href": l["href"]})
    return text, clean


def source_label(url: str) -> str:
    """'https://app.joinhandshake.com/...' -> 'capture:handshake'"""
    for name in ("handshake", "indeed", "linkedin", "glassdoor", "greenhouse", "lever", "workday"):
        if name in url.lower():
            return f"capture:{name}"
    return "capture:web"
