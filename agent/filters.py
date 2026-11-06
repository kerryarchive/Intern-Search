"""
filters.py — keep only the listings you care about (rules from config.yaml).

Plain keyword matching, no AI. `\\b` in a regex means "word boundary", so the
keyword "SOC" matches "SOC Analyst Intern" but not "Associate".
"""

from __future__ import annotations

import re

from agent.models import Listing


def _has_any(text: str, words: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text, re.I) for w in words)


def matches(listing: Listing, cfg: dict) -> bool:
    title = listing.title
    # 1. Must look like an internship. Plain "contains" check here (not word
    #    boundary) so "intern" also catches "Internship".
    must = cfg.get("must_include", [])
    if must and not any(w.lower() in title.lower() for w in must):
        return False
    # 2. Must match at least one of your interest keywords (if you listed any)
    keywords = cfg.get("keywords", [])
    if keywords and not _has_any(title, keywords):
        return False
    # 3. Must not contain an excluded word
    if _has_any(title, cfg.get("exclude", [])):
        return False
    # 4. Location (empty list = anywhere)
    locations = cfg.get("locations", [])
    if locations and not _has_any(listing.location, locations):
        return False
    return True


def apply(listings: list[Listing], cfg: dict) -> list[Listing]:
    return [l for l in listings if matches(l, cfg)]
