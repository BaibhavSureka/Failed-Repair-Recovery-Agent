"""Deterministic synthetic fixtures: python -m app.seed [--reset]."""
import argparse
import os
from pathlib import Path

from .db import Database


def main():
    parser = argparse.ArgumentParser(description="Initialize or reset the synthetic repair demo")
    parser.add_argument("--reset", action="store_true", help="Replace synthetic data with deterministic fixtures")
    args = parser.parse_args()
    if os.getenv("DEMO_MODE", "true").lower() not in {"true", "1", "yes"}:
        parser.error("Seeding is only supported in DEMO_MODE=true")
    path = os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parents[1] / "data" / "demo.sqlite3"))
    demo_date = os.getenv("DEMO_DATE", "2026-10-21")
    db = Database(path)
    db.initialize(demo_date)
    if args.reset:
        db.reset(demo_date)
    print(f"Synthetic demo {'reset' if args.reset else 'initialized'}: {path} (business date {demo_date})")


if __name__ == "__main__":
    main()
