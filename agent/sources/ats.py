"""
ats.py — jobs straight from company job boards (Greenhouse and Lever).

Lots of companies host their careers page on Greenhouse or Lever. Both offer a
free, public, no-login JSON API for their job boards — they WANT these read.

How to find a company's "token":
  - Greenhouse: careers link looks like  boards.greenhouse.io/<token>  or
                job-boards.greenhouse.io/<token>
  - Lever:      careers link looks like  jobs.lever.co/<token>
Put the tokens in config.yaml.

These sources also give us the full job description, which `score` uses.
"""

from __future__ import annotations

import html
import re

import requests

from agent.models import Listing


def _strip_html(raw: str) -> str:
    text = html.unescape(raw or "")          # Greenhouse double-encodes its HTML
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def fetch_greenhouse(token: str, timeout: int = 30) -> list[Listing]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    out = []
    for job in resp.json().get("jobs", []):
        out.append(Listing(
            company=token,
            title=job.get("title", ""),
            url=job.get("absolute_url", ""),
            location=(job.get("location") or {}).get("name", ""),
            source="greenhouse",
            description=_strip_html(job.get("content", "")),
        ))
    return out


def fetch_lever(token: str, timeout: int = 30) -> list[Listing]:
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    out = []
    for job in resp.json():
        cats = job.get("categories") or {}
        out.append(Listing(
            company=token,
            title=job.get("text", ""),
            url=job.get("hostedUrl", ""),
            location=cats.get("location", "") or "",
            source="lever",
            description=job.get("descriptionPlain", "") or "",
        ))
    return out
