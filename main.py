"""Job application automation pipeline.

Usage:
    python main.py <job_posting_url> [options]

Options:
    --company NAME       Override the scraped company name
    --title TITLE        Override the scraped job title
    --domain example.com Company domain for Apollo (skips domain lookup)
    --description FILE    Use this text file as the job description (skip scraping)
    --skip-contacts      Do not query Apollo
    --skip-draft         Do not create a Gmail draft
    --skip-log           Do not append to the Google Sheet
    --show-browser       Run Playwright with a visible browser window
    --dry-run            Scrape + tailor + PDF only; no Apollo, no Gmail, no Sheets

Nothing is ever sent: step 6 creates a Gmail DRAFT only.
"""
from __future__ import annotations

import argparse
import re
import sys
import traceback
from datetime import date
from pathlib import Path

from config import CONFIG


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "job"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Job application automation pipeline")
    p.add_argument("url", help="Job posting URL")
    p.add_argument("--company")
    p.add_argument("--title")
    p.add_argument("--domain")
    p.add_argument("--description", type=Path)
    p.add_argument("--skip-contacts", action="store_true")
    p.add_argument("--skip-draft", action="store_true")
    p.add_argument("--skip-log", action="store_true")
    p.add_argument("--show-browser", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.dry_run:
        args.skip_contacts = args.skip_draft = args.skip_log = True

    from pipeline.scraper import JobPosting, parse_html, scrape_job

    summary: dict[str, str] = {}
    contacts = []
    resume = None
    pdf_path = ""

    # --- 2. Scrape -------------------------------------------------------------
    print(f"[1/7] Reading job posting: {args.url}")
    if args.description and args.description.exists():
        job = JobPosting(
            url=args.url,
            company=args.company or "Unknown Company",
            title=args.title or "Unknown Role",
            description=args.description.read_text(encoding="utf-8"),
        )
    else:
        try:
            job = scrape_job(args.url, headless=not args.show_browser)
        except Exception as exc:  # noqa: BLE001
            print(f"      scrape failed: {exc}")
            print("      Re-run with --description FILE and --company/--title.")
            return 2
    if args.company:
        job.company = args.company
    if args.title:
        job.title = args.title
    print(f"      Company: {job.company}")
    print(f"      Title:   {job.title}")
    print(f"      Description: {len(job.description)} chars")
    if not job.is_usable():
        print("      WARNING: scrape looks thin - consider --description FILE.")

    out_dir = CONFIG.output_dir / (
        f"{date.today().isoformat()}_{_slug(job.company)}_{_slug(job.title)}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 3 + 4. Tailor resume + render PDF -----------------------------------
    try:
        CONFIG.require("anthropic", "master_resume")
        from pipeline.resume import build_docx, extract_master, tailor_resume

        print("[2/7] Tailoring resume via Anthropic API...")
        blocks = extract_master()
        resume = tailor_resume(job, blocks)
        docx_path = build_docx(
            resume, job, out_dir / f"{_slug(job.company)}_{_slug(job.title)}_resume.docx"
        )
        print(f"      Wrote {docx_path}")
        for w in resume.warnings:
            print(f"      GUARDRAIL: {w}")
        for n in resume.notes_for_human:
            print(f"      model note: {n}")

        print("[3/7] Rendering PDF (LibreOffice headless)...")
        from pipeline.pdf import docx_to_pdf

        pdf_path = docx_to_pdf(docx_path, out_dir)
        print(f"      Wrote {pdf_path}")
        summary["resume"] = pdf_path
    except Exception as exc:  # noqa: BLE001
        print(f"      resume/PDF step failed: {exc}")
        traceback.print_exc()
        if not (args.skip_draft and args.skip_log):
            return 3

    # --- 5. Contacts --------------------------------------------------------
    if not args.skip_contacts and CONFIG.contacts_provider.lower() != "none":
        try:
            from pipeline.contacts import find_contacts

            print(f"[4/7] Finding contacts ({CONFIG.contacts_provider})...")
            contacts = find_contacts(job.company, domain=args.domain, hint_url=args.url)
            for c in contacts:
                mark = "" if c.email_is_real else "  (email locked)"
                print(f"      - {c.name} | {c.title} | {c.archetype} [{c.source}]{mark}")
            summary["contacts"] = f"{len(contacts)} found"
        except Exception as exc:  # noqa: BLE001
            print(f"      contacts step failed: {exc}")
            summary["contacts"] = f"failed: {exc}"
    else:
        print("[4/7] Contacts: skipped")

    # --- 6. Gmail draft ---------------------------------------------------
    draft_info = None
    if not args.skip_draft and resume and pdf_path:
        try:
            CONFIG.require("anthropic", "google")
            from pipeline.gmail_draft import create_draft

            print("[5/7] Creating Gmail DRAFT (not sending)...")
            draft_info = create_draft(job, resume, contacts, pdf_path)
            print(f"      Draft {draft_info['draft_id']} - subject: {draft_info['subject']}")
            print(f"      To: {draft_info['to'] or '(no verified addresses - see draft note)'}")
            summary["draft"] = draft_info["draft_id"]
        except Exception as exc:  # noqa: BLE001
            print(f"      draft step failed: {exc}")
            summary["draft"] = f"failed: {exc}"
    else:
        print("[5/7] Gmail draft: skipped")

    # --- 7. Sheet log ---------------------------------------------------
    status = "draft created" if draft_info else "materials prepared"
    if not args.skip_log:
        try:
            CONFIG.require("google", "sheet")
            from pipeline.sheet_log import append_row

            print("[6/7] Logging to Google Sheet...")
            append_row(
                company=job.company,
                role=job.title,
                url=args.url,
                contacts=[f"{c.name} ({c.title})" for c in contacts],
                status=status,
            )
            summary["log"] = "appended"
        except Exception as exc:  # noqa: BLE001
            print(f"      log step failed: {exc}")
            summary["log"] = f"failed: {exc}"
    else:
        print("[6/7] Sheet log: skipped")

    # --- Summary -------------------------------------------------------
    print("\n[7/7] Done.")
    print(f"  Output folder: {out_dir}")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    if draft_info:
        print("\n  Review the draft in Gmail before sending. Nothing was sent.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
