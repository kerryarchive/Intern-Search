"""
scoring.py - grades a tailored resume, in the spirit of VMock.

Two separate grades:
  1. RESUME SCORE (0-100)  how strong the resume is on its own
       Impact        /40   action verbs, numbers, no filler, no repeated words, bullet length
       Presentation  /20   one page, complete contact info, sections, bullet count, consistency
       Competencies  /40   analytical, communication, leadership, teamwork, initiative
  2. JOB FIT (0-100)       how much of what the posting asks for shows up on the resume

This is this bot's own rubric. It borrows VMock's three categories but not VMock's
formula, so the numbers will not match VMock exactly. Use it to compare versions.
"""

import re

ACTION_VERBS = set("""achieved analyzed assessed audited authored automated built collaborated communicated
completed conducted configured coordinated created cut debugged decreased defined delivered deployed designed
developed documented drove engineered enhanced established evaluated executed expanded facilitated generated
guided identified implemented improved increased integrated investigated launched led leveraged maintained
managed mapped mentored modeled monitored negotiated optimized organized oriented performed planned presented
processed produced programmed published reduced researched resolved reviewed selected served solved
streamlined supervised supported tested trained translated validated wrote
deliver maintain leverage coordinate manage train mentor support lead process resolve serve monitor""".split())

FILLER = ["responsible for", "helped", "assisted with", "worked on", "duties included", "various",
          "in charge of", "participated in", "tasked with", "etc"]

COMPETENCIES = {
    "Analytical":    ["analy", "evaluat", "research", "model", "assess", "validat", "test", "audit", "identif", "mapp"],
    "Communication": ["present", "author", "wrote", "document", "report", "interview", "communicat", "publish"],
    "Leadership":    ["led ", "lead", "mentor", "train", "manag", "orient", "coordinat", "supervis", "selected by"],
    "Teamwork":      ["team", "collaborat", "cross-functional", "support", "peer", "staff"],
    "Initiative":    ["built", "design", "develop", "engineer", "creat", "implement", "deploy", "streamlin", "automat"],
}

COMMON_WORDS = set("""the and for with a an to of in on at by from during while using into as that this
new high all its their our your is are was were be been""".split())


def _bullets(plan):
    return [b["text"] for group in ("projects", "experience") for entry in plan[group] for b in entry["bullets"]]


def score_impact(bullets):
    """How strong are the bullets? Returns (points out of 40, list of tips)."""
    tips, n = [], max(len(bullets), 1)

    verb_start = [b for b in bullets if b.split()[0].lower().strip(",.:") in ACTION_VERBS]
    # a real number, not the digit in a name like HTML5 or SWE-055
    numbered = [b for b in bullets if re.search(r"(?<![A-Za-z\-])\d", b)]
    filler = [f for f in FILLER for b in bullets if re.search(r"\b" + re.escape(f) + r"\b", b.lower())]
    good_len = [b for b in bullets if 10 <= len(b.split()) <= 32]

    # Words used too often across the whole resume (first words count double: they stand out)
    counts = {}
    for b in bullets:
        tokens = [t for t in re.findall(r"[a-z]{4,}", b.lower()) if t not in COMMON_WORDS]
        for i, t in enumerate(tokens):
            counts[t] = counts.get(t, 0) + (2 if i == 0 else 1)
    overused = sorted([w for w, c in counts.items() if c >= 4], key=lambda w: -counts[w])

    points = 12 * len(verb_start) / n                       # 12: start with an action verb
    points += 12 * min(len(numbered) / n / 0.5, 1)          # 12: half or more of bullets have a number
    points += max(0, 6 - 2 * len(set(filler)))              # 6: no filler phrases
    points += max(0, 5 - len(overused))                     # 5: no overused words
    points += 5 * len(good_len) / n                         # 5: bullets are 10-32 words

    weak = [b for b in bullets if b not in verb_start]
    if weak:
        tips.append(f"{len(weak)} bullet(s) don't start with an action verb, e.g. \"{weak[0][:55]}...\"")
    if len(numbered) / n < 0.5:
        tips.append(f"Only {len(numbered)} of {len(bullets)} bullets have a number. Add real counts, sizes, "
                    "times or percentages (team size, pages, records, hours, customers per shift).")
    if filler:
        tips.append("Replace filler phrases: " + ", ".join(sorted(set(filler))))
    if overused:
        tips.append("Overused words: " + ", ".join(overused[:6]))
    if len(good_len) < len(bullets):
        tips.append(f"{len(bullets) - len(good_len)} bullet(s) are too short or too long (aim for 10-32 words).")
    return round(points), tips


def score_presentation(plan, profile, fits_one_page):
    """Is it clean and complete? Returns (points out of 20, tips)."""
    tips, points = [], 0
    bullets = _bullets(plan)

    if fits_one_page:
        points += 6
    else:
        tips.append("Runs past one page in this layout. Try --layout compact.")

    contact = " ".join(str(v) for v in profile["contact"].values())
    if "000-0000" in contact or "YOUR-HANDLE" in contact:
        tips.append("Contact line still has placeholders. Fix them in profile.json.")
    else:
        points += 4

    if plan["skills"] and plan["projects"] and plan["experience"] and plan["coursework"]:
        points += 4
    else:
        tips.append("A section is empty (skills, projects, experience or coursework).")

    if 8 <= len(bullets) <= 18 and all(1 <= len(e["bullets"]) <= 4
                                        for g in ("projects", "experience") for e in plan[g]):
        points += 3
    else:
        tips.append(f"{len(bullets)} bullets total. Aim for 8-18, with 1-4 per entry.")

    endings = {b.rstrip()[-1] == "." for b in bullets}
    if len(endings) == 1:
        points += 3
    else:
        tips.append("Some bullets end with a period and some don't. Pick one style.")
    return points, tips


def score_competencies(bullets):
    """Does the resume show the 5 soft skills recruiters look for? (points out of 40, tips, detail)"""
    tips, points, detail = [], 0, {}
    for name, stems in COMPETENCIES.items():
        hits = sum(1 for b in bullets if any(s in b.lower() + " " for s in stems))
        detail[name] = hits
        points += 8 if hits >= 3 else 6 if hits == 2 else 3 if hits == 1 else 0
        if hits < 3:
            tips.append(f"{hits} bullet(s) show {name.lower()}. Aim for 3 or more.")
    return points, tips, detail


def score_job_fit(plan, profile, job_text, words, tools):
    """What share of the posting's important words appear on the resume?
    `words` is the bot's word-splitting function; `tools` is its list of common tools."""
    shown = [profile["education"]["degree"], profile["education"]["concentration"]]
    shown += _bullets(plan) + plan["coursework"]
    for items in plan["skills"].values():
        shown += items
    by_id = {p["id"]: p for p in profile["projects"]}
    shown += [by_id[e["id"]]["title"] + " " + by_id[e["id"]]["stack"] for e in plan["projects"]]
    resume_text = " ".join(shown).lower()
    resume_words = set(words(resume_text))

    counts = {}
    for w in words(job_text):
        counts[w] = counts.get(w, 0) + 1
    job_lower = job_text.lower()

    def has_tool(tool, text):
        return re.search(r"(?<![a-z0-9])" + re.escape(tool) + r"(?![a-z0-9])", text)

    # Important = named tools in the posting, plus any other word it repeats.
    wanted_tools = [t for t in tools if has_tool(t, job_lower)]
    repeated = [w for w, c in sorted(counts.items(), key=lambda kv: -kv[1])
                if c >= 2 and len(w) > 3 and w not in wanted_tools][:20]

    matched = [t for t in wanted_tools if has_tool(t, resume_text)] + [w for w in repeated if w in resume_words]
    missing = [t for t in wanted_tools if not has_tool(t, resume_text)] + [w for w in repeated if w not in resume_words]
    total = len(matched) + len(missing)
    return (round(100 * len(matched) / total) if total else 0), matched, missing


def score_resume(plan, profile, job_text, fits_one_page, words, tools):
    bullets = _bullets(plan)
    impact, impact_tips = score_impact(bullets)
    pres, pres_tips = score_presentation(plan, profile, fits_one_page)
    comp, comp_tips, comp_detail = score_competencies(bullets)
    fit, matched, missing = score_job_fit(plan, profile, job_text, words, tools)
    return {"total": impact + pres + comp, "impact": impact, "presentation": pres, "competencies": comp,
            "competency_detail": comp_detail, "tips": impact_tips + pres_tips + comp_tips,
            "job_fit": fit, "matched": matched, "missing": missing}


def format_report(s):
    zone = "Good Job" if s["total"] >= 85 else "On Track" if s["total"] >= 65 else "Needs Work"
    fit_zone = "Strong match" if s["job_fit"] >= 70 else "Partial match" if s["job_fit"] >= 45 else "Weak match"
    lines = [
        "",
        f"RESUME SCORE   {s['total']}/100   ({zone})",
        f"  Impact         {s['impact']}/40",
        f"  Presentation   {s['presentation']}/20",
        f"  Competencies   {s['competencies']}/40   "
        + ", ".join(f"{k} {v}" for k, v in s["competency_detail"].items()) + "  (bullets showing each)",
        "",
        f"JOB FIT        {s['job_fit']}/100   ({fit_zone})",
        "  Matched: " + (", ".join(s["matched"]) or "none"),
        "  Missing: " + (", ".join(s["missing"]) or "none"),
    ]
    if s["tips"]:
        lines += ["", "HOW TO RAISE YOUR SCORE"] + [f"  - {t}" for t in s["tips"]]
    return "\n".join(lines)
