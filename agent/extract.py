"""
extract.py — turn a messy job-search page (Handshake, Indeed, a careers page)
into clean Listing rows, WITHOUT trusting the AI blindly.

Pipeline:
    page text + numbered links  ->  Claude (structured output)  ->  verify  ->  Listings

The verify step is the important part:
  * company and title must appear word-for-word in the page text, or the job is dropped
  * location must appear in the page text, or it's left blank
  * Claude never writes a URL. It picks a link NUMBER from the list we gave it,
    and we look up the real URL ourselves. It can't invent a link that way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.grounding import appears_in, keep_if_grounded
from agent.models import Listing

MAX_PAGE_CHARS = 60_000
MAX_LINKS = 400

SYSTEM = (
    "You extract internship/job postings from the text of a web page. "
    "Copy every value EXACTLY as it is written in the page text — do not fix, shorten, "
    "translate, or guess anything. If a value is not on the page, use an empty string. "
    "For the link, give the number of the link from the provided list that opens that "
    "specific posting, or null if none clearly does. Only list postings that are actually "
    "on the page; it is fine to return an empty list."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string", "description": "Exactly as written on the page"},
                    "title": {"type": "string", "description": "Exactly as written on the page"},
                    "location": {"type": "string", "description": "Exactly as written, or empty"},
                    "link_number": {"type": ["integer", "null"]},
                },
                "required": ["company", "title", "location", "link_number"],
            },
        }
    },
    "required": ["jobs"],
}


@dataclass
class ExtractResult:
    kept: list[Listing] = field(default_factory=list)
    rejected: list[tuple[dict, str]] = field(default_factory=list)   # (what the AI said, why we dropped it)


def build_prompt(page_text: str, links: list[dict]) -> str:
    link_lines = "\n".join(f"[{i}] {l['text'][:100]} -> {l['href']}" for i, l in enumerate(links[:MAX_LINKS]))
    return (
        f"PAGE TEXT:\n<<<\n{page_text[:MAX_PAGE_CHARS]}\n>>>\n\n"
        f"LINKS ON THE PAGE:\n{link_lines}\n\n"
        "Extract every internship/job posting shown on this page."
    )


def verify(raw_jobs: list[dict], page_text: str, links: list[dict],
           page_url: str, source: str) -> ExtractResult:
    """The trust boundary. Pure Python, no AI — so it can be unit-tested."""
    result = ExtractResult()
    for job in raw_jobs:
        company, title = job.get("company", ""), job.get("title", "")
        if not appears_in(title, page_text, min_len=4):
            result.rejected.append((job, "title not found on page"))
            continue
        if not appears_in(company, page_text):
            result.rejected.append((job, "company not found on page"))
            continue

        n = job.get("link_number")
        url = links[n]["href"] if isinstance(n, int) and 0 <= n < min(len(links), MAX_LINKS) else page_url

        result.kept.append(Listing(
            company=company.strip(),
            title=title.strip(),
            location=keep_if_grounded(job.get("location"), page_text),
            url=url,
            source=source,
        ))
    return result


def extract_listings(llm, page_text: str, links: list[dict], page_url: str, source: str) -> ExtractResult:
    data = llm.call_tool(
        system=SYSTEM,
        prompt=build_prompt(page_text, links),
        tool_name="record_jobs",
        tool_description="Record the job postings found on the page.",
        schema=SCHEMA,
        max_tokens=8000,
    )
    return verify(data.get("jobs", []), page_text, links, page_url, source)
