"""
resume_bot.py - tailors your resume to a job description, scores it, and tests layouts.

How it works (5 steps):
  1. LOAD     profile.json  -> every true fact about you lives there
  2. PLAN     decide what to show for THIS job (AI mode or offline mode)
  3. CHECK    throw out anything the AI wrote that isn't backed by profile.json
  4. BUILD    fit as many projects as the page allows and write a one-page .docx
  5. SCORE    grade the resume and how well it fits the job (see scoring.py)

Usage:
  python resume_bot.py job.txt --no-ai                     # offline mode, no API key needed
  python resume_bot.py job.txt                             # AI mode (needs ANTHROPIC_API_KEY)
  python resume_bot.py job.txt --no-ai --layout compact    # pick a layout
  python resume_bot.py job.txt --no-ai --compare-layouts   # build every layout and compare
  python resume_bot.py job.txt --label Acme_SOC_Intern     # name the output file
  python resume_bot.py                                     # paste the job description, then type END
"""

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from scoring import format_report, score_resume

HERE = Path(__file__).parent
MODEL = os.environ.get("RESUME_BOT_MODEL", "claude-sonnet-5-5")
MAX_COURSES = 6
FILL_LIMIT = 0.96       # stop adding content when the page is estimated to be this full

# ---------------------------------------------------------------- layouts
# Each layout is just a set of numbers. Add your own by copying one and changing it.
#   size / margin     body font size (points) and page margins (inches)
#   gap               blank space above each section heading (points)
#   char_width        average letter width as a share of the font size (used to estimate wrapping)
#   line_height       line height as a multiple of the font size
#   skill_rows        how many skill categories to show (the best-matching ones come first)
#   exp_bullets       most bullets to show per job (the first ones in profile.json are kept)
#   inline_extras     True = certifications/activities on one line instead of one bullet each
#   order             the order of the sections after Education
LAYOUTS = {
    "standard": {"font": "Calibri", "size": 10.5, "margin": 0.6, "gap": 7, "char_width": 0.475,
                 "line_height": 1.29, "skill_rows": 4, "exp_bullets": 2, "inline_extras": True,
                 "order": ["skills", "projects", "experience", "extras"]},
    "compact":  {"font": "Calibri", "size": 10, "margin": 0.5, "gap": 4, "char_width": 0.475,
                 "line_height": 1.29, "skill_rows": 6, "exp_bullets": 3, "inline_extras": True,
                 "order": ["skills", "projects", "experience", "extras"]},
    "classic":  {"font": "Times New Roman", "size": 10.5, "margin": 0.6, "gap": 7, "char_width": 0.44,
                 "line_height": 1.22, "skill_rows": 4, "exp_bullets": 3, "inline_extras": True,
                 "order": ["skills", "experience", "projects", "extras"]},
    "projects": {"font": "Calibri", "size": 10, "margin": 0.5, "gap": 4, "char_width": 0.475,
                 "line_height": 1.29, "skill_rows": 3, "exp_bullets": 1, "inline_extras": True,
                 "order": ["projects", "skills", "experience", "extras"]},
    "roomy":    {"font": "Calibri", "size": 11, "margin": 0.7, "gap": 9, "char_width": 0.475,
                 "line_height": 1.29, "skill_rows": 3, "exp_bullets": 2, "inline_extras": True,
                 "order": ["skills", "projects", "experience", "extras"]},
}

STOPWORDS = set("""a an and are as at be by for from has have in is it its of on or that the
to with will you your our we they this their can may must should who what when all any
work working role team teams job position including such etc able strong ability experience
skills knowledge preferred required requirements responsibilities qualifications candidate
intern internship student students program summer fall spring year years degree currently
pursuing bachelor related field about more other not but help assist alongside looking
across against using well also within through both each them than into over under""".split())

# Tools employers often ask for. Used to tell you what the job wants that your profile lacks.
COMMON_TOOLS = ["splunk", "siem", "wireshark", "nmap", "linux", "windows server", "active directory",
                "powershell", "bash", "aws", "azure", "gcp", "java", "c++", "c#", "git", "github",
                "jira", "servicenow", "tableau", "power bi", "nist", "iso 27001", "soc 2", "owasp",
                "tcp/ip", "firewall", "burp suite", "metasploit", "kali", "docker", "kubernetes",
                "security+", "network+", "comptia", "crowdstrike", "edr", "mitre att&ck",
                "pandas", "react", "node", "rest api", "etl", "snowflake", "excel", "sql", "python"]


# ---------------------------------------------------------------- helpers

def words(text):
    """Lowercase words in a piece of text, minus filler words."""
    result = []
    for w in re.findall(r"[a-z][a-z0-9+#/&.\-]*", text.lower()):
        w = w.strip(".-/")
        if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "is", "us")):
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


def as_bullets(item):
    return [{"source": b["id"], "text": b["text"]} for b in item["bullets"]]


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

    projects = sorted(profile["projects"], key=project_score, reverse=True)   # ALL projects, best first
    courses = sorted(profile["coursework"],
                     key=lambda c: score(words(" ".join(c["keywords"])) + words(c["name"]), job_counts),
                     reverse=True)[:MAX_COURSES]

    # Within each skill category, put the skills the job mentions first...
    skills = {}
    for category, items in profile["skills"].items():
        skills[category] = sorted(items, key=lambda s: score(words(s), job_counts), reverse=True)
    # ...and put the best-matching category on top.
    skills = dict(sorted(skills.items(),
                         key=lambda kv: sum(score(words(s), job_counts) for s in kv[1]), reverse=True))

    return {
        "skills": skills,
        "projects": [{"id": p["id"], "bullets": as_bullets(p)} for p in projects],
        "experience": [{"id": e["id"], "bullets": as_bullets(e)} for e in profile["experience"]],
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

Rank every project that is at all relevant, most relevant first (the bot fits as many as the
page allows, so order matters). Pick at most {max_courses} courses.
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
    prompt = PROMPT.format(max_courses=MAX_COURSES, profile=json.dumps(shareable, indent=1), job=job_text)

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from your environment
    reply = client.messages.create(model=MODEL, max_tokens=4000,
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

    # Experience is never optional: if the AI left a job out, put it back.
    shown = {e["id"] for e in clean["experience"]}
    for e in profile["experience"]:
        if e["id"] not in shown:
            clean["experience"].append({"id": e["id"], "bullets": as_bullets(e)})
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

class ResumeDoc:
    """Writes the Word file and, at the same time, keeps a running ESTIMATE of how much
    of the page is used (self.used, in points; 72 points = 1 inch). The estimate is how
    the bot knows how many projects fit without opening Word."""

    def __init__(self, layout):
        self.L = layout
        self.doc = Document()
        section = self.doc.sections[0]
        section.page_width, section.page_height = Inches(8.5), Inches(11)
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            setattr(section, side, Inches(layout["margin"]))
        self.width_in = 8.5 - 2 * layout["margin"]
        self.page_pt = (11 - 2 * layout["margin"]) * 72
        self.line_pt = layout["size"] * layout["line_height"]
        self.used = 0.0

        normal = self.doc.styles["Normal"]
        normal.font.name = layout["font"]
        normal.font.size = Pt(layout["size"])
        normal.paragraph_format.space_after = Pt(0)
        normal.paragraph_format.space_before = Pt(0)

    def _count(self, text, indent_in=0.0, extra_pt=0.0, size=None):
        """Add this paragraph's estimated height to the running total."""
        size = size or self.L["size"]
        chars_per_line = (self.width_in - indent_in) * 72 / (size * self.L["char_width"])
        lines = max(1, math.ceil(len(text) / chars_per_line))
        self.used += lines * size * self.L["line_height"] + extra_pt

    def name_block(self, contact):
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(contact["name"])
        run.bold = True
        run.font.size = Pt(self.L["size"] + 7.5)
        self._count(contact["name"], size=self.L["size"] + 7.5)
        text = " | ".join(v for v in (contact.get("location"), contact.get("phone"), contact.get("email"),
                                      contact.get("linkedin"), contact.get("github")) if v)
        self.doc.add_paragraph(text).alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._count(text)

    def heading(self, text):
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = Pt(self.L["gap"])
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run(text.upper())
        run.bold = True
        run.font.size = Pt(self.L["size"] + 0.5)
        borders = OxmlElement("w:pBdr")            # thin line under the heading
        bottom = OxmlElement("w:bottom")
        for key, val in (("val", "single"), ("sz", "6"), ("space", "1"), ("color", "000000")):
            bottom.set(qn("w:" + key), val)
        borders.append(bottom)
        p._p.get_or_add_pPr().append(borders)
        self._count(text, extra_pt=self.L["gap"] + 6)

    def row(self, bold, plain="", right="", gap=3):
        """One line with text on the left and a date on the right."""
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = Pt(gap)
        p.paragraph_format.tab_stops.add_tab_stop(Inches(self.width_in), WD_TAB_ALIGNMENT.RIGHT)
        if bold:
            p.add_run(bold).bold = True
        if plain:
            p.add_run(plain)
        if right:
            p.add_run("\t" + right)
        self._count(bold + plain + "   " + right, extra_pt=gap)

    def labeled(self, label, text):
        p = self.doc.add_paragraph()
        p.add_run(label).bold = True
        p.add_run(text)
        self._count(label + text)

    def bullet(self, text):
        p = self.doc.add_paragraph(text, style="List Bullet")
        p.paragraph_format.left_indent = Inches(0.25)
        self._count(text, indent_in=0.25)

    @property
    def fill(self):
        return self.used / self.page_pt

    @property
    def free_lines(self):
        return int((self.page_pt - self.used) / self.line_pt)


def build(plan, profile, layout):
    """Lay out everything in `plan` on the page. Returns the ResumeDoc."""
    r = ResumeDoc(layout)
    r.name_block(profile["contact"])

    ed = profile["education"]
    r.heading("Education")
    r.row(ed["school"], "", ed["location"])
    r.row("", f'{ed["degree"]} | Concentration: {ed["concentration"]} | GPA: {ed["gpa"]}', ed["graduation"], gap=0)
    if ed.get("honors"):
        r.labeled("Honors: ", ed["honors"])
    if plan["coursework"]:
        r.labeled("Relevant Coursework: ", ", ".join(plan["coursework"]))

    def skills():
        r.heading("Technical Skills")
        for category, items in plan["skills"].items():
            r.labeled(category + ": ", ", ".join(items))

    def projects():
        if not plan["projects"]:
            return
        r.heading("Projects")
        by_id = {p["id"]: p for p in profile["projects"]}
        for entry in plan["projects"]:
            proj = by_id[entry["id"]]
            detail = f' | {proj["context"]} | {proj["stack"]}'
            if len(proj["title"] + detail + proj["date"]) > 100:     # too long for one line: drop the stack
                detail = f' | {proj["context"]}'
            r.row(proj["title"], detail, proj["date"])
            for b in entry["bullets"]:
                r.bullet(b["text"])

    def experience():
        r.heading("Experience")
        by_id = {e["id"]: e for e in profile["experience"]}
        for entry in plan["experience"]:
            job = by_id[entry["id"]]
            where = f' | {job["company"]}' + (f', {job["location"]}' if job["location"] else "")
            r.row(job["title"], where, job["dates"])
            for b in entry["bullets"]:
                r.bullet(b["text"])

    def extras():
        items = profile.get("certifications", []) + profile.get("activities", [])
        if not items:
            return
        r.heading("Certifications & Activities")
        if layout["inline_extras"]:
            r.labeled("", " | ".join(items))
        else:
            for item in items:
                r.bullet(item)

    sections = {"skills": skills, "projects": projects, "experience": experience, "extras": extras}
    for name in layout["order"]:
        sections[name]()
    return r


def fit_to_page(plan, profile, layout):
    """Fill the page: go down the ranked project list and add each one that still fits.
    If a whole project doesn't fit, try it with just its first bullet.
    Returns (ResumeDoc, the trimmed plan that is actually on the page)."""
    base = dict(plan,
                skills=dict(list(plan["skills"].items())[:layout["skill_rows"]]),
                experience=[dict(e, bullets=e["bullets"][:layout["exp_bullets"]]) for e in plan["experience"]])
    chosen = []
    r = build(dict(base, projects=chosen), profile, layout)
    for entry in plan["projects"]:
        for option in (entry, dict(entry, bullets=entry["bullets"][:1])):
            trial = build(dict(base, projects=chosen + [option]), profile, layout)
            if trial.fill <= FILL_LIMIT:
                chosen, r = chosen + [option], trial
                break
    return r, dict(base, projects=chosen)


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
    parser.add_argument("--layout", default="standard", choices=LAYOUTS, help="page layout to use")
    parser.add_argument("--compare-layouts", action="store_true", help="build one resume per layout and compare")
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
    base = f'{profile["contact"]["name"].replace(" ", "_")}_Resume_{label}'
    titles = {p["id"]: p["title"] for p in profile["projects"]}
    total = len(plan["projects"])

    names = list(LAYOUTS) if args.compare_layouts else [args.layout]
    print()
    if args.compare_layouts:
        print(f'{"LAYOUT":<10}{"FONT":<24}{"PROJECTS":<10}{"BULLETS":<9}{"PAGE USED":<11}{"ROOM LEFT":<12}SCORE  FIT')

    for name in names:
        layout = LAYOUTS[name]
        r, shown = fit_to_page(plan, profile, layout)           # shown = what actually made it onto the page
        n = len(shown["projects"])
        fits = r.fill <= 1.0
        on_page = {p["id"] for p in shown["projects"]}
        s = score_resume(shown, profile, job_text, fits, words, COMMON_TOOLS)
        out_path = out_dir / (f"{base}_{name}.docx" if args.compare_layouts else f"{base}.docx")
        r.doc.save(out_path)
        bullets = sum(len(e["bullets"]) for g in ("projects", "experience") for e in shown[g])

        if args.compare_layouts:
            font = f'{layout["font"]} {layout["size"]}pt'
            print(f'{name:<10}{font:<24}{f"{n} of {total}":<10}{bullets:<9}{f"{min(r.fill, 9.99):.0%}":<11}'
                  f'{f"~{max(r.free_lines, 0)} lines":<12}{s["total"]:<7}{s["job_fit"]}')
            continue

        print(f"Saved: {out_path}")
        print(f'Layout "{name}": fits {n} of {total} projects, page about {r.fill:.0%} full, '
              f"room for about {max(r.free_lines, 0)} more lines (estimate - check in Word).")
        print("Projects shown: " + "; ".join(titles[p["id"]] for p in shown["projects"]))
        if n < total:
            print("Left off for space: " + "; ".join(titles[p["id"]] for p in plan["projects"] if p["id"] not in on_page))
        print("Coursework shown: " + "; ".join(plan["coursework"]))
        if not fits:
            print("WARNING: this probably runs past one page. Try --layout compact.")
        report = format_report(s)
        print(report)
        (out_dir / f"{base}_score.txt").write_text(report.strip() + "\n", encoding="utf-8")

    if args.compare_layouts:
        print(f"\nSaved {len(names)} files in {out_dir}  (one per layout, named ..._<layout>.docx)")
        print("Page used / room left are estimates. Open the files in Word to confirm each is one page.")
    if plan["missing_keywords"]:
        print("\nJob asks for, but your profile lacks: " + ", ".join(plan["missing_keywords"]))
    for w in warnings:
        print("CHECK: " + w)
    if any(x in json.dumps(profile["contact"]) for x in ("000-0000", "YOUR-HANDLE")):
        print("REMINDER: fill in your phone / LinkedIn in profile.json")


if __name__ == "__main__":
    main()
