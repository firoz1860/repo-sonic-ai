"""Database access. Business rules live elsewhere; this layer only reads/writes documents."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

NOID = {"_id": 0}


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, db):
        self.db = db

    # --- organization / customers / invoices
    def organization(self) -> dict:
        return self.db.organization.find_one({}, NOID) or {}

    def customers(self) -> list[dict]:
        return list(self.db.customers.find({}, NOID).sort("customer_id", ASCENDING))

    def customer(self, customer_id: str) -> dict | None:
        return self.db.customers.find_one({"customer_id": customer_id}, NOID)

    def customer_by_email(self, email: str) -> dict | None:
        return self.db.customers.find_one({"contact_email_lc": (email or "").strip().lower()}, NOID)

    def set_customer_email(self, customer_id: str, email: str) -> bool:
        r = self.db.customers.update_one({"customer_id": customer_id}, {"$set": {"contact_email": email, "contact_email_lc": email.lower()}})
        return r.matched_count == 1

    def invoices(self, **flt) -> list[dict]:
        return list(self.db.invoices.find(flt, NOID).sort("invoice_number", ASCENDING))

    def invoice(self, number: str) -> dict | None:
        return self.db.invoices.find_one({"invoice_number": number}, NOID)

    def apply_to_invoice(self, number: str, amount: float, tds_closed: float) -> dict | None:
        """Atomically reduce a balance. Returns the updated invoice, or None if the guard failed (paid/over-balance)."""
        inv = self.invoice(number)
        if not inv or inv["status"] != "sent":
            return None
        from ..logic.money import D, q2, to_float

        bal = D(inv["balance_due"])
        take = D(amount) + D(tds_closed)
        if take > bal or take <= 0:
            return None
        new_bal = q2(bal - take)
        update = {"amount_paid": to_float(D(inv["amount_paid"]) + D(amount)), "balance_due": to_float(new_bal),
                  "tds_deducted": to_float(D(inv.get("tds_deducted", 0)) + D(tds_closed))}
        if new_bal == 0:
            update["status"] = "paid"
        r = self.db.invoices.update_one({"invoice_number": number, "status": "sent", "balance_due": inv["balance_due"]}, {"$set": update})
        return self.invoice(number) if r.modified_count == 1 else None

    def restore_invoice(self, snapshot: dict) -> None:
        self.db.invoices.replace_one({"invoice_number": snapshot["invoice_number"]}, snapshot)

    # --- conversations & messages
    def conversation(self, cid: str) -> dict | None:
        return self.db.conversations.find_one({"conversation_id": cid}, NOID)

    def conversation_for_customer(self, customer_id: str | None, key: str | None = None) -> dict | None:
        flt = {"customer_id": customer_id} if customer_id else {"customer_id": None, "key": key}
        return self.db.conversations.find_one(flt, NOID, sort=[("last_message_at", DESCENDING)])

    def create_conversation(self, customer_id: str | None, subject: str, key: str | None = None) -> dict:
        doc = {"conversation_id": new_id("conv"), "customer_id": customer_id, "key": key, "subject": subject,
               "created_at": now(), "last_message_at": now(), "status": "open"}
        self.db.conversations.insert_one(dict(doc))
        return doc

    def touch_conversation(self, cid: str, **fields) -> None:
        self.db.conversations.update_one({"conversation_id": cid}, {"$set": {"last_message_at": now(), **fields}})

    def conversations(self) -> list[dict]:
        return list(self.db.conversations.find({}, NOID).sort("last_message_at", DESCENDING))

    def add_message(self, **fields) -> dict:
        doc = {"message_id": new_id("msg"), "created_at": now(), **fields}
        self.db.messages.insert_one(dict(doc))
        return doc

    def message(self, mid: str) -> dict | None:
        return self.db.messages.find_one({"message_id": mid}, NOID)

    def update_message(self, mid: str, **fields) -> None:
        self.db.messages.update_one({"message_id": mid}, {"$set": fields})

    def claim_message_for_send(self, mid: str) -> bool:
        """Atomic pending_review -> sending, so a draft cannot be sent twice."""
        r = self.db.messages.update_one({"message_id": mid, "status": "pending_review"}, {"$set": {"status": "sending"}})
        return r.modified_count == 1

    def messages(self, conversation_id: str) -> list[dict]:
        return list(self.db.messages.find({"conversation_id": conversation_id}, NOID).sort("created_at", ASCENDING))

    def find_by_rfc_ids(self, ids: list[str]) -> dict | None:
        ids = [i for i in ids if i]
        return self.db.messages.find_one({"rfc_message_id": {"$in": ids}}, NOID) if ids else None

    def pending_messages(self) -> list[dict]:
        return list(self.db.messages.find({"status": "pending_review"}, NOID).sort("created_at", DESCENDING))

    def count_auto_replies_since(self, conversation_id: str, since: datetime) -> int:
        return self.db.messages.count_documents({"conversation_id": conversation_id, "kind": "auto_reply", "created_at": {"$gte": since}})

    def mark_seen(self, rfc_id: str) -> bool:
        """True if this is the first time we see the id (dedupe for IMAP re-fetches)."""
        try:
            self.db.inbox_seen.insert_one({"_id": rfc_id, "at": now()})
            return True
        except DuplicateKeyError:
            return False

    # --- claims & reminders
    def add_claim(self, doc: dict) -> dict:
        doc = {"claim_id": new_id("claim"), "created_at": now(), **doc}
        self.db.claims.insert_one(dict(doc))
        return doc

    def claims(self) -> list[dict]:
        return list(self.db.claims.find({}, NOID).sort("created_at", DESCENDING))

    def last_reminder(self, invoice_number: str) -> dict | None:
        return self.db.reminders.find_one({"invoice_number": invoice_number, "status": "sent"}, NOID, sort=[("sent_at", DESCENDING)])

    def add_reminder(self, doc: dict) -> None:
        self.db.reminders.insert_one(dict(doc))

    def reminders(self, limit: int = 100) -> list[dict]:
        return list(self.db.reminders.find({}, NOID).sort("sent_at", DESCENDING).limit(limit))

    # --- bank statements
    def add_statement(self, doc: dict, txns: list[dict]) -> None:
        self.db.statements.insert_one(dict(doc))
        if txns:
            self.db.transactions.insert_many([dict(t) for t in txns])

    def statements(self) -> list[dict]:
        return list(self.db.statements.find({}, NOID).sort("uploaded_at", DESCENDING))

    def statement(self, sid: str) -> dict | None:
        return self.db.statements.find_one({"statement_id": sid}, NOID)

    def transactions(self, statement_id: str) -> list[dict]:
        return list(self.db.transactions.find({"statement_id": statement_id}, NOID).sort("row_no", ASCENDING))

    def transaction(self, tid: str) -> dict | None:
        return self.db.transactions.find_one({"txn_id": tid}, NOID)

    def update_transaction(self, tid: str, **fields) -> None:
        self.db.transactions.update_one({"txn_id": tid}, {"$set": fields})

    def claim_transaction(self, tid: str, from_status: str, to_status: str) -> bool:
        r = self.db.transactions.update_one({"txn_id": tid, "status": from_status}, {"$set": {"status": to_status}})
        return r.modified_count == 1

    def approved_fingerprints(self) -> dict[str, str]:
        return {t["fingerprint"]: t["txn_id"] for t in self.db.transactions.find({"status": "approved"}, NOID)}

    def add_payment(self, doc: dict) -> None:
        self.db.payments.insert_one(dict(doc))

    def payments(self) -> list[dict]:
        return list(self.db.payments.find({}, NOID).sort("approved_at", DESCENDING))

    # --- reset
    def clear_runtime(self) -> None:
        for name in ("conversations", "messages", "claims", "reminders", "statements", "transactions", "payments", "inbox_seen"):
            self.db[name].delete_many({})
