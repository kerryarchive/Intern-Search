"""
Tests — run with:  python -m pytest

They use a FakeLLM that returns canned answers (including deliberately
hallucinated ones), so they run offline, cost nothing, and prove the
verification layer catches made-up data.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import extract, filters, grounding, prefill, score
from agent.models import Listing
from agent.sources import simplify
from agent.tracker import Tracker


class FakeLLM:
    def __init__(self, answer: dict):
        self.answer = answer

    def call_tool(self, **kwargs):
        return self.answer


# ------------------------------------------------------------- grounding
def test_appears_in_ignores_case_whitespace_and_smart_quotes():
    page = "Cybersecurity  Analyst\n Intern – Delta’s  SOC team"
    assert grounding.appears_in("cybersecurity analyst intern - delta's soc", page)
    assert not grounding.appears_in("Senior Engineer", page)
    assert not grounding.appears_in("a", page)   # too short to count as proof


# ------------------------------------------------------------- extraction
PAGE = """Search results
Security Analyst Intern
Delta Air Lines · Atlanta, GA
IT Support Intern
Home Depot · Atlanta, GA"""
LINKS = [{"text": "Security Analyst Intern", "href": "https://jobs.example.com/delta/1"},
         {"text": "IT Support Intern", "href": "https://jobs.example.com/hd/2"}]


def test_extract_keeps_real_jobs_and_drops_hallucinations():
    llm = FakeLLM({"jobs": [
        {"company": "Delta Air Lines", "title": "Security Analyst Intern", "location": "Atlanta, GA", "link_number": 0},
        {"company": "Home Depot", "title": "IT Support Intern", "location": "Remote", "link_number": 1},   # wrong location
        {"company": "Google", "title": "Security Engineer Intern", "location": "", "link_number": 0},   # invented job
        {"company": "Delta Air Lines", "title": "Security Analyst Intern", "location": "", "link_number": 99},  # bad link
    ]})
    r = extract.extract_listings(llm, PAGE, LINKS, "https://search.page", "capture:test")
    titles = [l.title for l in r.kept]
    assert "Security Engineer Intern" not in titles
    assert len(r.rejected) == 1 and "not found" in r.rejected[0][1]
    assert r.kept[0].url == "https://jobs.example.com/delta/1"
    assert r.kept[1].location == ""                        # "Remote" wasn't on the page -> blanked
    assert r.kept[2].url == "https://search.page"          # invalid link number -> falls back to page URL


# ---------------------------------------------------------------- scoring
JOB = "Requirements: Knowledge of Python. Experience with Splunk. Pursuing a degree in Information Systems."
BACKGROUND = "Skills: Python, SQL. Pursuing a BBA in Computer Information Systems."


def test_score_is_computed_from_verified_claims_only():
    llm = FakeLLM({"requirements": [
        {"requirement_quote": "Knowledge of Python", "status": "met", "background_quote": "Python"},
        {"requirement_quote": "Experience with Splunk", "status": "not_met", "background_quote": ""},
        {"requirement_quote": "degree in Information Systems", "status": "met",
         "background_quote": "certified in Splunk"},                              # fake evidence -> unclear
        {"requirement_quote": "5 years of AWS", "status": "not_met", "background_quote": ""},  # not in job -> dropped
    ]})
    fit = score.score_fit(llm, JOB, BACKGROUND)
    assert fit.met == ["Knowledge of Python"]
    assert fit.missing == ["Experience with Splunk"]
    assert fit.unclear == ["degree in Information Systems"]
    assert fit.dropped == 1
    assert fit.score == 5                                                         # 1 met of 2 checked


def test_score_blank_when_nothing_verifiable():
    fit = score.score_fit(FakeLLM({"requirements": []}), JOB, BACKGROUND)
    assert fit.score is None


# ---------------------------------------------------------------- sources
def test_simplify_json_skips_closed_postings():
    rows = [
        {"company_name": "Acme", "title": "Cyber Intern", "url": "https://a", "locations": ["Atlanta, GA"],
         "active": True, "is_visible": True, "date_posted": 1762300800},
        {"company_name": "Old", "title": "IT Intern", "url": "https://b", "locations": [], "active": False},
    ]
    out = simplify.parse_json(rows)
    assert [l.company for l in out] == ["Acme"]
    assert out[0].location == "Atlanta, GA"


README_SNIPPET = """
<table><thead><tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr></thead>
<tbody>
<tr><td><strong><a href="https://simplify.jobs/c/Zoox">Zoox</a></strong></td><td>Software Engineer Intern</td>
<td>Foster City, CA</td><td><div><a href="https://jobs.lever.co/zoox/1/apply"><img alt="Apply"></a>
<a href="https://simplify.jobs/p/1"><img alt="Simplify"></a></div></td><td>0d</td></tr>
<tr><td>↳</td><td>Security Intern</td><td>Remote</td><td><div><a href="https://jobs.lever.co/zoox/2/apply">x</a></div></td><td>1d</td></tr>
<tr><td>Closed Co</td><td>IT Intern</td><td>NYC</td><td>🔒</td><td>9d</td></tr>
</tbody></table>
"""


def test_simplify_readme_parser():
    out = simplify.parse_readme(README_SNIPPET)
    assert [(l.company, l.title) for l in out] == [("Zoox", "Software Engineer Intern"), ("Zoox", "Security Intern")]
    assert out[0].url == "https://jobs.lever.co/zoox/1/apply"


# ---------------------------------------------------------------- filters
def test_filters():
    cfg = {"must_include": ["intern"], "keywords": ["security", "IT"], "exclude": ["PhD"],
           "locations": ["Atlanta", "Remote"]}
    mk = lambda t, loc: Listing(company="X", title=t, url="u", source="s", location=loc)
    assert filters.matches(mk("Security Internship", "Atlanta, GA"), cfg)
    assert filters.matches(mk("IT Intern", "Remote"), cfg)
    assert not filters.matches(mk("Security Engineer", "Atlanta"), cfg)          # not an internship
    assert not filters.matches(mk("Marketing Intern", "Atlanta"), cfg)           # no keyword
    assert not filters.matches(mk("PhD Security Intern", "Atlanta"), cfg)        # excluded
    assert not filters.matches(mk("Security Intern", "Seattle, WA"), cfg)        # location


# ---------------------------------------------------------------- tracker
def test_tracker_dedupes_and_tracks_status(tmp_path):
    path = tmp_path / "t.xlsx"
    t = Tracker(path)
    jobs = [Listing("Acme", "Cyber Intern", "https://a", "simplify", "Atlanta")]
    assert len(t.add(jobs)) == 1
    assert len(t.add(jobs)) == 0                      # same job again -> skipped
    t.save()

    t2 = Tracker(path)                                # reopen from disk
    job_id = jobs[0].id
    assert t2.link(job_id) == "https://a"
    t2.mark(job_id, "applied")
    assert t2.find(job_id)["Status"] == "Applied"
    assert t2.find(job_id)["Date Applied"]
    with pytest.raises(ValueError):
        t2.mark(job_id, "ghosted")


# ---------------------------------------------------------------- prefill
PROFILE = {"contact": {"first_name": "Kai", "last_name": "Doe", "email": "k@x.com", "phone": "555"},
           "education": {"school": "State U", "major": "CIS"}, "resume_path": "/tmp/resume.pdf"}


def test_prefill_plan_never_touches_sensitive_or_essay_fields():
    fields = [
        {"key": "a", "type": "text", "label": "First Name *", "value": ""},
        {"key": "b", "type": "email", "label": "Email", "value": ""},
        {"key": "c", "type": "select", "label": "Will you require visa sponsorship?", "value": ""},
        {"key": "d", "type": "select", "label": "Gender", "value": ""},
        {"key": "e", "type": "textarea", "label": "Why do you want to work here?", "value": ""},
        {"key": "f", "type": "file", "label": "Resume/CV", "value": ""},
        {"key": "g", "type": "checkbox", "label": "I agree", "value": ""},
        {"key": "h", "type": "text", "label": "Homepage", "value": ""},       # 'age' inside a word: not sensitive
        {"key": "i", "type": "text", "label": "Favorite color", "value": ""},
    ]
    plan = prefill.decide(fields, prefill.flatten_profile(PROFILE), llm=None)
    assert plan["a"][0] == "first_name"
    assert plan["b"][0] == "email"
    assert plan["c"][0] is None and "sensitive" in plan["c"][1]
    assert plan["d"][0] is None
    assert plan["e"][0] is None
    assert plan["f"][0] == "resume_path"
    assert "g" not in plan
    assert "sensitive" not in plan["h"][1]
    assert plan["i"][0] is None


def test_prefill_ai_can_only_pick_allowed_field_names():
    fields = [{"key": "x", "type": "text", "label": "Institution attended (most recent)", "value": ""},
              {"key": "y", "type": "text", "label": "Something odd", "value": ""}]
    llm = FakeLLM({"matches": [{"key": "y", "profile_field": "ssn"},       # not an allowed field -> ignored
                               {"key": "zzz", "profile_field": "email"}]})  # not a real form key -> ignored
    plan = prefill.decide(fields, prefill.flatten_profile(PROFILE), llm=llm)
    assert plan["x"][0] == "school"           # matched by rule
    assert plan["y"][0] is None
    assert "zzz" not in plan
