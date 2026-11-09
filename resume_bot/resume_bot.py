"""
resume_bot.py - tailors a resume to a job description.

How it works (4 steps):
  1. LOAD     profile.json  -> every true fact about you lives there
  2. PLAN     decide what to show for THIS job (AI mode or offline mode)
  3. CHECK    throw out anything the AI wrote that isn't backed by profile.json
  4. BUILD    write a one-page, ATS-friendly .docx

Usage:
  python resume_bot.py job.txt                 # AI mode (needs ANTHROPIC_API_KEY)
  python resume_bot.py job.txt --no-ai         # offline mode, no API key needed
  python resume_bot.py                         # paste the job description, then type END
  python resume_bot.py job.txt --label Acme_SOC_Intern
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

HERE = Path(__file__).parent
MODEL = os.environ.get("RESUME_BOT_MODEL", "claude-sonnet-5-5")

MAX_PROJECTS = 3        # keeps the resume to one page
MAX_COURSES = 6

STOPWORDS = set("""a an and are as at be by for from has have in is it its of on or that the
to with will you your our we they this their can may must should who what when all any
work working role team teams job position including such etc able strong ability experience
skills knowledge preferred required requirements responsibilities qualifications candidate
intern internship student students program summer fall spring year years degree currently
pursuing bachelor related field about more other not but""".split())

# Tools employers often ask for. Used only to tell you what the job wants
# that your profile does NOT have, so you know what to learn or add.
COMMON_TOOLS = ["splunk", "siem", "wireshark", "nmap", "linux", "windows server", "active directory",
                "powershell", "bash", "aws", "azure", "gcp", "java", "c++", "c#", "git", "github",
                "jira", "servicenow", "tableau", "power bi", "nist", "iso 27001", "soc 2", "owasp",
                "tcp/ip", "firewall", "burp suite", "metasploit", "kali", "docker", "kubernetes",
                "security+", "network+", "comptia", "crowdstrike", "edr", "mitre att&ck", "r",
                "pandas", "react", "node", "rest api", "etl", "snowflake", "excel", "sql", "python"]


# ---------------------------------------------------------------- helpers

def words(text):
    """Lowercase words in a piece of text, minus filler words."""
    result = []
    for w in re.findall(r"[a-z][a-z0-9+#/&.\-]*", text.lower()):
        w = w.strip(".-/")
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]                  # "scripts" and "script" count as the same word
        if len(w) > 1 and w not in STOPWORDS:
            result.append(w)
    return result


def profile_text(profile):
    """Everything in the profile as one lowercase string (used for fact-checking)."""
    return json.dumps(profile).lower()


def find_missing_tools(job_text, profile):
    """Tools the job mentions that appear nowhere in your profile."""
    job, mine = job_text.lower(), profile_text(profile)
    missing = []
    for tool in COMMON_TOOLS:
        pattern = r"(?<![a-z0-9])" + re.escape(tool) + r"(?![a-z0-9])"
        if re.search(pattern, job) and not re.search(pattern, mine):
            missing.append(tool)
    return missing


# ---------------------------------------------------------------- step 2a: offline plan

def score(item_words, job_counts):
    """How well does a profile item match the job? Counts shared words."""
    return sum(min(job_counts.get(w, 0), 3) for w in set(item_words))


def plan_offline(profile, job_text):
    """No AI: rank everything by keyword overlap with the job description.
    Bullets are copied word-for-word from profile.json, so nothing can be made up."""
    job_counts = {}
    for w in words(job_text):
        job_counts[w] = job_counts.get(w, 0) + 1

    def project_score(p):
        text = " ".join(b["text"] for b in p["bullets"]) + " " + p["stack"] + " " + p["title"]
        boost = 3 if p.get("technical") else 0      # hands-on technical work wins close calls
        return score(words(" ".join(p["keywords"])) + words(text), job_counts) + boost

    projects = sorted(profile["projects"], key=project_score, reverse=True)[:MAX_PROJECTS]
    courses = sorted(profile["coursework"], key=lambda c: score(words(" ".join(c["keywords"])) + words(c["name"]), job_counts),
                     reverse=True)[:MAX_COURSES]

    # Within each skill category, put the skills the job mentions first.
    skills = {}
    for category, items in profile["skills"].items():
        skills[category] = sorted(items, key=lambda s: score(words(s), job_counts), reverse=True)
    # ...and put the best-matching category on top.
    skills = dict(sorted(skills.items(),
                         key=lambda kv: sum(score(words(s), job_counts) for s in kv[1]), reverse=True))

    return {
        "skills": skills,
        "projects": [{"id": p["id"], "bullets": [{"source": b["id"], "text": b["text"]} for b in p["bullets"]]}
                     for p in projects],
        "experience": [{"id": e["id"], "bullets": [{"source": b["id"], "text": b["text"]} for b in e["bullets"]]}
                       for e in profile["experience"]],
        "coursework": [c["name"] for c in courses],
        "missing_keywords": find_missing_tools(job_text, profile),
    }


# ---------------------------------------------------------------- step 2b: AI plan

PROMPT = """You are tailoring a student's resume to a job description.

HARD RULES - the resume must stay 100% truthful:
- Use ONLY facts found in the PROFILE below. Never add a tool, skill, number, certification,
  employer, or result that is not in the PROFILE.
- You may reorder, select, and reword. Rewording means changing emphasis and using the job's
  vocabulary where the PROFILE genuinely supports it. It does not mean adding claims.
- Every bullet must name the id of the PROFILE bullet it came from in "source".
- Skills must be copied exactly as written in the PROFILE.
- If the job asks for something the PROFILE lacks, do NOT work it in. List it in "missing_keywords".

Pick at most {max_projects} projects (most relevant first) and at most {max_courses} courses.
Keep each bullet to one line or two short lines, starting with a strong past-tense verb
(present tense for the current job).

Return ONLY JSON in exactly this shape:
{{
  "company": "company name from the job post, or empty string",
  "role": "job title from the job post, or empty string",
  "skills": {{"<category from PROFILE>": ["<skill copied from PROFILE>", ...]}},
  "projects": [{{"id": "<project id>", "bullets": [{{"source": "<bullet id>", "text": "..."}}]}}],
  "experience": [{{"id": "<experience id>", "bullets": [{{"source": "<bullet id>", "text": "..."}}]}}],
  "coursework": ["<course name copied from PROFILE>", ...],
  "missing_keywords": ["..."]
}}

PROFILE:
{profile}

JOB DESCRIPTION:
{job}
"""


def plan_with_ai(profile, job_text):
    """Ask Claude to choose and reword. Contact info is NOT sent to the API."""
    import anthropic  # pip install anthropic

    shareable = {k: v for k, v in profile.items() if k != "contact"}
    prompt = PROMPT.format(max_projects=MAX_PROJECTS, max_courses=MAX_COURSES,
                           profile=json.dumps(shareable, indent=1), job=job_text)

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from your environment
    reply = client.messages.create(model=MODEL, max_tokens=3000,
                                   messages=[{"role": "user", "content": prompt}])
    raw = reply.content[0].text
    raw = raw[raw.find("{"): raw.rfind("}") + 1]   # drop anything around the JSON
    return json.loads(raw)


# ---------------------------------------------------------------- step 3: fact-check

def check_plan(plan, profile, job_text):
    """Never trust the AI's output directly. Returns (clean_plan, list_of_warnings).

    A reworded bullet is rejected (and your original wording used instead) if it:
      - points to a bullet id that doesn't exist
      - contains a number that isn't in your original bullet
      - uses a word from the job post that appears nowhere in your profile
    """
    warnings = []
    mine = profile_text(profile)
    job_words = set(words(job_text))
    original = {}                                   # bullet id -> original text
    for group in ("projects", "experience"):
        for item in profile[group]:
            for b in item["bullets"]:
                original[b["id"]] = b["text"]

    def clean_bullets(bullets, owner_id):
        cleaned = []
        for b in bullets:
            src, text = b.get("source"), (b.get("text") or "").strip()
            if src not in original or not src.startswith(owner_id):
                warnings.append(f"Dropped a bullet with no valid source: {text[:60]}")
                continue
            new_numbers = set(re.findall(r"\d+", text)) - set(re.findall(r"\d+", original[src]))
            borrowed = [w for w in set(words(text)) if w in job_words and w not in mine]
            if new_numbers or borrowed or not text:
                why = f"new numbers {sorted(new_numbers)}" if new_numbers else f"unsupported words {borrowed}"
                warnings.append(f"Kept your original wording for {src} ({why})")
                text = original[src]
            cleaned.append({"source": src, "text": text})
        return cleaned

    clean = {"company": plan.get("company", ""), "role": plan.get("role", "")}

    for group in ("projects", "experience"):
        known = {item["id"] for item in profile[group]}
        clean[group] = []
        for entry in plan.get(group, []):
            if entry.get("id") not in known:
                warnings.append(f"Dropped unknown {group} entry: {entry.get('id')}")
                continue
            bullets = clean_bullets(entry.get("bullets", []), entry["id"])
            if bullets:
                clean[group].append({"id": entry["id"], "bullets": bullets})
    clean["projects"] = clean["projects"][:MAX_PROJECTS]

    # Experience is never optional: if the AI left a job out, put it back.
    shown = {e["id"] for e in clean["experience"]}
    for e in profile["experience"]:
        if e["id"] not in shown:
            clean["experience"].append({"id": e["id"],
                                        "bullets": [{"source": b["id"], "text": b["text"]} for b in e["bullets"]]})
    order = [e["id"] for e in profile["experience"]]
    clean["experience"].sort(key=lambda e: order.index(e["id"]))

    # Skills and courses must match the profile exactly.
    clean["skills"] = {}
    for category, items in plan.get("skills", {}).items():
        allowed = profile["skills"].get(category, [])
        kept = [s for s in items if s in allowed]
        for s in items:
            if s not in allowed:
                warnings.append(f"Removed skill not in your profile: {s}")
        if kept:
            clean["skills"][category] = kept

    course_names = {c["name"] for c in profile["coursework"]}
    clean["coursework"] = [c for c in plan.get("coursework", []) if c in course_names][:MAX_COURSES]

    clean["missing_keywords"] = sorted({k.lower() for k in plan.get("missing_keywords", [])} |
                                       set(find_missing_tools(job_text, profile)))
    return clean, warnings


# ---------------------------------------------------------------- step 4: build the .docx

def add_heading(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(7)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(11)
    # thin line under the heading
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, val in (("val", "single"), ("sz", "6"), ("space", "1"), ("color", "000000")):
        bottom.set(qn("w:" + key), val)
    borders.append(bottom)
    p._p.get_or_add_pPr().append(borders)


def add_row(doc, left_bold, left_plain, right, width):
    """One line with text on the left and a date on the right."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT)
    p.add_run(left_bold).bold = True
    if left_plain:
        p.add_run(left_plain)
    if right:
        p.add_run("\t" + right)
    return p


def add_bullet(doc, text):
    p = doc.add_paragraph(text, style="List Bullet")
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.left_indent = Inches(0.25)


def build_docx(plan, profile, out_path):
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Inches(0.6))
    width = section.page_width - section.left_margin - section.right_margin

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(0)

    c = profile["contact"]
    name = doc.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = name.add_run(c["name"])
    run.bold = True
    run.font.size = Pt(18)
    line = doc.add_paragraph(" | ".join(v for v in (c["location"], c["phone"], c["email"],
                                                    c["linkedin"], c["github"]) if v))
    line.alignment = WD_ALIGN_PARAGRAPH.CENTER

    ed = profile["education"]
    add_heading(doc, "Education")
    add_row(doc, ed["school"], "", ed["location"], width)
    add_row(doc, "", f'{ed["degree"]} | Concentration: {ed["concentration"]} | GPA: {ed["gpa"]}',
            ed["graduation"], width).paragraph_format.space_before = Pt(0)
    if plan["coursework"]:
        p = doc.add_paragraph()
        p.add_run("Relevant Coursework: ").bold = True
        p.add_run(", ".join(plan["coursework"]))

    add_heading(doc, "Technical Skills")
    for category, items in plan["skills"].items():
        p = doc.add_paragraph()
        p.add_run(category + ": ").bold = True
        p.add_run(", ".join(items))

    add_heading(doc, "Projects")
    by_id = {p["id"]: p for p in profile["projects"]}
    for entry in plan["projects"]:
        proj = by_id[entry["id"]]
        detail = f' | {proj["context"]} | {proj["stack"]}'
        if len(proj["title"] + detail) > 95:        # too long for one line: drop the stack
            detail = f' | {proj["context"]}'
        add_row(doc, proj["title"], detail, proj["date"], width)
        for b in entry["bullets"]:
            add_bullet(doc, b["text"])

    add_heading(doc, "Experience")
    by_id = {e["id"]: e for e in profile["experience"]}
    for entry in plan["experience"]:
        job = by_id[entry["id"]]
        where = f' | {job["company"]}' + (f', {job["location"]}' if job["location"] else "")
        add_row(doc, job["title"], where, job["dates"], width)
        for b in entry["bullets"]:
            add_bullet(doc, b["text"])

    add_heading(doc, "Activities & Professional Development")
    for a in profile["activities"]:
        add_bullet(doc, a)

    doc.save(out_path)


# ---------------------------------------------------------------- main

def read_job(path):
    if path:
        return Path(path).read_text(encoding="utf-8")
    print("Paste the job description. When done, type END on its own line and press Enter:")
    lines = []
    for line in sys.stdin:
        if line.strip() == "END":
            break
        lines.append(line)
    return "".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Tailor your resume to a job description.")
    parser.add_argument("job", nargs="?", help="text file containing the job description")
    parser.add_argument("--no-ai", action="store_true", help="offline mode: reorder only, no rewording")
    parser.add_argument("--label", help="name used in the output file, e.g. Acme_SOC_Intern")
    parser.add_argument("--profile", default=str(HERE / "profile.json"))
    args = parser.parse_args()

    if not Path(args.profile).exists():
        sys.exit("No profile.json found. Copy profile.example.json to profile.json and fill in your details.")
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    job_text = read_job(args.job)
    if len(job_text.strip()) < 50:
        sys.exit("That job description looks empty. Paste the full posting.")

    if args.no_ai:
        raw_plan = plan_offline(profile, job_text)
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("Set ANTHROPIC_API_KEY first, or run with --no-ai.")
        raw_plan = plan_with_ai(profile, job_text)

    plan, warnings = check_plan(raw_plan, profile, job_text)

    label = args.label or "_".join(x for x in (plan["company"], plan["role"]) if x) \
        or (Path(args.job).stem if args.job else "Tailored")
    label = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
    out_dir = HERE / "output"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f'{profile["contact"]["name"].replace(" ", "_")}_Resume_{label}.docx'
    build_docx(plan, profile, out_path)

    titles = {p["id"]: p["title"] for p in profile["projects"]}
    print(f"\nSaved: {out_path}")
    print("Projects shown: " + "; ".join(titles[p["id"]] for p in plan["projects"]))
    print("Coursework shown: " + "; ".join(plan["coursework"]))
    if plan["missing_keywords"]:
        print("Job asks for, but your profile lacks: " + ", ".join(plan["missing_keywords"]))
    for w in warnings:
        print("CHECK: " + w)
    for placeholder in ("000-0000", "YOUR-HANDLE"):
        if placeholder in json.dumps(profile["contact"]):
            print("REMINDER: fill in your phone / LinkedIn in profile.json")
            break


if __name__ == "__main__":
    main()
