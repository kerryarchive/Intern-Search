# Resume Bot

Tailors a resume to a job description, saves a one-page Word file, scores it, and compares page layouts.

## Files
- `profile.example.json` - a template. Copy it to `profile.json` and fill in your own details.
- `profile.json` - every fact about you. The bot can only use what is in here. Ignored by git, so it never gets pushed.
- `resume_bot.py` - the bot.
- `scoring.py` - the resume and job-fit grader.
- `sample_job.txt` - a made-up posting for testing.
- `output/` - tailored resumes land here (also ignored by git).

## Setup (once)
```
pip install python-docx anthropic
cp profile.example.json profile.json      # Windows: copy profile.example.json profile.json
```
Set your API key (AI mode only):
- Mac/Linux: `export ANTHROPIC_API_KEY="your-key"`
- Windows PowerShell: `$env:ANTHROPIC_API_KEY="your-key"`

## Run
```
python resume_bot.py job.txt --no-ai                     # offline: picks and reorders only (free, no key)
python resume_bot.py job.txt                             # AI mode: also rewords bullets
python resume_bot.py job.txt --no-ai --layout compact    # choose a layout
python resume_bot.py job.txt --no-ai --compare-layouts   # build every layout and compare them
python resume_bot.py job.txt --label Acme_SOC_Intern     # name the output file
python resume_bot.py                                     # paste a posting, then type END
```

## Layouts
| Name | Look | Best for |
|---|---|---|
| `standard` | Calibri 10.5, projects before experience | a balanced default |
| `compact` | Calibri 10, narrow margins, all skill rows | showing the most skills |
| `classic` | Times New Roman 10.5, experience first | a traditional look |
| `projects` | Calibri 10, projects on top, 1 bullet per job | fitting the most projects |
| `roomy` | Calibri 11, wide margins | easy reading, least content |

The bot fills the page for you: it walks down your projects from most to least relevant and adds each
one that still fits. `--compare-layouts` prints how many projects and bullets fit in each layout, how
full the page is, and the score for each. Page fill is an estimate, so open the file in Word to confirm.
To make your own layout, copy one in the `LAYOUTS` section of `resume_bot.py` and change the numbers.

## Scoring (`scoring.py`)
Every run prints two grades and saves them to `output/..._score.txt`:
- **Resume score /100**: Impact /40 (action verbs, numbers, no filler or overused words),
  Presentation /20 (one page, contact info, sections, consistency), Competencies /40
  (analytical, communication, leadership, teamwork, initiative).
- **Job fit /100**: the share of the posting's tools and repeated keywords found on your resume,
  with lists of what matched and what is missing.

The categories follow VMock, but the formula is this bot's own, so the numbers will not match VMock.
Use them to compare one version of your resume against another.

## How it stays honest
1. The AI only sees `profile.json` (minus your contact info) and the job post.
2. Every bullet it writes must point to the bullet in `profile.json` it came from.
3. `check_plan()` rejects a reworded bullet and uses your original if it adds a number,
   or uses a word from the job post that appears nowhere in your profile.
4. Skills and courses must match `profile.json` exactly or they are removed.
5. Anything the job wants that you don't have is printed as "Job asks for, but your profile lacks".

## Updating your resume
Add a new project, job, skill, or course to `profile.json` and run the bot again.
Give each new bullet a unique `id` that starts with its project/job `id` (e.g. `inventory-app-3`).
