"""
score.py — how well does a job fit YOUR background?

The AI is only allowed to do the "reading" part: list the job's requirements
and say whether your background meets each one, quoting both sides. Then:

  * a requirement quote not found in the job text  -> thrown out
  * a "met" claim whose quote isn't in your profile -> downgraded to "unclear"
  * the SCORE is computed by plain Python from what survived (met / checked * 10)

So the number in your spreadsheet is never an AI's gut feeling — it's arithmetic
on quotes we verified. If nothing could be verified, the score stays blank.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.grounding import appears_in

MAX_JOB_CHARS = 30_000

SYSTEM = (
    "You compare a job posting to a candidate's background. List the posting's concrete "
    "requirements and qualifications (skills, tools, coursework, year in school, etc.). "
    "For each one, copy the requirement EXACTLY from the posting, then decide: "
    "'met' only if the candidate background explicitly shows it (copy the exact supporting "
    "phrase from the background), 'not_met' if the background clearly lacks it, otherwise "
    "'unclear'. Never infer skills the background does not state."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement_quote": {"type": "string"},
                    "status": {"type": "string", "enum": ["met", "not_met", "unclear"]},
                    "background_quote": {"type": "string", "description": "Exact phrase from the background, or empty"},
                },
                "required": ["requirement_quote", "status", "background_quote"],
            },
        }
    },
    "required": ["requirements"],
}


@dataclass
class FitResult:
    score: int | None          # 0-10, or None if there wasn't enough verified evidence
    met: list[str]
    missing: list[str]
    unclear: list[str]
    dropped: int               # how many AI claims failed verification

    def notes(self) -> str:
        parts = []
        if self.met:
            parts.append("Have: " + "; ".join(self.met[:5]))
        if self.missing:
            parts.append("Missing: " + "; ".join(self.missing[:5]))
        if self.score is None:
            parts.append("Not enough verified info to score")
        return " | ".join(parts)


def verify(requirements: list[dict], job_text: str, background: str) -> FitResult:
    met, missing, unclear, dropped = [], [], [], 0
    for r in requirements:
        req = (r.get("requirement_quote") or "").strip()
        if not appears_in(req, job_text, min_len=4):
            dropped += 1
            continue
        status = r.get("status")
        if status == "met" and not appears_in(r.get("background_quote", ""), background, min_len=3):
            status = "unclear"     # claimed a match but couldn't show us where
        if status == "met":
            met.append(req)
        elif status == "not_met":
            missing.append(req)
        else:
            unclear.append(req)

    checked = len(met) + len(missing)
    score = round(10 * len(met) / checked) if checked else None
    return FitResult(score, met, missing, unclear, dropped)


def score_fit(llm, job_text: str, background: str) -> FitResult:
    data = llm.call_tool(
        system=SYSTEM,
        prompt=f"JOB POSTING:\n<<<\n{job_text[:MAX_JOB_CHARS]}\n>>>\n\nCANDIDATE BACKGROUND:\n<<<\n{background}\n>>>",
        tool_name="record_fit",
        tool_description="Record each requirement and whether the candidate meets it.",
        schema=SCHEMA,
    )
    return verify(data.get("requirements", []), job_text, background)
