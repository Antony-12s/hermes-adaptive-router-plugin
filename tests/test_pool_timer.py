import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from dashboard import pool_actions
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
        self.assertEqual(self.store.alerts(), {"desktop": False, "telegram": False, "threshold": 90, "auto_balance": False})
        with self.assertRaises(ValueError):
            self.store.set_alerts({"auto_balance": "on"})


class BalancePool(Pool):
    """Just enough of Hermes' credential pool for pool_actions.apply_balance."""

    def mark_exhausted_and_rotate(self, status_code, credential_id, error_context):
        entry = next(e for e in self._entries if e.id == credential_id)
        entry.last_status = "exhausted"
        entry.last_error_reason = error_context["reason"]
        entry.last_error_message = error_context["message"]

    def reset_status(self, credential_id):
        entry = next(e for e in self._entries if e.id == credential_id)
        entry.last_status = entry.last_error_reason = entry.last_error_message = None

    def move_entry(self, credential_id, position):
        entry = next(e for e in self._entries if e.id == credential_id)
        self._entries.remove(entry)
        self._entries.insert(position, entry)
        for index, item in enumerate(self._entries):
            item.priority = index


def account(entry_id, label, priority):
    return SimpleNamespace(id=entry_id, label=label, priority=priority, last_status=None,
                           last_error_reason=None, last_error_message=None)


class AutoBalanceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name)
        self.store = state.Store(self.home)
        self.db = self.home / "harp-activity.sqlite3"
        self.max = account("m", "Max", 0)
        self.pro = account("p", "Pro", 1)
        self.pools = {"anthropic": BalancePool(self.pro, self.max), "openai-codex": BalancePool()}
        self.usage = {"m": 0, "p": 98}
        self.sent = []

    def tearDown(self):
        self.dir.cleanup()

    def run_once(self, now):
        def read(provider, entry):
            return SimpleNamespace(windows=[window("Current session", self.usage[entry.id], datetime.fromtimestamp(now + 3600)),
                                            window("Current week", 10, datetime(2026, 12, 1))])
        return state.run_timer(self.store, self.db, self.pools.__getitem__, datetime.fromtimestamp(now),
                               read=read, send=lambda *message: self.sent.append(message), balance=pool_actions)

    def test_auto_balance_alone_needs_the_timer(self):
        self.assertFalse(state.timer_needed(self.store))
        self.store.set_alerts({"auto_balance": True})
        self.assertTrue(state.timer_needed(self.store))

    def test_off_by_default_changes_nothing(self):
        self.run_once(1_000_000)
        self.assertIsNone(self.pro.last_status)

    def test_benches_the_hot_account_and_puts_the_free_one_first(self):
        self.store.set_alerts({"auto_balance": True})
        lines = self.run_once(1_000_000)
        self.assertEqual(self.pro.last_status, "exhausted")
        self.assertTrue(pool_actions.benched_by_harp(self.pro))
        self.assertEqual([e.id for e in sorted(self.pools["anthropic"].entries(), key=lambda e: e.priority)], ["m", "p"])
        self.assertTrue(any("benched Pro" in line for line in lines))
        self.assertEqual([e["kind"] for e in state.read_events(self.db, "anthropic", 0)], ["balance"])
        self.assertEqual(self.sent, [])  # no channel on: switch quietly

    def test_a_second_run_with_the_same_usage_does_not_rebalance(self):
        self.store.set_alerts({"auto_balance": True})
        self.run_once(1_000_000)
        self.run_once(1_000_300)
        self.assertEqual([e["kind"] for e in state.read_events(self.db, "anthropic", 0)], ["balance"])

    def test_releases_the_account_once_its_window_resets(self):
        self.store.set_alerts({"auto_balance": True})
        self.run_once(1_000_000)
        self.usage["p"] = 5
        self.run_once(1_000_300)
        self.assertIsNone(self.pro.last_status)

    def test_never_benches_every_account(self):
        self.store.set_alerts({"auto_balance": True})
        self.usage.update(m=95, p=98)
        self.run_once(1_000_000)
        self.assertIsNone(self.pro.last_status)
        self.assertIsNone(self.max.last_status)

    def test_tells_the_enabled_channel_which_account_now_runs(self):
        self.store.set_alerts({"auto_balance": True, "desktop": True, "threshold": 99})
        self.run_once(1_000_000)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Pro", self.sent[0][1])
        self.assertIn("Max", self.sent[0][1])
