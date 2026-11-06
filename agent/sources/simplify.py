"""
simplify.py — internships from the SimplifyJobs GitHub list.

github.com/SimplifyJobs/Summer2027-Internships is a community list, updated
daily, with thousands of tech internships. It's public data on GitHub, so we can
read it directly: no login, no scraping a site that doesn't want bots.

We try two formats:
  1. listings.json — the structured data file the repo's own README is built from.
  2. README.md     — the HTML table you see on GitHub (fallback if #1 moves).

No AI is involved here: the data is already structured, so there's nothing to
hallucinate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser

import requests

from agent.models import Listing

REPO = "SimplifyJobs/Summer2027-Internships"   # change next year to Summer2028-...


def fetch(repo: str = REPO, timeout: int = 30) -> list[Listing]:
    raw = f"https://raw.githubusercontent.com/{repo}/dev"
    try:
        resp = requests.get(f"{raw}/.github/scripts/listings.json", timeout=timeout)
        resp.raise_for_status()
        return parse_json(resp.json())
    except (requests.RequestException, ValueError):
        # JSON missing or moved -> fall back to reading the README table.
        resp = requests.get(f"{raw}/README.md", timeout=timeout)
        resp.raise_for_status()
        return parse_readme(resp.text)


# ---------------------------------------------------------------- JSON format
def parse_json(rows: list[dict]) -> list[Listing]:
    out = []
    for r in rows:
        # Skip closed or hidden postings.
        if not r.get("active", True) or not r.get("is_visible", True):
            continue
        company, title, url = r.get("company_name", ""), r.get("title", ""), r.get("url", "")
        if not (company and title and url):
            continue
        posted = r.get("date_posted")
        found = (datetime.fromtimestamp(posted, tz=timezone.utc).date().isoformat()
                 if isinstance(posted, (int, float)) else None)
        listing = Listing(
            company=company,
            title=title,
            url=url,
            source="simplify",
            location="; ".join(r.get("locations") or []),
        )
        if found:
            listing.date_found = found
        out.append(listing)
    return out


# -------------------------------------------------------------- README format
class _TableParser(HTMLParser):
    """Walks the README's <table> rows and collects each cell's text + links.

    HTMLParser calls handle_starttag / handle_data / handle_endtag as it reads,
    so we keep a little bit of state about which <tr>/<td> we're inside.
    """

    def __init__(self):
        super().__init__()
        self.rows: list[list[dict]] = []
        self._row: list[dict] | None = None
        self._cell: dict | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = {"text": "", "links": []}
        elif tag == "a" and self._cell is not None:
            href = dict(attrs).get("href")
            if href:
                self._cell["links"].append(href)

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None and self._row is not None:
            self._cell["text"] = self._cell["text"].strip()
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def parse_readme(markdown: str) -> list[Listing]:
    parser = _TableParser()
    parser.feed(markdown)
    out, last_company = [], ""
    for cells in parser.rows:
        if len(cells) < 4:
            continue
        company = cells[0]["text"]
        # "↳" means "same company as the row above".
        if company in ("↳", ""):
            company = last_company
        last_company = company
        # The first link in the Application cell is the real apply link
        # (the second one is Simplify's own page). 🔒 rows have no link = closed.
        apply_links = [l for l in cells[3]["links"] if "simplify.jobs" not in l]
        if not apply_links:
            continue
        out.append(Listing(
            company=company,
            title=cells[1]["text"],
            location=cells[2]["text"],
            url=apply_links[0],
            source="simplify",
        ))
    return out
