"""
main.py — the command-line front door. Every feature is one command:

    python main.py search            pull new internships from GitHub list + job boards
    python main.py capture           read Handshake/Indeed/any page you're looking at
    python main.py score             AI fit-check for jobs that don't have a score yet
    python main.py list              show what's in your tracker
    python main.py prefill <ID>      open a job and fill the form from profile.yaml
    python main.py mark <ID> <STATUS>  e.g. mark a1b2c3d4 Interview

Read this file top to bottom: each `cmd_*` function is one command, and it just
glues together pieces from the agent/ folder.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from agent import filters
from agent.tracker import STATUSES, Tracker, TrackerLockedError

ROOT = Path(__file__).parent


# ------------------------------------------------------------------ helpers
def load_yaml(name: str, required: bool = True) -> dict:
    path = ROOT / name
    if not path.exists():
        if required:
            sys.exit(f"Missing {name}. Copy {name.replace('.yaml', '.example.yaml')} to {name} and fill it in.")
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def get_llm(cfg: dict):
    from agent.llm import Claude, LLMError
    try:
        return Claude(model=cfg.get("model", "claude-haiku-5-5"))
    except LLMError as e:
        sys.exit(str(e))


def save(tracker: Tracker) -> None:
    try:
        tracker.save()
    except TrackerLockedError as e:
        sys.exit(str(e))


def ask(prompt: str) -> str:
    return input(prompt).strip()


# ----------------------------------------------------------------- commands
def cmd_search(args, cfg):
    from agent.sources import ats, simplify

    found, errors = [], []
    if cfg.get("simplify_repo"):
        try:
            found += simplify.fetch(cfg["simplify_repo"])
        except Exception as e:
            errors.append(f"Simplify list: {e}")
    for token in cfg.get("greenhouse_boards") or []:
        try:
            found += ats.fetch_greenhouse(token)
        except Exception as e:
            errors.append(f"Greenhouse '{token}': {e}")
    for token in cfg.get("lever_companies") or []:
        try:
            found += ats.fetch_lever(token)
        except Exception as e:
            errors.append(f"Lever '{token}': {e}")

    matching = filters.apply(found, cfg.get("filters", {}))
    tracker = Tracker(cfg["tracker_file"])
    added = tracker.add(matching)
    save(tracker)

    print(f"Checked {len(found)} listings -> {len(matching)} match your filters -> {len(added)} new.")
    for l in added[:25]:
        print(f"  [{l.id}] {l.company} — {l.title} ({l.location or 'location n/a'})")
    if len(added) > 25:
        print(f"  ...and {len(added) - 25} more in {cfg['tracker_file']}")
    for e in errors:
        print(f"  ! {e}")


def cmd_capture(args, cfg):
    from agent.browser import current_page, open_browser, read_page, source_label
    from agent.extract import extract_listings

    llm = get_llm(cfg)
    tracker = Tracker(cfg["tracker_file"])
    with open_browser() as ctx:
        page = current_page(ctx)
        page.goto(args.url or "https://app.joinhandshake.com/stu/postings")
        print("In the Chrome window: log in if needed and open a page of job results.")
        while True:
            if ask("Press Enter to capture this page (or type q to quit): ").lower() == "q":
                break
            page = current_page(ctx)
            text, links = read_page(page)
            result = extract_listings(llm, text, links, page.url, source_label(page.url))
            added = tracker.add(result.kept)
            save(tracker)
            print(f"  AI found {len(result.kept) + len(result.rejected)} jobs; "
                  f"{len(result.kept)} verified on the page; {len(added)} new added.")
            for job, why in result.rejected:
                print(f"  ✗ dropped '{job.get('title')}' — {why}")
            print("  Go to the next page of results, then capture again.")


def cmd_score(args, cfg):
    from agent.browser import current_page, open_browser, read_page
    from agent.score import score_fit

    profile = load_yaml("profile.yaml")
    background = profile.get("background", "")
    if not background.strip():
        sys.exit("Add your resume text under 'background:' in profile.yaml first.")

    llm = get_llm(cfg)
    tracker = Tracker(cfg["tracker_file"])
    todo = [r for r in tracker.rows() if r.get("Fit Score") in (None, "")
            and r.get("Status") in ("Found", "Interested")][: args.limit]
    if not todo:
        print("Nothing to score.")
        return
    with open_browser() as ctx:
        page = current_page(ctx)
        for row in todo:
            print(f"Scoring [{row['ID']}] {row['Company']} — {row['Title']}")
            try:
                page.goto(tracker.link(row["ID"]), timeout=45_000)
                page.wait_for_timeout(2500)   # let JavaScript-heavy job pages finish rendering
                text, _ = read_page(page)
                fit = score_fit(llm, text, background)
            except Exception as e:
                print(f"  ! skipped: {e}")
                continue
            tracker.update(row["ID"], **{"Fit Score": fit.score, "Fit Notes": fit.notes()})
            save(tracker)   # save after each one so nothing is lost if you stop early
            print(f"  -> {fit.score if fit.score is not None else 'n/a'}/10  "
                  f"(met {len(fit.met)}, missing {len(fit.missing)}, unverified dropped {fit.dropped})")


def cmd_list(args, cfg):
    tracker = Tracker(cfg["tracker_file"])
    rows = tracker.rows(status=args.status)
    rows.sort(key=lambda r: (r.get("Fit Score") is None, -(r.get("Fit Score") or 0)))
    for r in rows:
        score = r.get("Fit Score")
        print(f"[{r['ID']}] {str(score) + '/10' if score is not None else '  -  '}  "
              f"{r.get('Status', ''):<10} {r['Company']} — {r['Title']}")
    print(f"{len(rows)} jobs.")


def cmd_mark(args, cfg):
    tracker = Tracker(cfg["tracker_file"])
    try:
        tracker.mark(args.id, args.status)
    except (KeyError, ValueError) as e:
        sys.exit(e.args[0])
    save(tracker)
    print(f"[{args.id}] -> {args.status}")


def cmd_prefill(args, cfg):
    from agent.browser import current_page, open_browser
    from agent.prefill import fill_page

    profile = load_yaml("profile.yaml")
    tracker = Tracker(cfg["tracker_file"])
    row = tracker.find(args.id)
    if not row:
        sys.exit(f"No job with ID {args.id}. Run `python main.py list` to see IDs.")
    llm = get_llm(cfg) if cfg.get("ai_field_matching", True) else None

    with open_browser() as ctx:
        page = current_page(ctx)
        page.goto(tracker.link(args.id))
        print(f"Opened {row['Company']} — {row['Title']}")
        print("Click 'Apply' / sign in until you see the form.")
        while True:
            if ask("Press Enter to fill this page (or type done): ").lower() == "done":
                break
            report = fill_page(current_page(ctx), profile, llm)
            for label, key in report.filled:
                print(f"  ✓ {label[:60]}  <- {key}")
            for label, why in report.left_for_you:
                print(f"  • {label[:60]}  ({why})")
            print("  Green = filled. Yellow = for you. Multi-page form? Go to the next page and fill again.")

        tracker.mark(args.id, "Pre-filled")
        if ask("Review everything, then YOU click Submit. Did you submit it? [y/N]: ").lower() == "y":
            tracker.mark(args.id, "Applied")
        save(tracker)
    print(f"Tracker updated: [{args.id}] {tracker.find(args.id)['Status']}")


# --------------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description="Internship search + tracking agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("search", help="find new internships from the GitHub list and job-board APIs")
    p = sub.add_parser("capture", help="read job results from the page open in Chrome (Handshake, Indeed...)")
    p.add_argument("--url", help="page to open first")
    p = sub.add_parser("score", help="AI fit-check against your background")
    p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("list", help="show tracked jobs, best fit first")
    p.add_argument("--status", choices=STATUSES)
    p = sub.add_parser("mark", help="change a job's status")
    p.add_argument("id")
    p.add_argument("status", help=" / ".join(STATUSES))
    p = sub.add_parser("prefill", help="open a job and fill its form from profile.yaml")
    p.add_argument("id")

    args = parser.parse_args()
    cfg = load_yaml("config.yaml")
    cfg.setdefault("tracker_file", "internships.xlsx")
    {"search": cmd_search, "capture": cmd_capture, "score": cmd_score, "list": cmd_list,
     "mark": cmd_mark, "prefill": cmd_prefill}[args.command](args, cfg)


if __name__ == "__main__":
    main()
