# Internship Agent

A Python agent that finds internships, checks how well they fit you, pre-fills
applications, and tracks everything in an Excel sheet — built so the AI **can't
slip made-up info into your tracker or your applications.**

| Command | What it does | Uses AI? |
|---|---|---|
| `search` | Pulls new internships from the [SimplifyJobs GitHub list](https://github.com/SimplifyJobs/Summer2027-Internships) + Greenhouse/Lever job boards | No |
| `capture` | Reads job results from the page you have open (Handshake, Indeed, any site) | Yes, verified |
| `score` | Rates each job 0–10 against your resume, with reasons | Yes, verified |
| `list` | Shows your tracker, best fit first | No |
| `prefill <ID>` | Opens the job and fills the form from `profile.yaml` | Only to match labels |
| `mark <ID> <Status>` | Updates status (Applied, Interview, Offer…) | No |

**You always click Submit.** There is no code in this project that submits an application.

---

## Setup (one time)

**1. Get the code and a virtual environment**

```bash
git clone https://github.com/kerryarchive/Intern-Search.git
cd Intern-Search
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

**2. Claude API key** — create one at [console.anthropic.com](https://console.anthropic.com) (needs a few dollars of credit; Haiku costs fractions of a cent per job).

```bash
# Windows (PowerShell) — then open a NEW terminal
setx ANTHROPIC_API_KEY "sk-ant-..."
# Mac/Linux — add to ~/.zshrc or ~/.bashrc to make it permanent
export ANTHROPIC_API_KEY="sk-ant-..."
```

Never paste your key into a file you commit.

**3. Your profile**

```bash
cp profile.example.yaml profile.yaml     # Windows: copy profile.example.yaml profile.yaml
```

Fill in `profile.yaml` — contact info, school, resume path, and paste your resume
text under `background:`. It's in `.gitignore`, so it stays on your computer.

**4. Your search** — edit `config.yaml`: keywords, locations, and any company
job boards you want watched.

---

## Daily workflow

```bash
python main.py search              # new jobs from GitHub + job boards -> internships.xlsx
python main.py capture             # Chrome opens -> log in to Handshake -> go to results -> press Enter
python main.py score --limit 10    # fit-score the newest 10
python main.py list                # see what's best
python main.py prefill a1b2c3d4    # fill an application, review it, YOU submit
python main.py mark a1b2c3d4 Interview
```

The first time Chrome opens, log in to Handshake / Indeed / LinkedIn yourself.
Logins are saved in the `browser_profile/` folder (separate from your normal
Chrome), so you only do it once.

Close `internships.xlsx` in Excel before running a command — Windows locks open files.

---

## Run the tests

```bash
python -m pytest
```

The tests feed the agent deliberately fake AI answers (invented jobs, wrong
locations, bogus evidence) and check that every one gets caught.

---

## Things to know

- **Handshake and Indeed don't allow bots** in their terms of service. That's why
  `capture` only reads a page *you* opened, one page at a time, while you're at
  the keyboard — it doesn't crawl. Use it lightly; if a site shows a CAPTCHA,
  solve it yourself or skip that site.
- **Sensitive questions are never auto-filled**: work authorization/sponsorship,
  demographics, salary, passwords. Same for essay questions and checkboxes.
- New year? Change `simplify_repo` in `config.yaml` to the next `SummerYYYY-Internships` repo.

How the code works, file by file: **[HOW_IT_WORKS.md](HOW_IT_WORKS.md)**
