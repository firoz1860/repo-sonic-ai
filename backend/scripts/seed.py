"""Reload the demo data from /data (same as the UI's "Reset demo data" button).

    python -m scripts.seed [--keep-emails]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.db import connect  # noqa: E402
from app.repositories.store import Store  # noqa: E402
from app.services.seed_service import seed  # noqa: E402

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--keep-emails", action="store_true")
    args = p.parse_args()
    s = Settings.from_env()
    print(seed(Store(connect(s)), s, keep_emails=args.keep_emails))
