"""Shared Google OAuth (installed-app flow) for Gmail drafts + Sheets logging.

One consent covers both APIs. The first run opens a browser window; after that
the refresh token in credentials/token.json is reused silently.

Scopes:
  * gmail.compose   - create/read/update/delete drafts. This tool NEVER sends.
  * spreadsheets    - append rows to the tracking sheet.
  * gmail.readonly  - OPT-IN (INBOX_ACCESS=true). Lets pipeline/inbox.py read
                      application replies. Read-only: cannot send, modify or delete.

Adding a scope never silently pops a browser during a normal run: an existing
token keeps working with the scopes it already has, and only `python
authorize.py` asks for the new consent.
"""
from __future__ import annotations

import json

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import CONFIG

INBOX_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/spreadsheets",
]
if CONFIG.inbox_access:
    SCOPES.append(INBOX_SCOPE)


def _granted_scopes(token_path) -> set[str]:
    """Scopes recorded in the saved token (what the user actually consented to)."""
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
        return set(data.get("scopes") or [])
    except Exception:  # noqa: BLE001
        return set()


def get_credentials(force_consent: bool = False) -> Credentials:
    token_path = CONFIG.google_token_path
    creds: Credentials | None = None

    granted = _granted_scopes(token_path) if token_path.exists() else set()
    missing = set(SCOPES) - granted
    # Use the full requested scope list only if it is already granted (or we are about to
    # ask for it); otherwise keep working with exactly what the old token was granted.
    use_scopes = SCOPES if (not missing or force_consent) else sorted(granted)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), use_scopes)
    if force_consent and missing:
        creds = None  # a new scope needs fresh consent

    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except RefreshError:
                # Refresh token itself has expired/been revoked (common with an
                # OAuth consent screen still in "Testing" mode) - fall back to a
                # fresh interactive login instead of crashing.
                creds = None
        if not refreshed:
            if not CONFIG.google_client_secrets.exists():
                raise FileNotFoundError(
                    f"Google client secrets not found at {CONFIG.google_client_secrets}. "
                    "Create an OAuth 'Desktop app' client in Google Cloud Console, "
                    "download the JSON, and save it there (see README)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CONFIG.google_client_secrets), SCOPES
            )
            creds = flow.run_local_server(port=0)
            granted = set(creds.scopes or SCOPES)
        data = json.loads(creds.to_json())
        data["scopes"] = sorted(granted)  # record what was really granted, not what was asked
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(json.dumps(data), encoding="utf-8")

    return creds
