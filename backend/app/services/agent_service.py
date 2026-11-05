"""Collections agent: reminders, inbound email handling, drafts and human review."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

from ..config import Settings
from ..logic.collections import rules, templates
from ..logic.collections.automated import detect_automated
from ..logic.collections.facts import build_facts, llm_view
from ..logic.collections.parser import extract_claim_hints, parse_raw_email, strip_quoted
from ..logic.collections.validator import validate_reply
from ..logic.invoices import is_overdue
from ..logic.money import parse_date
from ..repositories.store import Store, now
from .email_service import EmailError, EmailService, valid_address
from .llm_service import LLMError, LLMService

log = logging.getLogger("agent")
PLACEHOLDER = re.compile(r"@(example\.(com|org|net)|.*\.invalid)$", re.I)
AUTH_FAIL = re.compile(r"\b(dkim|spf|dmarc)=(fail|softfail)\b", re.I)


class AgentError(Exception):
    def __init__(self, message: str, status: int = 400, problems: list[str] | None = None):
        super().__init__(message)
        self.status, self.problems = status, problems or []


class AgentService:
    def __init__(self, store: Store, settings: Settings, mail: EmailService, llm: LLMService):
        self.store, self.s, self.mail, self.llm = store, settings, mail, llm
        self._inbox_lock = threading.Lock()

    # ------------------------------------------------------------------ reminders
    def _due_for_reminder(self, inv: dict) -> bool:
        last = self.store.last_reminder(inv["invoice_number"])
        if not last:
            return True
        sent = last["sent_at"]
        sent = sent.replace(tzinfo=timezone.utc) if sent.tzinfo is None else sent
        return now() - sent >= timedelta(days=self.s.reminder_min_days)

    def plan_reminders(self, customer_id: str | None = None, force: bool = False) -> list[dict]:
        org, today = self.store.organization(), self.s.today()
        all_inv = self.store.invoices()
        plans = []
        for c in self.store.customers():
            if customer_id and c["customer_id"] != customer_id:
                continue
            overdue = [i for i in all_inv if i["customer_id"] == c["customer_id"] and is_overdue(i, today)]
            if not overdue:
                continue
            facts = build_facts(org, c, all_inv, today)
            rows = [r for r in facts["invoices"] if r["overdue"]]
            due = [r for r in rows if force or self._due_for_reminder(self.store.invoice(r["invoice_number"]))]
            plan = {"customer_id": c["customer_id"], "customer_name": c["name"], "to": c["contact_email"],
                    "invoices": [r["invoice_number"] for r in rows], "status": "planned", "reason": None}
            if not due:
                plan.update(status="skipped", reason=f"reminded within the last {self.s.reminder_min_days} days")
            elif PLACEHOLDER.search(c["contact_email"] or ""):
                plan.update(status="skipped", reason="customer email is still a placeholder (example.com) - edit it first")
            elif not valid_address(c["contact_email"]):
                plan.update(status="skipped", reason="invalid customer email address")
            else:
                subject, body = templates.reminder_email(facts, due)
                plan.update(invoices=[r["invoice_number"] for r in due], subject=subject, body=body)
            plans.append(plan)
        return plans

    def send_reminders(self, customer_id: str | None = None, dry_run: bool = False, force: bool = False) -> list[dict]:
        plans = self.plan_reminders(customer_id, force)
        if dry_run:
            return [{**p, "status": "preview" if p["status"] == "planned" else p["status"]} for p in plans]
        for p in plans:
            if p["status"] != "planned":
                continue
            try:
                conv = self.store.conversation_for_customer(p["customer_id"]) or self.store.create_conversation(p["customer_id"], p["subject"])
                rfc = self.mail.send(p["to"], p["subject"], p["body"])
                msg = self.store.add_message(conversation_id=conv["conversation_id"], customer_id=p["customer_id"], direction="outbound",
                                             kind="reminder", subject=p["subject"], body=p["body"], to=p["to"], rfc_message_id=rfc,
                                             status="sent", invoice_numbers=p["invoices"])
                self.store.touch_conversation(conv["conversation_id"])
                for n in p["invoices"]:
                    self.store.add_reminder({"customer_id": p["customer_id"], "invoice_number": n, "sent_at": now(), "status": "sent",
                                             "message_id": msg["message_id"], "to": p["to"]})
                p["status"] = "sent"
            except EmailError as exc:
                p.update(status="failed", reason=str(exc))
        return plans

    # ------------------------------------------------------------------ inbox
    def check_inbox(self) -> dict:
        if not self._inbox_lock.acquire(blocking=False):
            return {"status": "busy", "processed": 0, "results": []}
        try:
            results = []

            def handler(raw: bytes) -> bool:
                try:
                    results.append(self.process_email(parse_raw_email(raw), raw))
                    return True
                except Exception:  # noqa: BLE001
                    log.exception("failed to process email")
                    return False

            self.mail.fetch_unseen(handler)
            return {"status": "ok", "processed": len(results), "results": results}
        finally:
            self._inbox_lock.release()

    # ------------------------------------------------------------------ one inbound email
    def process_email(self, parsed: dict, raw: bytes | None = None) -> dict:
        rfc = parsed.get("message_id") or "<gen-" + hashlib.sha256(raw or repr(parsed).encode()).hexdigest()[:24] + ">"
        if not self.store.mark_seen(rfc):
            return {"outcome": "duplicate", "message_id": rfc}
        sender = parsed.get("from", "")
        if sender and sender == self.mail.own_address:
            return {"outcome": "ignored_own_message"}
        body = strip_quoted(parsed.get("body", ""))[:5000]
        subject = parsed.get("subject", "")
        customer = self.store.customer_by_email(sender)
        parent = self.store.find_by_rfc_ids([parsed.get("in_reply_to")] + list(parsed.get("references", [])))

        # conversation: thread first, but never attach to another customer's conversation
        conv = None
        if parent:
            c = self.store.conversation(parent["conversation_id"])
            if c and c.get("customer_id") == (customer or {}).get("customer_id"):
                conv = c
        if conv is None:
            conv = self.store.conversation_for_customer(customer["customer_id"] if customer else None, None if customer else f"unknown:{sender}")
        if conv is None:
            conv = self.store.create_conversation(customer["customer_id"] if customer else None, subject or "(no subject)", None if customer else f"unknown:{sender}")
        cid = conv["conversation_id"]
        base = dict(conversation_id=cid, customer_id=customer["customer_id"] if customer else None, direction="inbound",
                    subject=subject, body=body, **{"from": sender}, rfc_message_id=rfc, in_reply_to=parsed.get("in_reply_to"))

        automated = detect_automated(parsed)
        if automated:
            m = self.store.add_message(**base, kind="inbound", status="automated", classification={"intents": [automated], "confidence": 1.0, "summary": f"Detected {automated.replace('_', ' ')}; not answered."})
            self.store.touch_conversation(cid)
            return {"outcome": automated, "message_id": m["message_id"], "conversation_id": cid}

        inbound = self.store.add_message(**base, kind="inbound", status="received")
        self.store.touch_conversation(cid)
        org = self.store.organization()

        if not customer:
            return self._escalate(inbound, conv, None, org, {"intents": ["unknown_sender"]}, ["unknown sender - no customer data shared"], parsed, None)

        all_inv = self.store.invoices()
        customers = self.store.customers()
        facts = build_facts(org, customer, all_inv, self.s.today())
        reasons: list[str] = []
        owner = {i["invoice_number"]: i["customer_id"] for i in all_inv}
        hints = extract_claim_hints(body, set(owner))
        flags = rules.risky_flags(body)
        other = [n for n in hints["all_invoice_numbers"] if n in owner and owner[n] != customer["customer_id"]]
        brand_hits = [c["name"] for c in customers if c["customer_id"] != customer["customer_id"]
                      and len(c["name"].split()[0]) >= 4 and re.search(rf"\b{re.escape(c['name'].split()[0])}\b", body, re.I)]
        if other or brand_hits:
            flags.add("other_company")
            reasons.append("asks about another company or its invoices")
        if AUTH_FAIL.search(parsed.get("headers", {}).get("authentication-results", "") + parsed.get("headers", {}).get("received-spf", "")):
            flags.add("unclear")
            reasons.append("sender failed SPF/DKIM/DMARC authentication - possible spoofing")

        analysis, llm_error = None, None
        if self.llm.enabled:
            history = [m for m in self.store.messages(cid) if m["message_id"] != inbound["message_id"] and m.get("status") in {"sent", "received"}]
            try:
                analysis = self.llm.analyse(llm_view(facts), history, body, subject)
            except LLMError as exc:
                llm_error = str(exc)
        else:
            analysis = rules.offline_classify(body)
        if analysis is None:
            analysis = {"intents": ["unclear"], "confidence": 0.0, "summary": "LLM unavailable", "payment_claim": None, "reply": ""}
            reasons.append(f"LLM unavailable ({llm_error})")

        intents = {i for i in analysis["intents"] if i in rules.ROUTINE | rules.ESCALATE} | flags
        if not intents:
            intents = {"unclear"}
        for k in sorted(intents & {"legal", "dispute", "extension_request"}):
            reasons.append({"legal": "mentions legal action", "dispute": "disputes or questions an invoice", "extension_request": "asks for more time / instalments / waiver"}[k])
        if intents & rules.ESCALATE and not reasons:
            reasons.append("not a routine question: " + ", ".join(sorted(intents & rules.ESCALATE)))
        if analysis["confidence"] < 0.7 and "LLM unavailable" not in str(reasons):
            reasons.append(f"low classifier confidence ({analysis['confidence']:.2f})")

        # payment claim: record only, never mark paid
        claim = None
        if "payment_claim" in intents:
            c = analysis.get("payment_claim") or {}
            own_numbers = {i["invoice_number"] for i in all_inv if i["customer_id"] == customer["customer_id"]}
            nums = [n for n in dict.fromkeys(list(c.get("invoice_numbers") or []) + hints["invoice_numbers"]) if n in own_numbers]
            cdate = None
            try:
                cdate = parse_date(c["date"]).isoformat() if c.get("date") else None
            except (ValueError, TypeError):
                cdate = None
            claim = {"customer_id": customer["customer_id"], "invoice_numbers": nums, "claimed_date": cdate,
                     "reference": hints["reference"] or (str(c.get("reference")).upper() if c.get("reference") else None),
                     "snippet": body[:200], "conversation_id": cid, "message_id": inbound["message_id"]}
            self.store.add_claim(claim)

        escalate = bool(intents & rules.ESCALATE) or not (intents & rules.ROUTINE) or analysis["confidence"] < 0.7 or llm_error
        draft = None
        if not escalate:
            draft = analysis.get("reply") if self.llm.enabled and analysis.get("reply") else templates.routine_reply(facts, intents, claim)
            problems = validate_reply(draft, facts, customers, body)
            if problems:
                escalate = True
                reasons.append("draft failed validation: " + "; ".join(problems[:3]))
                self.store.update_message(inbound["message_id"], rejected_draft=draft)
                draft = None
            elif self.store.count_auto_replies_since(cid, now() - timedelta(days=1)) >= self.s.max_auto_replies_per_day:
                escalate = True
                reasons.append("daily auto-reply limit reached for this conversation (loop protection)")
                draft = None
        if escalate:
            return self._escalate(inbound, conv, customer, org, {**analysis, "intents": sorted(intents)}, reasons, parsed, facts)

        subj = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        refs = list(parsed.get("references", [])) + [rfc]
        try:
            out_rfc = self.mail.send(customer["contact_email"], subj, draft, in_reply_to=rfc, references=refs)
        except EmailError as exc:
            return self._escalate(inbound, conv, customer, org, {**analysis, "intents": sorted(intents)}, [f"auto-reply could not be sent: {exc}"], parsed, facts, draft=draft)
        out = self.store.add_message(conversation_id=cid, customer_id=customer["customer_id"], direction="outbound", kind="auto_reply", subject=subj,
                                     body=draft, to=customer["contact_email"], rfc_message_id=out_rfc, in_reply_to=rfc, status="sent")
        self.store.update_message(inbound["message_id"], status="answered", classification={**analysis, "intents": sorted(intents)})
        self.store.touch_conversation(cid)
        return {"outcome": "auto_replied", "intents": sorted(intents), "message_id": out["message_id"], "conversation_id": cid}

    def _escalate(self, inbound, conv, customer, org, analysis, reasons, parsed, facts, draft=None) -> dict:
        contact = customer["contact_name"] if customer else None
        text = draft or templates.holding_reply(facts, org, contact)
        subj = inbound["subject"] if inbound["subject"].lower().startswith("re:") else f"Re: {inbound['subject']}"
        to = customer["contact_email"] if customer else parsed.get("from")
        d = self.store.add_message(conversation_id=conv["conversation_id"], customer_id=inbound.get("customer_id"), direction="outbound", kind="draft",
                                   subject=subj, body=text, to=to, in_reply_to=inbound["rfc_message_id"], status="pending_review",
                                   needs_attention_reason="; ".join(reasons), reply_to_message=inbound["message_id"],
                                   references=list(parsed.get("references", [])) + [inbound["rfc_message_id"]])
        self.store.update_message(inbound["message_id"], status="needs_attention", classification={**analysis, "reasons": reasons})
        self.store.touch_conversation(conv["conversation_id"])
        return {"outcome": "needs_attention", "reasons": reasons, "message_id": d["message_id"], "conversation_id": conv["conversation_id"]}

    # ------------------------------------------------------------------ human review
    def send_draft(self, message_id: str, subject: str | None, body: str, force: bool = False) -> dict:
        msg = self.store.message(message_id)
        if not msg or msg.get("status") != "pending_review":
            raise AgentError("draft not found or already handled", 404)
        body = (body or "").strip()
        if not body:
            raise AgentError("reply body is empty", 422)
        to = msg["to"]
        if msg.get("customer_id"):
            customer = self.store.customer(msg["customer_id"])
            to = customer["contact_email"]  # always the address on record, never a header from the email
            facts = build_facts(self.store.organization(), customer, self.store.invoices(), self.s.today())
            inbound_text = (self.store.message(msg.get("reply_to_message")) or {}).get("body", "")
            problems = validate_reply(body, facts, self.store.customers(), inbound_text)
            if problems and not force:
                raise AgentError("The reply contains details that do not match the database", 422, problems)
        if not self.store.claim_message_for_send(message_id):
            raise AgentError("draft is already being sent", 409)
        subj = (subject or msg["subject"]).strip()
        try:
            rfc = self.mail.send(to, subj, body, in_reply_to=msg.get("in_reply_to"), references=msg.get("references"))
        except EmailError as exc:
            self.store.update_message(message_id, status="pending_review")
            raise AgentError(str(exc), 502) from exc
        self.store.update_message(message_id, status="sent", body=body, subject=subj, rfc_message_id=rfc, sent_at=now(), to=to, edited_by_human=True)
        if msg.get("reply_to_message"):
            self.store.update_message(msg["reply_to_message"], status="handled")
        self.store.touch_conversation(msg["conversation_id"])
        return self.store.message(message_id)

    def dismiss_draft(self, message_id: str) -> None:
        msg = self.store.message(message_id)
        if not msg or msg.get("status") != "pending_review":
            raise AgentError("draft not found or already handled", 404)
        self.store.update_message(message_id, status="dismissed")
        if msg.get("reply_to_message"):
            self.store.update_message(msg["reply_to_message"], status="handled")
