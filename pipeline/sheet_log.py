"""Append one row per application to a Google Sheet.

Columns: date | company | role | url | contacts found | status

Set LOG_SHEET_ID in .env to the spreadsheet ID (the long token in the sheet URL:
https://docs.google.com/spreadsheets/d/<ID>/edit). The tab defaults to
"Applications" (LOG_SHEET_TAB). A header row is written automatically if the tab
is empty.
"""
from __future__ import annotations

from datetime import date

from googleapiclient.discovery import build

from config import CONFIG
from pipeline.google_auth import get_credentials

HEADER = ["Date", "Company", "Role", "URL", "Contacts found", "Status"]


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
    rng = f"{CONFIG.log_sheet_tab}!A1:F1"
    existing = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=CONFIG.log_sheet_id, range=rng)
        .execute()
        .get("values", [])
    )
    if not existing:
        service.spreadsheets().values().update(
            spreadsheetId=CONFIG.log_sheet_id,
            range=rng,
            valueInputOption="RAW",
            body={"values": [HEADER]},
        ).execute()


def append_row(
    company: str,
    role: str,
    url: str,
    contacts: list[str],
    status: str,
) -> None:
    if not CONFIG.log_sheet_id:
        raise RuntimeError("LOG_SHEET_ID is not set (see .env / README).")
    service = _service()
    _ensure_tab(service)
    _ensure_header(service)
    row = [date.today().isoformat(), company, role, url, "; ".join(contacts), status]

    tab = CONFIG.log_sheet_tab
    values = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=CONFIG.log_sheet_id, range=f"{tab}!A:F")
        .execute()
        .get("values", [])
    )
    # Update in place if this URL was already logged; otherwise append.
    for i, existing in enumerate(values[1:], start=2):
        if len(existing) >= 4 and existing[3] == url:
            service.spreadsheets().values().update(
                spreadsheetId=CONFIG.log_sheet_id,
                range=f"{tab}!A{i}:F{i}",
                valueInputOption="USER_ENTERED",
                body={"values": [row]},
            ).execute()
            return

    service.spreadsheets().values().append(
        spreadsheetId=CONFIG.log_sheet_id,
        range=f"{tab}!A:F",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()
