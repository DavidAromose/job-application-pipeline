"""Shared Google OAuth (installed-app flow) for Gmail drafts + Sheets logging.

One consent covers both APIs. The first run opens a browser window; after that
the refresh token in credentials/token.json is reused silently.

Scopes:
  * gmail.compose      - create/read/update/delete drafts. This tool NEVER sends.
  * spreadsheets       - append rows to the tracking sheet.
"""
from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import CONFIG

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/spreadsheets",
]


def get_credentials() -> Credentials:
    token_path = CONFIG.google_token_path
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
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
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds
