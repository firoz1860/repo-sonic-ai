"""MongoDB connection and index setup. `mongomock://` URIs give an in-memory database for tests."""
from __future__ import annotations

from pymongo import ASCENDING, MongoClient

from .config import Settings

COLLECTIONS = ["organization", "customers", "invoices", "conversations", "messages", "claims", "reminders",
               "statements", "transactions", "payments", "inbox_seen"]


def connect(settings: Settings):
    if settings.mongodb_uri.startswith("mongomock://"):
        import mongomock

        client = mongomock.MongoClient()
    else:
        client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    db = client[settings.mongodb_db]
    ensure_indexes(db)
    return db


def ensure_indexes(db) -> None:
    db.customers.create_index([("customer_id", ASCENDING)], unique=True)
    db.invoices.create_index([("invoice_number", ASCENDING)], unique=True)
    db.invoices.create_index([("customer_id", ASCENDING), ("status", ASCENDING)])
    db.conversations.create_index([("customer_id", ASCENDING), ("last_message_at", ASCENDING)])
    db.messages.create_index([("conversation_id", ASCENDING), ("created_at", ASCENDING)])
    db.messages.create_index([("status", ASCENDING)])
    db.messages.create_index([("rfc_message_id", ASCENDING)])
    db.transactions.create_index([("statement_id", ASCENDING), ("row_no", ASCENDING)], unique=True)
    db.reminders.create_index([("invoice_number", ASCENDING), ("sent_at", ASCENDING)])
    db.payments.create_index([("invoice_number", ASCENDING)])
