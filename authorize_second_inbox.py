"""One-time READ-ONLY consent for a second Gmail account.

Saves a separate token so the main pipeline token is untouched. Read-only scope:
it cannot send, delete or modify mail.

Usage:  python authorize_second_inbox.py <label>

The label names the token file (credentials/token_<label>.json), so you can keep
several accounts side by side.
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from config import CONFIG

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

label = sys.argv[1] if len(sys.argv) > 1 else "second"
out = CONFIG.google_token_path.parent / f"token_{label}.json"
flow = InstalledAppFlow.from_client_secrets_file(str(CONFIG.google_client_secrets), SCOPES)
print("A browser window will open. Sign in with the SECOND Gmail account and allow read-only access.")
creds = flow.run_local_server(port=0, prompt="consent select_account", login_hint=f"{label}@gmail.com")
out.write_text(creds.to_json(), encoding="utf-8")
print(f"Saved read-only token to {out}")
