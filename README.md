# Job Application Automation Pipeline

Give it a job posting URL. It produces a tailored CV, a PDF, a Gmail **draft**
(never sent), and a tracking row in a Google Sheet.

```bash
python main.py "https://boards.greenhouse.io/acme/jobs/123456"
```

```
[1/7] Reading job posting ....... scrape company + title + description (Playwright)
[2/7] Tailoring resume .......... Anthropic API: reorder / rephrase existing bullets only
[3/7] Rendering PDF ............. python-docx + LibreOffice headless
[4/7] Finding contacts ......... Hunter.io (or Apollo): recruiter / hiring manager / dept head
[5/7] Creating Gmail DRAFT ..... short intro email + CV attached — never sent
[6/7] Logging to Google Sheet .. date, company, role, URL, contacts, status
```

It does **not** submit the application on the company's site, and it never sends
email. You review every draft and apply through the official posting yourself.

---

## What "tailoring" means here

The resume step is deliberately conservative. It treats your master resume as the
single source of truth and only:

- selects a subset of existing bullets relevant to the job,
- reorders them,
- lightly rephrases them (verb swaps, tightening), matching the job description's
  language where it stays truthful,
- keeps light-to-medium keyword overlap for ATS screens.

It will **not** invent metrics, employers, titles, tools or achievements. After
generation, a guardrail (`pipeline/resume.py`):

- flags any number/percentage in the output that isn't present anywhere in the
  master resume,
- flags any bullet whose closest match to a master line scores below a similarity
  floor (a possible fabrication),

and prints those warnings during the run. Review them before using the PDF.

---

## Setup

### 1. Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

Install **LibreOffice** for PDF rendering: <https://www.libreoffice.org/download/>
If `soffice` isn't on your PATH, set `SOFFICE_PATH` in `.env`.

### 2. Configure

```bash
cp .env.example .env
```

Fill in `.env` as you go — `.env`, `credentials/`, `output/` and `*.docx` are all
gitignored. Each credential is documented inline in `.env.example`:

| Credential | For | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | resume + email text | pay-as-you-go, cents per run |
| `HUNTER_API_KEY` | contacts | free tier: 25 searches/mo, returns emails |
| `APOLLO_API_KEY` | contacts (alt) | **search needs a paid plan**; free = 403 |
| Google OAuth client | Gmail draft + Sheet | one "Desktop app" client, `credentials/client_secret.json` |
| `LOG_SHEET_ID` | tracking | the ID (or full URL) of a Google Sheet you own |

Then authorise Google once:

```bash
python authorize.py
```

### 3. Add your master resume

Put a comprehensive `master-resume.docx` in the project root (or point
`MASTER_RESUME_PATH` at it). It should contain every role, project and bullet you
might ever want to draw on — headings, sub-headings (job/role lines) and bullet
lists. The tailoring step never goes beyond what's in this file.

---

## Usage

```
python main.py <job_url> [options]

--company NAME         override the scraped company name
--title TITLE          override the scraped job title
--domain example.com   company domain for the contact search (skips lookup)
--description FILE      use a local text file as the JD (skip scraping)
--skip-contacts        don't look up contacts
--skip-draft           don't create a Gmail draft
--skip-log             don't append to the sheet
--show-browser         visible browser (debug the scraper)
--dry-run              scrape + tailor + PDF only
```

Aggregator sites (Indeed, LinkedIn, Glassdoor) block automated browsers. For
those, paste the JD into a text file and use `--description file.txt --company …
--title …`. Direct ATS links (Greenhouse, Lever, Workday, most careers pages)
scrape fine.

Artifacts land in `output/<date>_<company>_<role>/`.

### Truthful corrections

If your master resume has an error (wrong degree title, a cert you don't hold),
fix it with:

- `RESUME_CONSTRAINTS` — free-text instruction added to the tailoring prompt
- `RESUME_REPLACEMENTS` — literal `old=>new` pairs applied after generation
- `RESUME_DROP_PATTERNS` — regex; matching skill/bullet/cert lines are removed

---

## Layout

```
main.py                 orchestrator / CLI
config.py               .env loading, fail-fast credential checks
authorize.py            one-time Google OAuth
pipeline/
  scraper.py            Playwright + JSON-LD JobPosting extraction
  resume.py             Anthropic tailoring + anti-fabrication guardrail + docx builder
  pdf.py                LibreOffice headless docx -> pdf
  contacts.py           Hunter / Apollo people lookup
  google_auth.py        shared OAuth (Gmail + Sheets)
  gmail_draft.py        draft creation (never sends) + de-duplication
  sheet_log.py          append / update tracking rows
```

## Terms of service

Fetches one posting per run with a normal user agent. Prefer employer careers
pages, keep volume low, and paste the JD yourself (`--description`) where a site
disallows scraping. Contact-provider usage is subject to Hunter's / Apollo's API
terms. Nothing is ever emailed automatically.

## License

MIT — see [LICENSE](LICENSE).
