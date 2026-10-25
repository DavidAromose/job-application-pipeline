"""Append/update one row per role in the Google Sheet job pipeline.

Columns (must stay in sync with the "Job Pipeline" tab used by the Monday
Application Sprint - see reference_job_pipeline_sheet memory):
  Job Title | Employer | Track | Date Found | Date Applied | Pay | Hours |
  Location | Contract | Sponsorship Status | Priority | CV Version |
  Recruiter Contacted | Follow-up Date | Interview Status | Outcome |
  Status | Application Link | Notes

Set LOG_SHEET_ID in .env to the spreadsheet ID (the long token in the sheet
URL: https://docs.google.com/spreadsheets/d/<ID>/edit). The tab defaults to
"Job Pipeline" (LOG_SHEET_TAB). A header row is written automatically if the
tab is empty. Rows are matched/updated by Application Link, never duplicated.

Status vocabulary: FOUND, SHORTLISTED, READY TO APPLY, APPLIED, FOLLOW-UP DUE,
INTERVIEW, OFFER, REJECTED, WITHDRAWN, CLOSED/EXPIRED. Never write APPLIED here
- only the user confirms an application was actually submitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from googleapiclient.discovery import build

from config import CONFIG
from pipeline.google_auth import get_credentials

HEADER = [
    "Job Title", "Employer", "Track", "Date Found", "Date Applied", "Pay", "Hours",
    "Location", "Contract", "Sponsorship Status", "Priority", "CV Version",
    "Recruiter Contacted", "Follow-up Date", "Interview Status", "Outcome",
    "Status", "Application Link", "Notes",
]
_LINK_COL = HEADER.index("Application Link")  # 0-indexed column to dedupe on


@dataclass
class PipelineRow:
    job_title: str
    employer: str
    application_link: str
    track: str = "Track 2"
    date_found: str = ""
    date_applied: str = ""
    pay: str = "Not stated"
    hours: str = "Not stated"
    location: str = "Not stated"
    contract: str = "Not stated"
    sponsorship_status: str = "Sponsorship Not Stated"
    priority: str = ""
    cv_version: str = ""
    recruiter_contacted: str = "No"
    follow_up_date: str = ""
    interview_status: str = ""
    outcome: str = ""
    status: str = "FOUND"
    notes: str = ""

    def to_row(self) -> list[str]:
        return [
            self.job_title, self.employer, self.track,
            self.date_found or date.today().isoformat(), self.date_applied,
            self.pay, self.hours, self.location, self.contract,
            self.sponsorship_status, self.priority, self.cv_version,
            self.recruiter_contacted, self.follow_up_date, self.interview_status,
            self.outcome, self.status, self.application_link, self.notes,
        ]


def _service():
    return build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)


def _ensure_tab(service) -> None:
    meta = service.spreadsheets().get(spreadsheetId=CONFIG.log_sheet_id).execute()
    tabs = {sh["properties"]["title"] for sh in meta.get("sheets", [])}
    if CONFIG.log_sheet_tab not in tabs:
        service.spreadsheets().batchUpdate(
            spreadsheetId=CONFIG.log_sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": CONFIG.log_sheet_tab}}}]},
        ).execute()


def _ensure_header(service) -> None:
    last_col = chr(ord("A") + len(HEADER) - 1)
    rng = f"{CONFIG.log_sheet_tab}!A1:{last_col}1"
    existing = (
        service.spreadsheets().values().get(spreadsheetId=CONFIG.log_sheet_id, range=rng)
        .execute().get("values", [])
    )
    if not existing:
        service.spreadsheets().values().update(
            spreadsheetId=CONFIG.log_sheet_id, range=rng,
            valueInputOption="RAW", body={"values": [HEADER]},
        ).execute()


def upsert_row(row: PipelineRow) -> None:
    """Insert a new pipeline row, or update the existing one for this Application Link."""
    if not CONFIG.log_sheet_id:
        raise RuntimeError("LOG_SHEET_ID is not set (see .env / README).")
    service = _service()
    _ensure_tab(service)
    _ensure_header(service)

    tab = CONFIG.log_sheet_tab
    last_col = chr(ord("A") + len(HEADER) - 1)
    values = (
        service.spreadsheets().values().get(spreadsheetId=CONFIG.log_sheet_id, range=f"{tab}!A:{last_col}")
        .execute().get("values", [])
    )
    new_row = row.to_row()

    for i, existing in enumerate(values[1:], start=2):
        if len(existing) > _LINK_COL and existing[_LINK_COL] == row.application_link:
            # Never clobber a status the user has already progressed past FOUND/READY TO APPLY.
            existing_status = existing[HEADER.index("Status")] if len(existing) > HEADER.index("Status") else ""
            if existing_status not in ("", "FOUND", "SHORTLISTED", "READY TO APPLY"):
                return
            # Merge - keep everything already recorded (pay, hours, location, priority...)
            # and only overwrite fields the pipeline actually knows something new about.
            merged = list(existing) + [""] * (len(HEADER) - len(existing))
            placeholder = {"", "Not stated", "Sponsorship Not Stated", "No"}
            for idx, val in enumerate(new_row):
                col = HEADER[idx]
                if col in ("Job Title", "Employer", "Application Link"):
                    continue
                if col == "Date Found":
                    merged[idx] = merged[idx] or val
                elif col == "Notes":
                    if val and val not in merged[idx]:
                        merged[idx] = f"{merged[idx]} | {val}" if merged[idx] else val
                elif val not in placeholder:
                    merged[idx] = val
            service.spreadsheets().values().update(
                spreadsheetId=CONFIG.log_sheet_id, range=f"{tab}!A{i}:{last_col}{i}",
                valueInputOption="USER_ENTERED", body={"values": [merged]},
            ).execute()
            return

    service.spreadsheets().values().append(
        spreadsheetId=CONFIG.log_sheet_id, range=f"{tab}!A:{last_col}",
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body={"values": [new_row]},
    ).execute()


def append_row(
    company: str,
    role: str,
    url: str,
    contacts: list[str],
    status: str,
    track: str = "Track 2",
    cv_version: str = "",
    notes: str = "",
) -> None:
    """Back-compat wrapper for main.py's pipeline-completion log call.

    A finished CV means READY TO APPLY. Rows already past that stage (APPLIED, INTERVIEW,
    ...) are never overwritten - see upsert_row.
    """
    sheet_status = "READY TO APPLY" if (cv_version or status == "draft created") else "FOUND"
    parts = []
    if contacts:
        parts.append(f"Contacts found: {'; '.join(contacts)}")
    if notes:
        parts.append(notes)
    upsert_row(PipelineRow(
        job_title=role,
        employer=company,
        application_link=url,
        track=track,
        cv_version=cv_version,
        status=sheet_status,
        notes=" | ".join(parts),
    ))
