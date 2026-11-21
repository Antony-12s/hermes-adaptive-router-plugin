import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from dashboard import pool_state as state


class ScheduleTests(unittest.TestCase):
    def test_parses_days_and_hours(self):
        self.assertEqual(state.parse_schedule("mon-fri 09:00-17:00"), ({0, 1, 2, 3, 4}, 540, 1020))
        self.assertEqual(state.parse_schedule("sat,sun 22:00-06:00"), ({5, 6}, 1320, 360))
        for bad in ("", "mon", "xyz 09:00-10:00", "mon 25:00-26:00", "mon 09:00-09:00"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                state.parse_schedule(bad)

    def test_window_end_inside_and_outside(self):
        spec = "mon-fri 09:00-17:00"
        monday_noon = datetime(2026, 11, 23, 12, 0)  # a Monday
        self.assertEqual(state.window_end(spec, monday_noon), datetime(2026, 11, 23, 17, 0))
        self.assertIsNone(state.window_end(spec, datetime(2026, 11, 23, 18, 0)))
        self.assertIsNone(state.window_end(spec, datetime(2026, 11, 22, 12, 0)))  # Sunday

    def test_overnight_window_belongs_to_its_start_day(self):
        spec = "fri 22:00-06:00"
        self.assertEqual(state.window_end(spec, datetime(2026, 11, 27, 23, 0)), datetime(2026, 11, 28, 6, 0))
        self.assertEqual(state.window_end(spec, datetime(2026, 11, 28, 5, 0)), datetime(2026, 11, 28, 6, 0))
        self.assertIsNone(state.window_end(spec, datetime(2026, 11, 27, 5, 0)))


class FakePool:
    def __init__(self, *ids):
        self.items = {i: SimpleNamespace(id=i, label=i, priority=n, last_status=None,
                                         last_error_reason=None) for n, i in enumerate(ids)}
        self.calls = []

    def entries(self):
        return list(self.items.values())

    def mark_exhausted_and_rotate(self, status_code, credential_id, error_context):
        self.calls.append(("hold", credential_id, error_context["reset_at"]))
        entry = self.items[credential_id]
        entry.last_status, entry.last_error_reason = "exhausted", error_context["reason"]

    def reset_status(self, credential_id):
        self.calls.append(("release", credential_id))
        self.items[credential_id].last_status = None


class ParkingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = state.Store(Path(self.dir.name))

    def tearDown(self):
        self.dir.cleanup()

    def test_park_holds_entry_and_unpark_releases(self):
        pool = FakePool("a")
        state.park(self.store, pool, "anthropic", "a", until=None, by="user", now=100)
        self.assertEqual(pool.calls[0][:2], ("hold", "a"))
        self.assertGreater(pool.calls[0][2], 100 + 300 * 86400)
        self.assertEqual(self.store.parked("anthropic")["a"]["by"], "user")
        state.unpark(self.store, pool, "anthropic", "a")
        self.assertEqual(pool.calls[-1], ("release", "a"))
        self.assertEqual(self.store.parked("anthropic"), {})

    def test_tick_parks_in_window_and_returns_after_schedule_only(self):
        pool = FakePool("a", "b")
        self.store.set_schedule("anthropic", "a", "a", "mon-fri 09:00-17:00")
        state.park(self.store, pool, "anthropic", "b", until=None, by="user", now=0)
        monday_noon = datetime(2026, 11, 23, 12, 0)
        changes = state.tick(self.store, lambda provider: pool, monday_noon)
        self.assertEqual(changes, ["anthropic: parked a by schedule"])
        self.assertEqual(self.store.parked("anthropic")["a"]["by"], "schedule")
        changes = state.tick(self.store, lambda provider: pool, datetime(2026, 11, 23, 18, 0))
        self.assertEqual(changes, ["anthropic: a back in rotation"])
        self.assertIn("b", self.store.parked("anthropic"))  # a manual park stays

    def test_tick_forgets_parks_whose_time_passed_or_entry_left(self):
        pool = FakePool("a")
        state.park(self.store, pool, "anthropic", "a", until=time.time() - 1, by="user")
        self.store.save_park("anthropic", "gone", {"label": "gone", "until": None, "by": "user"})
        state.tick(self.store, lambda provider: pool, datetime.now())
        self.assertEqual(self.store.parked("anthropic"), {})


class TrackerTests(unittest.TestCase):
    def test_counts_tokens_per_account_for_24_hours_and_recent_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "t.sqlite3"
            now = 1_000_000.0
            state.record_call(db, "anthropic", "a", "s1", "desktop", 120, now - 25 * 3600)
            state.record_call(db, "anthropic", "a", "s1", "desktop", 30, now - 60)
            state.record_call(db, "anthropic", "a", "s2", "telegram", 20, now - 10)
            state.record_call(db, "anthropic", "a", "s3", "cli", None, now - 3600)
            summary = state.account_activity(db, "anthropic", now)
            self.assertEqual(summary["a"]["tokens_24h"], 50)
            self.assertEqual(summary["a"]["used_by"], ["desktop", "telegram"])
            self.assertEqual(state.account_activity(Path(directory) / "missing.sqlite3", "anthropic", now), {})
