"""Parse raw RFC 822 email into a plain dict, strip quoted history and extract payment claims."""
from __future__ import annotations

import re
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html import unescape


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
    return unescape(re.sub(r"<[^>]+>", "", html))


def parse_raw_email(raw: bytes) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    body = ""
    part = msg.get_body(preferencelist=("plain",))
    if part is not None:
        body = part.get_content()
    else:
        part = msg.get_body(preferencelist=("html",))
        if part is not None:
            body = _html_to_text(part.get_content())
    name, addr = parseaddr(str(msg.get("From", "")))
    try:
        date = parsedate_to_datetime(str(msg["Date"])).isoformat() if msg["Date"] else None
    except Exception:  # noqa: BLE001
        date = None
    refs = re.findall(r"<[^>]+>", str(msg.get("References", "")))
    return {
        "message_id": (str(msg.get("Message-ID", "")).strip() or None),
        "in_reply_to": (str(msg.get("In-Reply-To", "")).strip() or None),
        "references": refs,
        "from": addr.lower(), "from_name": name,
        "to": [a.lower() for _, a in getaddresses([str(msg.get("To", ""))])],
        "subject": str(msg.get("Subject", "") or "").replace("\r", " ").replace("\n", " ").strip(),
        "body": body or "", "date": date,
        "headers": {k.lower(): str(v) for k, v in msg.items() if k.lower() in {
            "auto-submitted", "x-autoreply", "x-autorespond", "precedence", "return-path", "content-type",
            "x-failed-recipients", "x-auto-response-suppress", "authentication-results", "received-spf", "reply-to"}},
    }


_QUOTE_START = re.compile(r"^(on .{5,120} wrote:|-{2,}\s*original message\s*-{2,}|from:\s.+|_{5,}|\*?from:\*?\s.+)$", re.I)


def strip_quoted(body: str) -> str:
    lines = []
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith(">"):
            continue
        if _QUOTE_START.match(s) and lines:
            break
        lines.append(line)
    return "\n".join(lines).strip()


_REF_RE = re.compile(r"(?:ref(?:erence)?(?:\s*(?:no|number|#))?|utr|txn(?:\s*id)?|transaction(?:\s*id)?)\s*[:#\-]?\s*([A-Z0-9]{8,24})\b", re.I)


def extract_claim_hints(text: str, known_invoice_numbers: set[str]) -> dict:
    """Regex hints used to cross-check the LLM and for the email<->bank-row link."""
    nums = []
    for m in re.finditer(r"\bINV[\s\-]?(\d{3,6})\b", text, re.I):
        n = f"INV-{int(m.group(1)):04d}" if len(m.group(1)) <= 4 else f"INV-{m.group(1)}"
        if n not in nums:
            nums.append(n)
    ref = None
    for m in _REF_RE.finditer(text):
        cand = m.group(1)
        if any(ch.isdigit() for ch in cand) and not cand.upper().startswith("INV"):
            ref = cand.upper()
            break
    return {"invoice_numbers": [n for n in nums if n in known_invoice_numbers], "all_invoice_numbers": nums, "reference": ref}
