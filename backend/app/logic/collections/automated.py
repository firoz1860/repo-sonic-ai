"""Recognise out-of-office replies, bounces and other machine-generated mail so the agent never answers them."""
from __future__ import annotations

import re

_OOO_SUBJ = re.compile(r"(out of (the )?office|automatic reply|auto[- ]?reply|autoreply|away from (my )?(desk|office)|on (annual )?leave|vacation reply)", re.I)
_OOO_BODY = re.compile(r"(i am (currently )?(out of (the )?office|on leave|away)|will (be back|return) on|limited access to (my )?email|this is an automated (reply|response))", re.I)
_BOUNCE_SUBJ = re.compile(r"(undeliver|delivery (status|failure|has failed)|mail delivery|returned mail|failure notice|could not be delivered|delivery incomplete|bounce)", re.I)
_BOUNCE_FROM = re.compile(r"(mailer-daemon|postmaster|mail delivery subsystem)", re.I)


def detect_automated(parsed: dict) -> str | None:
    """Return 'bounce', 'out_of_office' or None."""
    h = {k.lower(): str(v) for k, v in (parsed.get("headers") or {}).items()}
    subj, body, sender = parsed.get("subject", ""), parsed.get("body", "")[:1500], parsed.get("from", "")
    ctype = h.get("content-type", "").lower()
    if (_BOUNCE_FROM.search(sender) or "report-type=delivery-status" in ctype or h.get("return-path", "").strip() == "<>"
            or _BOUNCE_SUBJ.search(subj) or h.get("x-failed-recipients")):
        return "bounce"
    auto = h.get("auto-submitted", "").lower()
    if (auto and auto != "no") or h.get("x-autoreply") or h.get("x-autorespond") or h.get("x-auto-response-suppress"):
        return "out_of_office"
    if _OOO_SUBJ.search(subj) or (_OOO_BODY.search(body) and len(body) < 1200):
        return "out_of_office"
    if h.get("precedence", "").lower() in {"bulk", "junk", "auto_reply"}:
        return "out_of_office"
    return None
