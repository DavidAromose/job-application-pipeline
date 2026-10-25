"""Read-only check of application replies in Gmail (opt-in: INBOX_ACCESS=true).

What it does
  * Searches ONLY for mail that looks like a reply to a job application - job-board
    and applicant-tracking senders, employer names from your Job Pipeline sheet, and
    standard phrases ("unfortunately", "interview", "application received"...).
  * Reads headers + Gmail's short snippet only - never full bodies or attachments.
  * Classifies each hit (REJECTED / CLOSED / INTERVIEW / NEXT_STEP / RECEIPT) and matches
    it to a row in the sheet by employer name.
  * Proposes sheet updates per application. Nothing is written unless you approve
    (check_inbox.py --apply).

What it can never do: send, delete, archive, label or otherwise change any email
(the gmail.readonly scope does not permit it).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta
from email.utils import parsedate_to_datetime

from googleapiclient.discovery import build

from config import CONFIG
from pipeline.google_auth import get_credentials
from pipeline.sheet_log import HEADER

REJECT_RE = re.compile(
    r"unfortunately|not been selected|not be progressing|not successful|regret to|"
    r"other (candidates|applicants)|proceed with other|decided to proceed|not to proceed|"
    r"unable to (offer|progress)|not (be )?taking your application further|"
    r"not moving forward|no longer (being )?considered|not shortlisted|won't be putting you",
    re.IGNORECASE,
)
CLOSED_RE = re.compile(
    r"closed your application|application (has been |is )?closed|we've closed",
    re.IGNORECASE,
)
# "Your application has been sent" style receipts. Checked BEFORE interview wording because
# NHS Jobs receipts say "they'll let you know if they want to invite you to interview".
SENT_RE = re.compile(
    r"(we've|we have) sent your application|application (is |has been )?(sent|submitted)|"
    r"successfully submitted|has been submitted|confirmation of receipt|"
    r"(your details|application) (have|has) been submitted",
    re.IGNORECASE,
)
INTERVIEW_RE = re.compile(
    r"invite you to (an? )?(\w+ )?interview|invited to (an? )?(\w+ )?interview|"
    r"arrange (an? )?(\w+ )?interview|schedule (an? )?(\w+ )?interview|"
    r"interview (date|invitation|slot|invite)|shortlisted for|"
    r"(telephone|phone|video) (screen|interview|call)|assessment cent(re|er)",
    re.IGNORECASE,
)
NEXT_RE = re.compile(
    r"next step|assessment|online tests?|complete (the|your) (form|application)|"
    r"move you forward|reference|right to work|dbs",
    re.IGNORECASE,
)
RECEIPT_RE = re.compile(
    r"(thank you for|thanks for|many thanks for) (applying|your (recent )?application|"
    r"choosing to apply|completing your application)|application received|"
    r"we have received your|excited to receive your application|thank you for applying",
    re.IGNORECASE,
)
# Job-alert / newsletter / social / marketing mail - never an answer to an application.
ALERT_RE = re.compile(
    r"^(our recommendation|new job|\d+ new |new jobs? similar|jobs? similar|i want to connect|"
    r"weekly digest|job alert|recommended jobs|jobs for you)|"
    r" is hiring| @ |new job opportunities|jobs? in (the )?uk|check your payslip|"
    r"newsletter|webinar|digest|unsubscribe|accepted your invitation|leetcode|contest|"
    r"tomorrows walk",
    re.IGNORECASE,
)
# Senders that carry application traffic (substring match on the From header).
APPLICATION_SENDERS = (
    "indeed", "reed.co.uk", "totaljobs", "cv-library", "cvlibrary", "jobsite", "glassdoor",
    "jobs.nhs.uk", "nhs.net", "workable", "greenhouse", "lever.co", "teamtailor",
    "smartrecruiters", "successfactors", "myworkday", "taleo", "icims", "apply.greater.jobs",
    "recruitment", "recruiting", "careers", "talent", "hr@",
)
# Personal / financial mail is never read into results, whatever it says.
PRIVATE_SENDERS = ("revolut", "monzo", "starling", "barclays", "hsbc", "lloyds", "natwest",
                   "santander", "nationwide", "halifax", "paypal", "wise.com", "klarna",
                   "hmrc", "gov.uk", "ukvi", "home office")
PRIVATE_RE = re.compile(r"you sent £|you received £|payment (received|sent)|your statement|"
                        r"one-time (pass)?code|verification code|password reset", re.IGNORECASE)
_ACTIVE = ("APPLIED", "READY TO APPLY", "FOLLOW-UP DUE", "INTERVIEW", "FOUND")
_PRIORITY = {"REJECTED": 0, "CLOSED": 1, "INTERVIEW": 2, "NEXT_STEP": 3, "RECEIPT": 4, "OTHER": 9}


@dataclass
class Reply:
    message_id: str
    date: str
    sender: str
    subject: str
    snippet: str
    kind: str  # REJECTED | CLOSED | INTERVIEW | NEXT_STEP | RECEIPT | OTHER
    employer: str = ""  # matched sheet employer, if any
    row: int = 0  # matched sheet row number (1-based), if any


def _classify(text: str) -> str:
    text = html.unescape(text)
    if REJECT_RE.search(text):
        return "REJECTED"
    if CLOSED_RE.search(text):
        return "CLOSED"
    if SENT_RE.search(text):
        return "RECEIPT"
    if INTERVIEW_RE.search(text):
        return "INTERVIEW"
    if NEXT_RE.search(text):
        return "NEXT_STEP"
    if RECEIPT_RE.search(text):
        return "RECEIPT"
    return "OTHER"


def _sheet_rows() -> list[tuple[int, dict]]:
    svc = build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)
    last = chr(ord("A") + len(HEADER) - 1)
    values = (
        svc.spreadsheets().values()
        .get(spreadsheetId=CONFIG.log_sheet_id, range=f"'{CONFIG.log_sheet_tab}'!A1:{last}300")
        .execute().get("values", [])
    )
    rows = []
    for n, row in enumerate(values[1:], start=2):
        d = {h: (row[i] if len(row) > i else "") for i, h in enumerate(HEADER)}
        rows.append((n, d))
    return rows


def _employer_tokens(name: str) -> list[str]:
    stop = {"ltd", "limited", "group", "the", "plc", "llp", "uk", "services", "practice", "centre"}
    name = re.sub(r"\(.*?\)", " ", name.lower())  # drop "(Swinton, Salford)" style qualifiers
    words = [w for w in re.findall(r"[a-z0-9&'-]+", name) if w not in stop and len(w) > 2]
    return words[:2]  # the distinctive leading words


def _has_word(text: str, word: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None


def _match_employer(subject: str, snippet: str, sender: str, rows: list[tuple[int, dict]]):
    """Whole-word match in subject/snippet, or the employer's name inside the sender's DOMAIN
    (never the local part, so 'noreply@...' cannot match an employer called Reply)."""
    text = f"{subject} {snippet}".lower()
    domain = re.sub(r"[.\-]", "", sender.lower().split("@")[-1].rstrip("> "))
    best, best_score = (0, None), -1
    for n, d in rows:
        if d["Status"] not in _ACTIVE:
            continue
        toks = _employer_tokens(d["Employer"])
        if not toks:
            continue
        joined = re.sub(r"[.\-]", "", "".join(toks))
        if not (all(_has_word(text, t) for t in toks) or (len(joined) >= 6 and joined in domain)):
            continue
        # Several rows can share an employer (e.g. two Priory or two Co-op roles): prefer the
        # row whose JOB TITLE words appear in the email.
        title_words = {w for w in re.findall(r"[a-z0-9]+", d["Job Title"].lower()) if len(w) > 3}
        score = sum(1 for w in title_words if _has_word(text, w))
        if d["Status"] == "FOUND":
            score -= 0.5  # a role never applied for is less likely to be what the email is about
        if score > best_score:
            best, best_score = (n, d), score
    return best if best[1] else (0, None)


def find_replies(days: int = 14) -> list[Reply]:
    if not CONFIG.inbox_access:
        raise RuntimeError(
            "Inbox access is off. Set INBOX_ACCESS=true in .env, then run "
            "`python authorize.py` once to grant read-only access."
        )
    rows = _sheet_rows()
    svc = build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)

    senders = " OR ".join(f"from:{s}" for s in APPLICATION_SENDERS)
    phrases = 'application OR applied OR "your CV" OR unfortunately OR interview OR vacancy OR shortlisted'
    employers = " OR ".join(
        f'"{" ".join(_employer_tokens(d["Employer"]))}"'
        for _, d in rows
        if d["Status"] in _ACTIVE and _employer_tokens(d["Employer"])
    )
    no_alerts = (
        "-from:revolut -from:monzo -from:paypal -from:hmrc -from:gov.uk "
        '-subject:"our recommendation" -subject:"new jobs" -subject:"new job opportunities" '
        '-subject:"is hiring" -subject:"i want to connect" -subject:"jobs similar" '
        "-subject:digest -subject:newsletter"
    )
    query = (
        f"newer_than:{days}d in:inbox (({senders}) OR ({phrases})"
        + (f" OR ({employers}))" if employers else ")")
        + f" {no_alerts}"
    )

    ids: list[str] = []
    page = None
    while len(ids) < 300:
        resp = svc.users().messages().list(userId="me", q=query, maxResults=50, pageToken=page).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page:
            break

    out: list[Reply] = []
    for mid in ids:
        m = (
            svc.users().messages()
            .get(userId="me", id=mid, format="metadata", metadataHeaders=["From", "Subject", "Date"])
            .execute()
        )
        h = {x["name"]: x["value"] for x in m.get("payload", {}).get("headers", [])}
        sender = h.get("From", "")
        subject = html.unescape(h.get("Subject", ""))
        snippet = html.unescape(m.get("snippet", ""))
        if any(p in sender.lower() for p in PRIVATE_SENDERS) or PRIVATE_RE.search(subject):
            continue  # banking / government / security mail - never surfaced
        if ALERT_RE.search(subject) or ALERT_RE.search(snippet[:80]):
            continue  # job-alert / social / newsletter mail, not a reply to an application
        kind = _classify(f"{subject} {snippet}")
        n, d = _match_employer(subject, snippet, sender, rows)
        if kind == "OTHER" and not d:
            continue  # unrelated mail that only matched a broad sender keyword
        try:
            date = parsedate_to_datetime(h.get("Date", "")).date().isoformat()
        except Exception:  # noqa: BLE001
            date = h.get("Date", "")[:16]
        out.append(Reply(mid, date, sender, subject, snippet, kind, d["Employer"] if d else "", n))
    return sorted(out, key=lambda r: r.date, reverse=True)


def propose_changes(replies: list[Reply]) -> list[dict]:
    """One proposed update per matched sheet row, based on the strongest evidence in the inbox."""
    rows = dict(_sheet_rows())
    by_row: dict[int, list[Reply]] = {}
    for r in replies:
        if r.row:
            by_row.setdefault(r.row, []).append(r)
    proposals = []
    for n, rs in by_row.items():
        cur = rows[n]["Status"]
        best = min(rs, key=lambda r: (_PRIORITY.get(r.kind, 9), r.date))
        receipts = sorted(r.date for r in rs if r.kind in ("RECEIPT", "NEXT_STEP", "INTERVIEW"))
        new = None
        if best.kind in ("REJECTED", "CLOSED") and cur in ("APPLIED", "READY TO APPLY", "FOLLOW-UP DUE", "FOUND"):
            new = "REJECTED"
        elif best.kind == "INTERVIEW" and cur in ("APPLIED", "READY TO APPLY", "FOLLOW-UP DUE"):
            new = "INTERVIEW"
        elif best.kind in ("RECEIPT", "NEXT_STEP") and cur in ("READY TO APPLY", "FOUND"):
            new = "APPLIED"  # inbox shows it was submitted
        if not new or new == cur:
            continue
        upd = {"Status": new, "Outcome": f"{best.kind} email {best.date}: {best.subject[:80]}"}
        if new == "APPLIED" and receipts:
            upd["Date Applied"] = receipts[0]
            upd["Follow-up Date"] = (_date.fromisoformat(receipts[0]) + timedelta(days=7)).isoformat()
            upd["Outcome"] = ""
        proposals.append({"row": n, "employer": rows[n]["Employer"], "job": rows[n]["Job Title"],
                          "from": cur, "to": new, "update": upd, "evidence": best.subject[:70]})
    return sorted(proposals, key=lambda p: p["row"])


def apply_to_sheet(proposals: list[dict]) -> list[str]:
    svc = build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)
    changes = []
    for p in proposals:
        for name, val in p["update"].items():
            if val == "":
                continue
            col = chr(ord("A") + HEADER.index(name))
            svc.spreadsheets().values().update(
                spreadsheetId=CONFIG.log_sheet_id,
                range=f"'{CONFIG.log_sheet_tab}'!{col}{p['row']}",
                valueInputOption="RAW", body={"values": [[val]]},
            ).execute()
        changes.append(f"row {p['row']} {p['employer']}: {p['from']} -> {p['to']}")
    return changes
