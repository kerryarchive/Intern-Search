"""
grounding.py — the anti-hallucination layer.

THE CORE IDEA
-------------
An LLM can't be *made* to never hallucinate. What we CAN do is never trust
anything it says unless we can check it against text we fetched ourselves.

So every time the AI hands us a value (a company name, a job title, a quote
from a job description), we look for that exact text in the source page.
  - Found it  -> keep it.
  - Not found -> throw it away. A blank cell is better than a made-up one.

This file has no AI in it at all. It's plain string checking, which is why
you can trust it.
"""

from __future__ import annotations

import re
import unicodedata

# Characters that look the same to a human but are different to a computer.
# Web pages are full of "smart quotes" and fancy dashes, so we flatten them.
_LOOKALIKES = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
}


def normalize(text: str) -> str:
    """Lowercase, flatten fancy punctuation, and squash all whitespace to single spaces."""
    text = unicodedata.normalize("NFKC", text or "")
    for fancy, plain in _LOOKALIKES.items():
        text = text.replace(fancy, plain)
    return re.sub(r"\s+", " ", text).strip().lower()


def appears_in(value: str, source: str, min_len: int = 2) -> bool:
    """True if `value` appears word-for-word (after normalizing) inside `source`.

    min_len stops the AI from "proving" something with a 1-letter match.
    """
    v = normalize(value)
    return len(v) >= min_len and v in normalize(source)


def keep_if_grounded(value: str | None, source: str) -> str:
    """Return the value if it's really in the source, otherwise an empty string."""
    if value and appears_in(value, source):
        return value.strip()
    return ""
