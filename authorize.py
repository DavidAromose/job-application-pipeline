"""One-time Google authorization for Gmail drafts + Sheets logging.

Run this once:  python authorize.py

It opens a browser window asking you to grant access to the Google account you
want the drafts created in. After you click "Allow", a token is saved to
credentials/token.json and the pipeline reuses it automatically.
"""
from pipeline.google_auth import get_credentials

if __name__ == "__main__":
    creds = get_credentials()
    print("Authorized. Token saved to credentials/token.json")
    print("Scopes:", creds.scopes)
