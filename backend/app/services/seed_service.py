"""Load / reset the demo data from the JSON files in /data."""
from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..repositories.store import Store


def _load(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def seed(store: Store, settings: Settings, keep_emails: bool = False) -> dict:
    d = settings.data_dir
    org, customers, invoices = _load(d / "organization.json"), _load(d / "customers.json"), _load(d / "invoices.json")
    kept = {c["customer_id"]: c["contact_email"] for c in store.customers()} if keep_emails else {}
    if settings.org_accounts_email:
        org["accounts_email"] = settings.org_accounts_email
    for c in customers:
        if c["customer_id"] in kept:
            c["contact_email"] = kept[c["customer_id"]]
        c["contact_email_lc"] = c["contact_email"].lower()
    for i in invoices:
        i["tds_deducted"] = 0.0
    store.clear_runtime()
    for name, docs in (("organization", [org]), ("customers", customers), ("invoices", invoices)):
        store.db[name].delete_many({})
        store.db[name].insert_many(docs)
    return {"customers": len(customers), "invoices": len(invoices)}


def ensure_seeded(store: Store, settings: Settings) -> None:
    if store.db.customers.count_documents({}) == 0:
        seed(store, settings)
