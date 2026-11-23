"""Optional one-time seed: teach a profile's tier learner from the synthetic training prompts.

    python -m training.seed [--home PATH] [--dry-run]

A fresh install knows nothing, so every message lands on the neutral tier until Jev or your corrections teach it.
This copies the 400 training prompts (not the held-out ones) into the profile's router database as low-weight
samples marked "seed", so the learner starts with a sensible spread of easy and hard work. The prompts are
synthetic; real corrections and verified runs outweigh them. Running it twice adds nothing new. The HARP
plugin never runs this itself.
"""

import argparse
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from dashboard import router
from training import train

SEED_WEIGHT = 0.5


def default_home():
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"])
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "hermes" if base else Path.home() / ".hermes"


def seed(home, rows, dry_run=False):
    """Insert training rows as seed samples. Returns how many were new."""
    home = Path(home)
    path = home / "harp-router.sqlite3"
    if dry_run:
        return sum(1 for row in rows if row["split"] == "train")
    router.Store(path)     # creates the tables when the profile has no router database yet
    added = 0
    with closing(sqlite3.connect(path)) as db, db:
        known = {text for (text,) in db.execute("SELECT text FROM samples WHERE source='seed'")}
        for row in rows:
            if row["split"] != "train" or row["prompt"] in known:
                continue
            db.execute("INSERT INTO samples (ts, text, label, difficulty, weight, source, grp) VALUES (?,?,?,?,?,?,?)",
                       (time.time(), row["prompt"], str(row["tier"]), row["tier"], SEED_WEIGHT, "seed", row["task_group"]))
            added += 1
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default=None, help="Hermes profile folder (default: the usual Hermes home)")
    parser.add_argument("--dry-run", action="store_true", help="count what would be added without writing")
    args = parser.parse_args()
    home = Path(args.home) if args.home else default_home()
    count = seed(home, train.load_rows() + train.load_extra(), args.dry_run)
    print(f"{'Would add' if args.dry_run else 'Added'} {count} seed prompts to {home / 'harp-router.sqlite3'}")


if __name__ == "__main__":
    main()
