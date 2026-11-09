# Resume Bot

Tailors a resume to a job description and saves a one-page Word file.

## Files
- `profile.example.json` - a template. Copy it to `profile.json` and fill in your own details.
- `profile.json` - every fact about you. The bot can only use what is in here. Ignored by git, so it never gets pushed.
- `resume_bot.py` - the bot.
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
python resume_bot.py job.txt              # AI mode: picks, reorders, and rewords
python resume_bot.py job.txt --no-ai      # offline: picks and reorders only (free, no key)
python resume_bot.py                      # paste a posting, then type END
python resume_bot.py job.txt --label Acme_SOC_Intern
```

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
