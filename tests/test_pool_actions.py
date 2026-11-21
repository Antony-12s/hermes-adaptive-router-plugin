import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from dashboard.pool_actions import BENCH_MARK, Row, account_peak, apply_balance, plan_balance, wake_ready


def window(label, percent, reset=None, scope="account"):
    return SimpleNamespace(label=label, used_percent=percent, reset_at=reset, scope=scope)


class FakePool:
    def __init__(self, entries):
        self._entries = entries
        self.calls = []

    def entries(self):
        return list(self._entries)

    def mark_exhausted_and_rotate(self, **kwargs):
        self.calls.append(("bench", kwargs["credential_id"], kwargs["error_context"]["message"]))

    def reset_status(self, credential_id):
        self.calls.append(("release", credential_id))

    def move_entry(self, credential_id, priority):
        self.calls.append(("move", credential_id, priority))


class BalancePlanTests(unittest.TestCase):
    def test_peak_ignores_model_scoped_and_unknown_windows(self):
        reset = datetime(2026, 11, 21, tzinfo=timezone.utc)
        snapshot = SimpleNamespace(windows=[
            window("Current session", 40, reset), window("Opus week", 99, scope="model"), window("Week", None)])
        self.assertEqual(account_peak(snapshot), (40, reset.timestamp()))
        self.assertIsNone(account_peak(SimpleNamespace(windows=[window("Opus week", 99, scope="model")])))

    def test_benches_at_threshold_and_orders_by_usage_with_unknown_and_dead_last(self):
        rows = [
            Row("a", "a", 0, "ok", (95, 10.0), False),
            Row("b", "b", 1, "ok", None, False),
            Row("c", "c", 2, "ok", (20, None), False),
            Row("d", "d", 3, "dead", None, False),
            Row("e", "e", 4, "exhausted", (50, None), True),
        ]
        plan = plan_balance(rows)
        self.assertEqual(plan["bench"], ["a"])
        self.assertEqual(plan["release"], ["e"])
        self.assertEqual(plan["order"], ["c", "e", "a", "b", "d"])

    def test_never_benches_whole_pool_below_full_and_skips_without_readings(self):
        rows = [Row("p", "p", 0, "parked", (99, None), False), Row("a", "a", 1, "ok", (10, None), False)]
        plan = plan_balance(rows)
        self.assertEqual((plan["bench"], plan["order"]), ([], ["a", "p"]))
        rows = [Row("a", "a", 0, "ok", (92, None), False), Row("b", "b", 1, "ok", (100, None), False)]
        self.assertEqual(plan_balance(rows)["bench"], ["b"])
        self.assertIsNone(plan_balance([Row("a", "a", 0, "ok", None, False)]))

    def test_apply_marks_harp_benches_and_moves_only_when_order_changes(self):
        pool = FakePool([SimpleNamespace(id="a", priority=0), SimpleNamespace(id="c", priority=1)])
        apply_balance(pool, {"bench": ["a"], "release": [], "order": ["c", "a"],
                             "peaks": {"a": (95, 10.0)}}, threshold=90)
        self.assertEqual(pool.calls[0][:2], ("bench", "a"))
        self.assertIn(BENCH_MARK, pool.calls[0][2])
        self.assertEqual(pool.calls[1:], [("move", "c", 0), ("move", "a", 1)])
        pool.calls.clear()
        apply_balance(pool, {"bench": [], "release": [], "order": ["a", "c"], "peaks": {}}, threshold=90)
        self.assertEqual(pool.calls, [])


class WakeTests(unittest.TestCase):
    def test_wake_needs_a_reading_whose_session_window_is_not_running(self):
        idle = SimpleNamespace(windows=[window("Current session", 0, None)])
        running = SimpleNamespace(windows=[window("Current session", 3, datetime.now(timezone.utc))])
        self.assertTrue(wake_ready(idle))
        self.assertFalse(wake_ready(running))
        self.assertFalse(wake_ready(None))
        self.assertFalse(wake_ready(SimpleNamespace(windows=[])))
