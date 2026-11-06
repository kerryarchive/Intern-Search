"""
models.py — the shape of one internship listing.

Every source (Simplify's GitHub list, Greenhouse, Lever, a Handshake page you
captured) turns its data into this same `Listing` object. That way the rest of
the program never has to care where a job came from.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Listing:
    company: str
    title: str
    url: str                      # where you'd go to apply
    source: str                   # "simplify", "greenhouse", "lever", "capture:handshake", ...
    location: str = ""
    date_found: str = field(default_factory=lambda: date.today().isoformat())
    description: str = ""         # full job text, if the source gave us one (used for scoring)

    @property
    def id(self) -> str:
        """A short, stable ID so the same job is never added to the sheet twice.

        We hash company + title + url. Same job tomorrow -> same ID -> skipped.
        """
        key = f"{self.company.strip().lower()}|{self.title.strip().lower()}|{self.url.strip()}"
        return hashlib.sha1(key.encode()).hexdigest()[:8]
