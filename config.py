"""Central configuration. Everything sensitive comes from .env (gitignored)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _sheet_id(value: str) -> str:
    """Accept either a bare spreadsheet ID or a full Google Sheets URL."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", value)
    return m.group(1) if m else value


class ConfigError(RuntimeError):
    """Raised when a required credential or setting is missing."""


@dataclass
class Applicant:
    name: str = _get("APPLICANT_NAME", "David Aromose")
    email: str = _get("APPLICANT_EMAIL")
    phone: str = _get("APPLICANT_PHONE")
    location: str = _get("APPLICANT_LOCATION")
    linkedin: str = _get("APPLICANT_LINKEDIN")
    github: str = _get("APPLICANT_GITHUB")
    # Optional header lines. Only printed when set - never asserted by default.
    right_to_work: str = _get("APPLICANT_RIGHT_TO_WORK")  # e.g. "Right to work in the UK"
    availability: str = _get("APPLICANT_AVAILABILITY")  # e.g. "Available for evening/weekend shifts"


@dataclass
class Config:
    # Anthropic
    anthropic_api_key: str = _get("ANTHROPIC_API_KEY")
    anthropic_model: str = _get("ANTHROPIC_MODEL", "claude-opus-5")

    # Contacts
    contacts_provider: str = _get("CONTACTS_PROVIDER", "auto")  # auto|apollo|hunter|none
    apollo_api_key: str = _get("APOLLO_API_KEY")
    hunter_api_key: str = _get("HUNTER_API_KEY")
    hunter_limit: int = int(_get("HUNTER_LIMIT", "10") or "10")

    # Google
    google_client_secrets: Path = PROJECT_ROOT / _get(
        "GOOGLE_CLIENT_SECRETS", "credentials/client_secret.json"
    )
    google_token_path: Path = PROJECT_ROOT / _get(
        "GOOGLE_TOKEN_PATH", "credentials/token.json"
    )
    log_sheet_id: str = _sheet_id(_get("LOG_SHEET_ID"))
    log_sheet_tab: str = _get("LOG_SHEET_TAB", "Applications")

    # PDF
    soffice_path: str = _get("SOFFICE_PATH")

    # CV presentation
    # Links (GitHub/LinkedIn) are only shown on technology/data/AI roles, and only if true.
    cv_show_links: bool = _get("CV_SHOW_LINKS", "false").lower() in ("1", "true", "yes")
    # Contact discovery + Gmail draft are opt-in per run via --outreach when this is false.
    outreach_default: bool = _get("OUTREACH_DEFAULT", "true").lower() in ("1", "true", "yes")
    # Gmail read-only inbox access (for response tracking). Off unless explicitly enabled;
    # enabling it adds the gmail.readonly scope, so re-run authorize.py afterwards.
    inbox_access: bool = _get("INBOX_ACCESS", "false").lower() in ("1", "true", "yes")

    # Files
    master_resume_path: Path = PROJECT_ROOT / _get(
        "MASTER_RESUME_PATH", "David_Aromose_Master_Resume.docx"
    )
    output_dir: Path = PROJECT_ROOT / _get("OUTPUT_DIR", "output")

    target_roles: list[str] = field(
        default_factory=lambda: _split(_get("TARGET_ROLES", "Software Engineer"))
    )
    # Extra truthful constraints from the candidate (things to exclude / correct).
    resume_constraints: str = _get(
        "RESUME_CONSTRAINTS",
        "Use ONLY content grounded in the master resume; if a fact is not in it, "
        "do not add it.",
    )
    # Post-generation enforcement (see pipeline/resume.py):
    resume_replacements: str = _get("RESUME_REPLACEMENTS")  # "old=>new;old2=>new2"
    resume_drop_patterns: str = _get("RESUME_DROP_PATTERNS")  # regex
    applicant: Applicant = field(default_factory=Applicant)

    def require(self, *keys: str) -> None:
        """Fail fast if a needed credential is absent."""
        missing = []
        checks = {
            "anthropic": self.anthropic_api_key,
            "apollo": self.apollo_api_key,
            "sheet": self.log_sheet_id,
        }
        for key in keys:
            if key in checks and not checks[key]:
                missing.append(key)
        if "google" in keys and not self.google_client_secrets.exists():
            missing.append(f"google client secrets ({self.google_client_secrets})")
        if "master_resume" in keys and not self.master_resume_path.exists():
            missing.append(f"master resume ({self.master_resume_path})")
        if missing:
            raise ConfigError(
                "Missing configuration: "
                + ", ".join(missing)
                + ".\nCopy .env.example to .env and fill in the blanks "
                "(see README.md for the credential walkthrough)."
            )


CONFIG = Config()
