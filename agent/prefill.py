"""
prefill.py — fill in an application form from YOUR profile.yaml.

SAFETY RULES (built into the code, not just the prompt):
  1. Every value typed into the form comes from profile.yaml. The AI never
     writes an answer. At most it helps decide WHICH profile field goes in
     WHICH box, and it can only pick from a fixed list of field names.
  2. Sensitive questions (demographics, work authorization/sponsorship,
     salary, passwords) are never touched. Those are your call, every time.
  3. Checkboxes, radio buttons and free-text essay questions are left for you.
  4. There is no code anywhere in this project that clicks Submit.

Filled boxes get a GREEN outline, boxes left for you get YELLOW.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Label patterns -> which profile field to use. Order matters: "first name"
# must be checked before plain "name".
RULES: list[tuple[str, str]] = [
    (r"first\s*name|given\s*name|preferred\s*name", "first_name"),
    (r"last\s*name|family\s*name|surname", "last_name"),
    (r"^\s*(full\s*)?name\s*\*?$|legal\s*name", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile|cell", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"portfolio|personal\s*(web)?site|website", "portfolio"),
    (r"\bcity\b", "city"),
    (r"\bstate\b|province", "state"),
    (r"school|university|college|institution", "school"),
    (r"degree", "degree"),
    (r"major|field\s*of\s*study|discipline|concentration", "major"),
    (r"graduat", "graduation_date"),
    (r"\bgpa\b|grade\s*point", "gpa"),
]

SENSITIVE = re.compile(
    r"gender|\bsex\b|\brace\b|ethnic|hispanic|latino|veteran|disabilit|sponsor|authori[sz]|\bvisa\b|"
    r"citizen|immigra|salary|compensation|pay\s*expect|pronoun|orientation|password|\bssn\b|"
    r"social\s*security|birth|\bage\b|criminal|convict",
    re.I,
)

SKIP_TYPES = {"checkbox", "radio", "password", "hidden", "submit", "button", "image", "reset"}

# JavaScript that runs inside the page: find every form field, figure out its
# human-readable label, and tag it with data-agent-field="agent-N" so Python
# can find it again.
COLLECT_FIELDS_JS = r"""
() => {
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    const r = el.getBoundingClientRect();
    if (type !== 'file' && (r.width === 0 || r.height === 0)) continue;  // invisible
    let label = '';
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) label = l.innerText; }
    if (!label) { const l = el.closest('label'); if (l) label = l.innerText; }
    if (!label && el.getAttribute('aria-labelledby')) {
      label = el.getAttribute('aria-labelledby').split(/\s+/)
        .map(id => (document.getElementById(id) || {}).innerText || '').join(' ');
    }
    if (!label) label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || el.id || '';
    const key = 'agent-' + (i++);
    el.setAttribute('data-agent-field', key);
    out.push({ key, type, label: label.replace(/\s+/g, ' ').trim().slice(0, 200), value: el.value || '' });
  }
  return out;
}
"""


@dataclass
class FillReport:
    filled: list[tuple[str, str]] = field(default_factory=list)    # (label, profile field)
    left_for_you: list[tuple[str, str]] = field(default_factory=list)  # (label, reason)


def flatten_profile(profile: dict) -> dict[str, str]:
    """Pull out the fields we're allowed to type into forms."""
    p = {k: str(v).strip() for k, v in (profile.get("contact") or {}).items() if v}
    p |= {k: str(v).strip() for k, v in (profile.get("education") or {}).items() if v}
    if "first_name" in p and "last_name" in p:
        p.setdefault("full_name", f"{p['first_name']} {p['last_name']}")
    if profile.get("resume_path"):
        p["resume_path"] = str(Path(profile["resume_path"]).expanduser())
    return p


def match_rule(label: str) -> str | None:
    for pattern, key in RULES:
        if re.search(pattern, label, re.I):
            return key
    return None


def decide(fields: list[dict], profile: dict[str, str], llm=None) -> dict[str, tuple[str | None, str]]:
    """For each field key -> (profile_field or None, reason). Pure logic, testable."""
    plan: dict[str, tuple[str | None, str]] = {}
    unknown = []
    for f in fields:
        label, ftype = f["label"], f["type"]
        if ftype in SKIP_TYPES:
            continue
        if SENSITIVE.search(label):
            plan[f["key"]] = (None, "sensitive — your decision")
        elif f.get("value") and ftype != "select":   # a dropdown always "has" a value (its first option)
            plan[f["key"]] = (None, "already filled")
        elif ftype == "file":
            is_resume = re.search(r"resume|résumé|\bcv\b", label, re.I)
            plan[f["key"]] = ("resume_path", "resume") if is_resume and "resume_path" in profile \
                else (None, "file upload — check it")
        elif ftype == "textarea":
            plan[f["key"]] = (None, "written answer — yours to write")
        elif (key := match_rule(label)) and key in profile:
            plan[f["key"]] = (key, "rule")
        else:
            unknown.append(f)

    if unknown and llm is not None:
        plan |= _ask_llm(unknown, profile, llm)
    for f in unknown:
        plan.setdefault(f["key"], (None, "not sure what this wants"))
    return plan


def _ask_llm(fields: list[dict], profile: dict[str, str], llm) -> dict:
    """Let Claude match unclear labels to a profile field NAME (never a value)."""
    allowed = sorted(k for k in profile if k != "resume_path")
    schema = {
        "type": "object",
        "properties": {"matches": {"type": "array", "items": {
            "type": "object",
            "properties": {"key": {"type": "string"},
                           "profile_field": {"type": "string", "enum": allowed + ["NONE"]}},
            "required": ["key", "profile_field"]}}},
        "required": ["matches"],
    }
    listing = "\n".join(f'{f["key"]}: "{f["label"]}" (type={f["type"]})' for f in fields)
    data = llm.call_tool(
        system="Match job-application form labels to profile field names. Use NONE unless the "
               "label clearly asks for exactly that field.",
        prompt=f"Form fields:\n{listing}\n\nProfile field names: {', '.join(allowed)}",
        tool_name="match_fields", tool_description="Record which profile field fits each form label.",
        schema=schema,
    )
    valid_keys = {f["key"] for f in fields}
    out = {}
    for m in data.get("matches", []):
        # Double-check the AI's answer is one of the options we offered.
        if m.get("key") in valid_keys and m.get("profile_field") in allowed:
            out[m["key"]] = (m["profile_field"], "AI-matched label")
    return out


def fill_page(page, profile_raw: dict, llm=None) -> FillReport:
    """Fill every frame on the current page (some forms live inside iframes)."""
    profile = flatten_profile(profile_raw)
    report = FillReport()
    for frame in page.frames:
        try:
            fields = frame.evaluate(COLLECT_FIELDS_JS)
        except Exception:
            continue   # frame from another site we can't read, or it navigated away
        labels = {f["key"]: f["label"] or "(no label)" for f in fields}
        types = {f["key"]: f["type"] for f in fields}
        for key, (pkey, reason) in decide(fields, profile, llm).items():
            sel = f'[data-agent-field="{key}"]'
            try:
                if pkey is None:
                    if reason != "already filled":
                        report.left_for_you.append((labels[key], reason))
                        _outline(frame, sel, "#eab308")
                    continue
                value = profile[pkey]
                if types[key] == "file":
                    frame.set_input_files(sel, value)
                elif types[key] == "select":
                    frame.select_option(sel, label=value)
                else:
                    frame.fill(sel, value)
                report.filled.append((labels[key], pkey))
                _outline(frame, sel, "#22c55e")
            except Exception as e:
                report.left_for_you.append((labels[key], f"couldn't fill ({type(e).__name__})"))
    return report


def _outline(frame, selector: str, color: str) -> None:
    try:
        frame.eval_on_selector(selector, f"el => {{ el.style.outline = '3px solid {color}'; }}")
    except Exception:
        pass
