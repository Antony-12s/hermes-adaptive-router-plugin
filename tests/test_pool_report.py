import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from dashboard import pool_state as state

NOW = datetime(2026, 11, 23, 12, 0).timestamp()


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = Path(self.dir.name) / "a.sqlite3"

    def tearDown(self):
        self.dir.cleanup()

    def test_report_groups_tokens_by_account_model_source_and_session(self):
        state.record_call(self.db, "anthropic", "a", "s1", "desktop", 300, NOW - 600, model="opus")
        state.record_call(self.db, "anthropic", "a", "s1", "desktop", 100, NOW - 300, model="opus")
        state.record_call(self.db, "anthropic", "b", "s2", "telegram", 100, NOW - 60, model="haiku")
        state.record_call(self.db, "anthropic", "b", "s3", "cli", 999, NOW - 30 * 3600, model="haiku")
        state.record_call(self.db, "openai-codex", "c", "s4", "cli", 50, NOW - 60, model="gpt")
        report = state.report(self.db, "anthropic", 24, NOW)
        self.assertEqual(report["total"], {"tokens": 500, "requests": 3})
        self.assertEqual([(r["name"], r["tokens"], r["share"]) for r in report["accounts"]], [("a", 400, 80.0), ("b", 100, 20.0)])
        self.assertEqual([r["name"] for r in report["models"]], ["opus", "haiku"])
        self.assertEqual([r["name"] for r in report["sources"]], ["desktop", "telegram"])
        self.assertEqual(report["sessions"][0]["session"], "s1")
        self.assertEqual(report["sessions"][0]["requests"], 2)
        self.assertEqual(len(report["chart"]), 7)
        self.assertEqual(sum(sum(day["tokens"]) for day in report["chart"]), 1499)

    def test_report_adds_peak_and_time_at_90_from_usage_snapshots(self):
        for minutes, pct in ((20, 50), (15, 92), (10, 95), (5, 60)):
            state.record_snapshot(self.db, "anthropic", "a", pct, NOW - minutes * 60)
        state.record_call(self.db, "anthropic", "a", "s1", "desktop", 10, NOW - 60, model="opus")
        row = state.report(self.db, "anthropic", 24, NOW)["accounts"][0]
        self.assertEqual((row["peak"], row["hot_min"]), (95, 10))

    def test_legacy_token_rows_still_count(self):
        with state.closing(state._open(self.db)) as con, con:
            con.execute("INSERT INTO tokens VALUES ('anthropic', 'a', ?, 70)", (int(NOW // 300) * 300,))
        state.record_call(self.db, "anthropic", "a", "s1", "desktop", 30, NOW, model="opus")
        self.assertEqual(state.account_activity(self.db, "anthropic", NOW)["a"]["tokens_24h"], 100)
        report = state.report(self.db, "anthropic", 24, NOW)
        self.assertEqual((report["total"], report["accounts"][0]["tokens"]), ({"tokens": 100, "requests": 1}, 100))
        self.assertEqual([s["session"] for s in report["sessions"]], ["s1"])


    def test_combined_chart_sums_providers_and_supports_a_longer_range(self):
        state.record_call(self.db, "anthropic", "a", "s1", "desktop", 100, NOW - 600, model="opus")
        state.record_call(self.db, "openai-codex", "c", "s2", "cli", 50, NOW - 600, model="sol")
        state.record_call(self.db, "anthropic", "a", "s3", "desktop", 10, NOW - 20 * 86400, model="opus")
        week = state.combined_chart(self.db, ["anthropic", "openai-codex"], 7, NOW)
        self.assertEqual(len(week), 7)
        self.assertEqual(sum(sum(d["tokens"]) for d in week), 150)
        month = state.combined_chart(self.db, ["anthropic", "openai-codex"], 30, NOW)
        self.assertEqual(len(month), 30)
        self.assertEqual(sum(sum(d["tokens"]) for d in month), 160)
        self.assertEqual(state.combined_chart(Path(self.dir.name) / "missing.sqlite3", ["anthropic"], 7, NOW), state.combined_chart(self.db, [], 7, NOW))


class EventTests(unittest.TestCase):
    def test_events_newest_first_within_period(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "a.sqlite3"
            state.log_event(db, "anthropic", "park", "Parked a", "page", NOW - 3 * 86400)
            state.log_event(db, "anthropic", "balance", "Balanced", "page", NOW - 60)
            state.log_event(db, "anthropic", "alert", "b at 95%", "timer", NOW - 30)
            state.log_event(db, "openai-codex", "park", "Parked c", "page", NOW - 30)
            events = state.read_events(db, "anthropic", NOW - 86400)
            self.assertEqual([e["kind"] for e in events], ["alert", "balance"])
            self.assertEqual(state.read_events(Path(directory) / "none.sqlite3", "anthropic", 0), [])


class HealthTests(unittest.TestCase):
    def facts(self, **changes):
        base = {"now": NOW, "timer_needed": True, "timer_installed": True, "last_tick": NOW - 120,
                "tracker_last": NOW - 600, "account_pool_timer": False, "live_read_at": NOW - 60,
                "live_ids": {"e1", "e2"}, "can_read": True,
                "accounts": [{"id": "e1", "label": "a", "status": None, "reason": None, "message": None, "reset_at": None},
                             {"id": "e2", "label": "b", "status": None, "reason": None, "message": None, "reset_at": None}]}
        base.update(changes)
        return base

    def levels(self, facts):
        return {c["text"].split(":")[0]: c["level"] for c in state.health_checks(facts)}

    def test_all_good(self):
        self.assertEqual({c["level"] for c in state.health_checks(self.facts())}, {"ok"})

    def test_problems_are_named(self):
        accounts = [{"id": "e1", "label": "a", "status": "dead", "reason": None, "message": None, "reset_at": None},
                    {"id": "e2", "label": "a", "status": "exhausted", "reason": "rate_limit", "message": None,
                     "reset_at": NOW + 3600},
                    {"id": "e3", "label": "c", "status": None, "reason": None, "message": None, "reset_at": None}]
        checks = state.health_checks(self.facts(timer_installed=False, last_tick=None, tracker_last=None,
                                                account_pool_timer=True, live_ids={"e2"}, accounts=accounts))
        text = " | ".join(f"{c['level']} {c['text']}" for c in checks)
        for expected in ("fail HARP Timer", "warn Tracker", "warn Two balance owners", "fail a needs to sign in again",
                         "warn a is rate-limited", "warn Duplicate", "warn No usage reading"):
            self.assertIn(expected, text)

    def test_parked_and_harp_bench_are_not_problems(self):
        accounts = [{"id": "e1", "label": "a", "status": "exhausted", "reason": state.PARK_REASON, "message": None, "reset_at": None},
                    {"id": "e2", "label": "b", "status": "exhausted", "reason": "rate_limit_error",
                     "message": "[HARP balance] usage 95% >= 90%", "reset_at": NOW + 60}]
        checks = state.health_checks(self.facts(accounts=accounts))
        self.assertEqual({c["level"] for c in checks}, {"ok"})




class AlertTests(unittest.TestCase):
    def test_raises_once_and_clears(self):
        accounts = [{"label": "a", "status": None, "session": 95, "week": 40, "week_reset": "w1"},
                    {"label": "b", "status": "dead", "session": None, "week": None, "week_reset": None}]
        active, raised, cleared = state.evaluate_alerts({}, accounts, threshold=90, now=NOW)
        self.assertEqual(sorted(raised), ["relogin:b", "session:a"])
        self.assertEqual(cleared, [])
        again, raised, cleared = state.evaluate_alerts(active, accounts, threshold=90, now=NOW + 300)
        self.assertEqual((raised, cleared), ([], []))
        self.assertEqual(again["session:a"]["since"], NOW)
        accounts[0]["session"] = 10
        accounts[1]["status"] = None
        _, raised, cleared = state.evaluate_alerts(again, accounts, threshold=90, now=NOW + 600)
        self.assertEqual((raised, sorted(cleared)), ([], ["relogin:b", "session:a"]))

    def test_week_alert_is_once_per_week_and_all_full_is_one_alert(self):
        accounts = [{"label": "a", "status": None, "session": 91, "week": 92, "week_reset": "w1"},
                    {"label": "b", "status": None, "session": 93, "week": 10, "week_reset": "w1"}]
        active, raised, _ = state.evaluate_alerts({}, accounts, threshold=90, now=NOW)
        self.assertIn("week:a:w1", raised)
        self.assertIn("all", raised)
        self.assertEqual(active["all"]["text"], "Every account is at 90% or more")
