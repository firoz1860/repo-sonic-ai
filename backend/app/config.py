"""Application settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

IST = ZoneInfo("Asia/Kolkata")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # database
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "sonic_collections"
    # app security
    admin_password: str = ""
    secret_key: str = ""
    token_ttl_minutes: int = 480
    frontend_origin: str = "http://localhost:5173"
    # SMTP
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"  # ssl | starttls | none
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    email_dry_run: bool = False
    # IMAP
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    imap_poll_seconds: int = 0
    # LLM
    llm_provider: str = "none"  # openai (any OpenAI-compatible API) | anthropic | none
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: int = 40
    # behaviour
    as_of_date: str = ""  # YYYY-MM-DD, overrides today (for demos/tests)
    reminder_min_days: int = 3
    auto_reminder_hours: int = 0
    max_auto_replies_per_day: int = 4
    data_dir: Path = field(default_factory=lambda: ROOT.parent / "data")
    org_accounts_email: str = ""
    max_upload_bytes: int = 1_000_000
    max_statement_rows: int = 2000

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        s = cls(
            mongodb_uri=e("MONGODB_URI", "mongodb://localhost:27017"),
            mongodb_db=e("MONGODB_DB", "sonic_collections"),
            admin_password=e("ADMIN_PASSWORD", ""),
            secret_key=e("SECRET_KEY", ""),
            token_ttl_minutes=_int("TOKEN_TTL_MINUTES", 480),
            frontend_origin=e("FRONTEND_ORIGIN", "http://localhost:5173"),
            smtp_host=e("SMTP_HOST", ""),
            smtp_port=_int("SMTP_PORT", 465),
            smtp_security=e("SMTP_SECURITY", "ssl").lower(),
            smtp_user=e("SMTP_USER", ""),
            smtp_password=e("SMTP_PASSWORD", ""),
            smtp_from=e("SMTP_FROM", "") or e("SMTP_USER", ""),
            email_dry_run=_bool("EMAIL_DRY_RUN", False),
            imap_host=e("IMAP_HOST", ""),
            imap_port=_int("IMAP_PORT", 993),
            imap_user=e("IMAP_USER", "") or e("SMTP_USER", ""),
            imap_password=e("IMAP_PASSWORD", "") or e("SMTP_PASSWORD", ""),
            imap_folder=e("IMAP_FOLDER", "INBOX"),
            imap_poll_seconds=_int("IMAP_POLL_SECONDS", 0),
            llm_provider=e("LLM_PROVIDER", "none").lower(),
            llm_base_url=e("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_api_key=e("LLM_API_KEY", ""),
            llm_model=e("LLM_MODEL", ""),
            llm_timeout=_int("LLM_TIMEOUT", 40),
            as_of_date=e("AS_OF_DATE", ""),
            reminder_min_days=_int("REMINDER_MIN_DAYS", 3),
            auto_reminder_hours=_int("AUTO_REMINDER_HOURS", 0),
            max_auto_replies_per_day=_int("MAX_AUTO_REPLIES_PER_DAY", 4),
            org_accounts_email=e("ORG_ACCOUNTS_EMAIL", "") or e("SMTP_FROM", "") or e("SMTP_USER", ""),
        )
        if e("DATA_DIR"):
            s.data_dir = Path(e("DATA_DIR"))
        elif (ROOT / "data").exists():
            s.data_dir = ROOT / "data"
        return s

    def today(self) -> date:
        if self.as_of_date:
            return date.fromisoformat(self.as_of_date)
        return datetime.now(IST).date()

    def validate_for_serving(self) -> list[str]:
        problems = []
        if len(self.admin_password) < 8:
            problems.append("ADMIN_PASSWORD must be set (min 8 chars)")
        if len(self.secret_key) < 16:
            problems.append("SECRET_KEY must be set (min 16 chars)")
        return problems
