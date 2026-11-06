# How It Works

Read this with the code open next to it. Suggested order: this page →
`main.py` → `agent/grounding.py` → `agent/extract.py` → everything else.

## The big picture

```
                         ┌───────────────────────── no AI ─────────────────────────┐
  SimplifyJobs GitHub ─┐ │                                                         │
  Greenhouse API ──────┼─┼─► Listing objects ─► filters.py ─► tracker.py (Excel)   │
  Lever API ───────────┘ │                                          ▲              │
                         └──────────────────────────────────────────┼──────────────┘
                                                                    │
  Handshake / Indeed page ─► browser.py ─► Claude ─► grounding check┘   (capture)
  Job description page ────► browser.py ─► Claude ─► grounding check ─► Fit Score (score)
  Application form ────────► browser.py ─► rules (+Claude picks field names) ─► profile.yaml values (prefill)
```

## Why this agent doesn't hallucinate (the important part)

You can't make an LLM *never* make things up. So we don't rely on that. We use
three rules, and each one is enforced by plain Python, not by asking the AI nicely:

**1. The AI never invents data — it copies, and we check the copy.**
When Claude reads a Handshake page and says "Delta Air Lines — Security Analyst
Intern", `extract.py` searches the page text for those exact words
(`grounding.appears_in`). Not there → the job is dropped and printed as
`✗ dropped`. A wrong location becomes a blank cell, not a wrong cell.

**2. The AI picks from options instead of writing free text.**
- Links: Claude gets a numbered list of the real links on the page and answers
  with a *number*. We look up the URL. It's impossible for it to invent a URL.
- Form filling: Claude can only answer with a field *name* from a fixed list
  (`enum` in the schema), and we double-check the answer is on that list. The
  actual value typed into the form always comes from your `profile.yaml`.

**3. Numbers are computed by code, not guessed by the AI.**
The fit score isn't "Claude, rate this 1–10". Claude lists requirements with
quotes; quotes that aren't really in the job post are thrown out; "you meet
this" claims that can't point to your resume text are downgraded; then Python
does `met / checked × 10`. No verified evidence → blank score.

Supporting tricks in `llm.py`: **forced tool calls** (structured JSON output
that matches a schema) and **temperature 0** (consistent answers).

## File by file

| File | Job | Key thing to learn |
|---|---|---|
| `main.py` | Command line. Each `cmd_*` function = one command | `argparse` subcommands |
| `agent/models.py` | `Listing` dataclass every source converts into | `@dataclass`, stable IDs via hashing |
| `agent/grounding.py` | Normalize text + "is this string really in the source?" | The trust boundary — tiny on purpose |
| `agent/sources/simplify.py` | Reads the SimplifyJobs GitHub list (JSON, falls back to README HTML) | `HTMLParser`, fallbacks |
| `agent/sources/ats.py` | Greenhouse + Lever public job-board APIs | Calling a REST API with `requests` |
| `agent/filters.py` | Keyword / location / exclude rules from `config.yaml` | Regex word boundaries `\b` |
| `agent/llm.py` | The only file that calls Claude | `tool_choice` forced structured output |
| `agent/extract.py` | Page text → verified listings | `verify()` is pure Python → unit-testable |
| `agent/score.py` | Job post + your background → verified fit score | Code computes the score, not the AI |
| `agent/tracker.py` | Excel read/write, dedupe, status dropdown | `openpyxl` |
| `agent/browser.py` | Opens Chrome with a saved-login profile; reads page text + links | Playwright persistent context |
| `agent/prefill.py` | Finds form fields, decides what goes where, fills, highlights | JavaScript inside the page via `evaluate` |
| `tests/test_agent.py` | Proves the checks catch fake AI output | Fakes/mocks; `tmp_path` |

## A pattern worth stealing: "decide" vs "do"

Look at `prefill.py`. `decide()` takes a list of fields and returns a plan — it
never touches a browser. `fill_page()` carries out the plan. Same in
`extract.py`: `verify()` is separate from `extract_listings()`.

Splitting *deciding* from *doing* is why the tests can run in a quarter second
with no browser and no API key. Use it in your own projects.

## Ideas to extend it (good practice / résumé material)

1. **Deadline reminders** — add a "Deadline" column and a command that lists jobs closing this week.
2. **More job boards** — Ashby has a public API too (`api.ashbyhq.com/posting-api/job-board/<token>`). Copy `fetch_lever` as a template.
3. **Streamlit dashboard** — read `internships.xlsx` with pandas and chart applications per week and response rate.
4. **Cover-letter drafts** — let Claude draft one, but run every sentence that mentions a skill through `grounding.appears_in` against your `background`, and flag anything it couldn't verify.
5. **Schedule `search`** daily with Windows Task Scheduler or cron.
