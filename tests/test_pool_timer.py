import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from dashboard import pool_state as state


def window(label, pct, reset=None):
    return SimpleNamespace(label=label, used_percent=pct, reset_at=reset, scope="account")


class Pool:
    def __init__(self, *entries):
        self._entries = list(entries)

    def entries(self):
        return list(self._entries)


class TimerRunTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name)
        self.store = state.Store(self.home)
        self.db = self.home / "harp-activity.sqlite3"
        self.entry = SimpleNamespace(id="e1", label="a", priority=0, last_status=None, last_error_reason=None)
        self.pools = {"anthropic": Pool(self.entry), "openai-codex": Pool()}
        self.sent = []

    def tearDown(self):
        self.dir.cleanup()

    def run_once(self, pct, now):
        reading = SimpleNamespace(windows=[window("Current session", pct), window("Current week", 10, datetime(2026, 11, 24))])
        return state.run_timer(self.store, self.db, self.pools.__getitem__, datetime.fromtimestamp(now),
                               read=lambda provider, entry: reading, send=lambda *message: self.sent.append(message))

    def test_without_alerts_it_only_applies_schedules(self):
        self.run_once(95, 1_000_000)
        self.assertEqual(self.sent, [])
        self.assertEqual(state.report(self.db, "anthropic", 24, 1_000_000)["snapshots"], 0)

    def test_alerts_record_snapshots_send_once_and_log(self):
        self.store.set_alerts({"desktop": True, "telegram": False, "threshold": 90})
        self.assertTrue(state.timer_needed(self.store))
        self.run_once(95, 1_000_000)
        self.run_once(96, 1_000_300)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("a 5-hour window at 95%", self.sent[0][1])
        self.assertIn("session:a", self.store.alert_state()["anthropic"])
        self.assertEqual(state.report(self.db, "anthropic", 24, 1_000_300)["snapshots"], 2)
        self.assertEqual([e["kind"] for e in state.read_events(self.db, "anthropic", 0)], ["alert"])
        self.run_once(20, 1_000_600)
        self.assertEqual(self.store.alert_state().get("anthropic", {}), {})
        self.assertEqual(self.store.last_tick(), 1_000_600)

    def test_alert_settings_are_validated(self):
        for bad in ({"threshold": 0}, {"threshold": 101}, {"desktop": "yes"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.set_alerts(bad)
        self.assertEqual(self.store.alerts(), {"desktop": False, "telegram": False, "threshold": 90})
