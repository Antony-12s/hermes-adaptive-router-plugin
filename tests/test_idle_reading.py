"""Idle accounts stay readable, and a chat call that pushes an account over the line balances at once."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import __init__ as harp
from dashboard import pool_actions
from dashboard import pool_state as state


def window(label, pct, reset=None):
    return SimpleNamespace(label=label, used_percent=pct, reset_at=reset, scope="account")


def account(entry_id, label, priority, expired=False):
    return SimpleNamespace(id=entry_id, label=label, priority=priority, last_status=None, last_error_reason=None,
                           last_error_message=None, expired=expired, token=f"old-{entry_id}")


class RefreshingPool:
    """Hermes' pool surface HARP uses: entries, the private refresh pair, and the balance mutations."""

    def __init__(self, *entries, refresh_fails=False):
        self._entries = list(entries)
        self.refreshed = []
        self.refresh_fails = refresh_fails

    def entries(self):
        return list(self._entries)

    def _entry_needs_refresh(self, entry):
        return entry.expired

    def _refresh_entry(self, entry, *, force):
        self.refreshed.append((entry.id, force))
        if self.refresh_fails:
            raise RuntimeError("token endpoint down")
        # Hermes persists a renewed credential with its status cleared (status_cleared_ids).
        fresh = SimpleNamespace(**{**vars(entry), "expired": False, "token": f"new-{entry.id}",
                                   "last_status": None, "last_error_reason": None, "last_error_message": None,
                                   "last_error_reset_at": None})
        self._entries[self._entries.index(entry)] = fresh
        return fresh

    def mark_exhausted_and_rotate(self, status_code, credential_id, error_context):
        entry = next(e for e in self._entries if e.id == credential_id)
        entry.last_status = "exhausted"
        entry.last_error_reason = error_context["reason"]
        entry.last_error_message = error_context["message"]
        entry.last_error_reset_at = error_context["reset_at"]

    def reset_status(self, credential_id):
        entry = next(e for e in self._entries if e.id == credential_id)
        entry.last_status = entry.last_error_reason = entry.last_error_message = None

    def move_entry(self, credential_id, position):
        entry = next(e for e in self._entries if e.id == credential_id)
        self._entries.remove(entry)
        self._entries.insert(position, entry)
        for index, item in enumerate(self._entries):
            item.priority = index


class FreshEntryTests(unittest.TestCase):
    def test_an_expired_token_is_renewed_through_the_pool_before_reading(self):
        idle = account("m", "Max", 0, expired=True)
        pool = RefreshingPool(idle)
        fresh = pool_actions.fresh_entry(pool, idle)
        self.assertEqual(fresh.token, "new-m")
        self.assertEqual(pool.refreshed, [("m", False)])

    def test_renewing_a_benched_account_keeps_its_bench_and_reset(self):
        idle = account("m", "Max", 0, expired=True)
        idle.last_status, idle.last_error_reason = "exhausted", "rate_limit_error"
        idle.last_error_message, idle.last_error_reset_at = f"{pool_actions.BENCH_MARK} usage 100% >= 90%", 2_000_000.0
        pool = RefreshingPool(idle)
        fresh = pool_actions.fresh_entry(pool, idle)
        self.assertEqual(fresh.token, "new-m")
        self.assertEqual(fresh.last_status, "exhausted")
        self.assertEqual(fresh.last_error_reset_at, 2_000_000.0)
        self.assertTrue(pool_actions.benched_by_harp(fresh))

    def test_a_valid_token_is_left_alone(self):
        live = account("p", "Pro", 0)
        pool = RefreshingPool(live)
        self.assertIs(pool_actions.fresh_entry(pool, live), live)
        self.assertEqual(pool.refreshed, [])

    def test_a_failed_refresh_or_an_older_hermes_keeps_the_entry(self):
        idle = account("m", "Max", 0, expired=True)
        self.assertIs(pool_actions.fresh_entry(RefreshingPool(idle, refresh_fails=True), idle), idle)
        self.assertIs(pool_actions.fresh_entry(SimpleNamespace(), idle), idle)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name)
        self.store = state.Store(self.home)
        self.db = self.home / "harp-activity.sqlite3"
        self.max = account("m", "Max", 1, expired=True)
        self.pro = account("p", "Pro", 0)
        self.pool = RefreshingPool(self.pro, self.max)
        self.usage = {"m": 10, "p": 95}

    def tearDown(self):
        self.dir.cleanup()

    def read(self, provider, entry):
        if entry.token.startswith("old-") and entry.expired:
            return None   # what Hermes' read-only fetch returns for an expired OAuth token
        return SimpleNamespace(windows=[window("Current session", self.usage[entry.id], datetime(2026, 12, 1)),
                                        window("Current week", 5, datetime(2026, 12, 3))])


class TimerReadsIdleAccountsTests(Fixture):
    def test_the_timer_renews_an_idle_account_so_it_has_a_reading(self):
        self.store.set_alerts({"auto_balance": True})
        state.run_timer(self.store, self.db, lambda provider: self.pool if provider == "anthropic" else RefreshingPool(),
                        datetime.fromtimestamp(1_000_000), read=self.read, send=lambda *m: None, balance=pool_actions)
        self.assertIn(("m", False), self.pool.refreshed)
        self.assertEqual(self.pool.entries()[0].id, "m")      # idle Max now runs first
        self.assertEqual(self.pro.last_status, "exhausted")   # hot Pro benched


class BalanceNowTests(Fixture):
    def test_balance_pool_benches_the_hot_account_and_logs_it_from_the_chat(self):
        text = state.balance_pool(self.db, self.pool, "anthropic", self.read, pool_actions, 1_000_000, "chat")
        self.assertIn("benched Pro", text)
        self.assertEqual([(e["kind"], e["by"]) for e in state.read_events(self.db, "anthropic", 0)], [("balance", "chat")])
        self.assertIsNone(state.balance_pool(self.db, self.pool, "anthropic", self.read, pool_actions, 1_000_060, "chat"))

    def test_a_chat_call_starts_one_balance_a_minute_and_only_when_auto_balance_is_on(self):
        started = []
        clock = iter([1000.0, 1010.0, 1100.0]).__next__
        harp._BALANCED_AT.clear()
        self.store.set_alerts({"auto_balance": False})
        harp.balance_soon(self.pool, "anthropic", self.home, clock=lambda: 1000.0, start=started.append)
        self.assertEqual(started, [])   # switched off
        self.store.set_alerts({"auto_balance": True})
        for _ in range(3):
            harp.balance_soon(self.pool, "anthropic", self.home, clock=clock, start=started.append)
        self.assertEqual(len(started), 2)   # 1000 and 1100; 1010 is inside the minute


if __name__ == "__main__":
    unittest.main()
