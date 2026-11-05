"""SMTP sending and IMAP reading. The only module that touches a mail server."""
from __future__ import annotations

import imaplib
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from ..config import Settings

log = logging.getLogger("email")
_ADDR = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+$")


class EmailError(Exception):
    pass


def clean_header(value: str) -> str:
    """Remove CR/LF so user-controlled text can never inject extra headers."""
    return re.sub(r"[\r\n]+", " ", value or "").strip()


def valid_address(addr: str) -> bool:
    return bool(_ADDR.match(addr or "")) and len(addr) <= 254


class EmailService:
    def __init__(self, settings: Settings):
        self.s = settings

    @property
    def smtp_configured(self) -> bool:
        return bool(self.s.smtp_host and self.s.smtp_from) or self.s.email_dry_run

    @property
    def imap_configured(self) -> bool:
        return bool(self.s.imap_host and self.s.imap_user and self.s.imap_password)

    @property
    def own_address(self) -> str:
        return parseaddr(self.s.smtp_from)[1].lower()

    def send(self, to: str, subject: str, body: str, in_reply_to: str | None = None, references: list[str] | None = None) -> str:
        to = clean_header(to)
        if not valid_address(to):
            raise EmailError(f"invalid recipient address: {to!r}")
        msg = EmailMessage()
        domain = (self.own_address.split("@")[-1] or "localhost") if self.own_address else "localhost"
        mid = make_msgid(domain=domain)
        msg["Message-ID"] = mid
        msg["From"] = clean_header(self.s.smtp_from) or "accounts@localhost"
        msg["To"] = to
        msg["Subject"] = clean_header(subject)[:300]
        msg["Date"] = formatdate(localtime=True)
        if in_reply_to:
            msg["In-Reply-To"] = clean_header(in_reply_to)
            refs = [clean_header(r) for r in (references or []) if r]
            msg["References"] = " ".join(dict.fromkeys(refs + [clean_header(in_reply_to)]))
        msg.set_content(body)  # plain text only: no HTML, so nothing to inject
        if self.s.email_dry_run:
            log.info("DRY RUN email to %s: %s", to, msg["Subject"])
            return mid
        if not self.s.smtp_host:
            raise EmailError("SMTP is not configured (set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM)")
        try:
            ctx = ssl.create_default_context()
            if self.s.smtp_security == "ssl":
                server = smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, timeout=30, context=ctx)
            else:
                server = smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=30)
                if self.s.smtp_security == "starttls":
                    server.starttls(context=ctx)
            with server:
                if self.s.smtp_user:
                    server.login(self.s.smtp_user, self.s.smtp_password)
                server.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            raise EmailError(f"SMTP send failed: {exc}") from exc
        return mid

    def fetch_unseen(self, handler) -> int:
        """Call handler(raw_bytes) for each unseen message; mark it \\Seen only if the handler returns True."""
        if not self.imap_configured:
            raise EmailError("IMAP is not configured (set IMAP_HOST, IMAP_USER, IMAP_PASSWORD)")
        handled = 0
        try:
            box = imaplib.IMAP4_SSL(self.s.imap_host, self.s.imap_port, timeout=30) if self.s.imap_port != 143 else imaplib.IMAP4(self.s.imap_host, 143, timeout=30)
            try:
                box.login(self.s.imap_user, self.s.imap_password)
                box.select(self.s.imap_folder)
                typ, data = box.search(None, "UNSEEN")
                for num in (data[0].split() if typ == "OK" and data and data[0] else []):
                    typ, parts = box.fetch(num, "(BODY.PEEK[])")
                    if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                        continue
                    if handler(parts[0][1]):
                        box.store(num, "+FLAGS", "\\Seen")
                        handled += 1
            finally:
                try:
                    box.logout()
                except Exception:  # noqa: BLE001
                    pass
        except EmailError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmailError(f"IMAP read failed: {exc}") from exc
        return handled
