import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dashboard import api_keys, delegation, router

NOW = time.mktime((2026, 11, 24, 15, 0, 0, 0, 0, -1))
KEYS = {"deepseek": True, "zai": True}
PRICES = {"deepseek-flash": 0.26, "deepseek-v4-pro": 0.99}
MODELS = {"deepseek": ["deepseek-v4-pro", "deepseek-flash"], "zai": ["glm-5.2", "glm-5"]}


def status(provider):
    return {"configured": KEYS.get(provider, False), "key_source": "DEEPSEEK_API_KEY" if provider == "deepseek" else ""}


def price(model, provider):
    return PRICES.get(model)


def models(provider):
    return MODELS.get(provider, [])


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, **providers):
        body = {"enabled": True, "daily_budget": 2.0, "order": ["zai", "deepseek"], "providers": providers}
        return api_keys.save(self.home, body)

    def log(self, provider, model, tokens, ts=NOW):
        store = router.Store(self.home / "harp-router.sqlite3")
        store.log_live("main", "x", provider, model, "", 1.0, tokens, 1, "ok", "")
        with closing(sqlite3.connect(self.home / "harp-router.sqlite3")) as db, db:
            db.execute("UPDATE live_calls SET ts = ? WHERE id = (SELECT MAX(id) FROM live_calls)", (ts,))

    def pick(self, tier):
        return api_keys.fallback(self.home, tier, status, price, models, now=NOW)

    def test_off_by_default_and_never_without_a_switch(self):
        self.assertIsNone(self.pick(3))
        self.config(deepseek={"use": False})
        self.assertIsNone(self.pick(3))

    def test_cheap_model_for_easy_work_strong_for_hard_work(self):
        self.config(deepseek={"use": True, "cheap": "deepseek-flash", "strong": "deepseek-v4-pro"})
        self.assertEqual(self.pick(1), {"provider": "deepseek", "model": "deepseek-flash", "effort": "low", "api": True})
        self.assertEqual(self.pick(3)["model"], "deepseek-v4-pro")
        self.assertEqual(self.pick(3)["effort"], "high")

    def test_a_provider_with_an_unknown_price_is_skipped_until_the_user_sets_one(self):
        self.config(zai={"use": True, "cheap": "glm-5", "strong": "glm-5.2"},
                    deepseek={"use": True, "cheap": "deepseek-flash", "strong": "deepseek-v4-pro"})
        self.assertEqual(self.pick(3)["provider"], "deepseek")      # GLM comes first but has no price
        self.config(zai={"use": True, "cheap": "glm-5", "strong": "glm-5.2", "price": 1.2})
        self.assertEqual(self.pick(3)["provider"], "zai")

    def test_a_provider_without_a_key_in_hermes_is_skipped(self):
        self.config(alibaba={"use": True, "cheap": "qwen3.6-flash", "strong": "qwen3.8-max", "price": 1.0})
        self.assertIsNone(self.pick(3))

    def test_todays_spend_stops_the_fallback_at_the_budget(self):
        self.config(deepseek={"use": True, "cheap": "deepseek-flash", "strong": "deepseek-v4-pro"})
        self.log("deepseek", "deepseek-v4-pro", 1_000_000)                 # $0.99
        self.log("deepseek", "deepseek-v4-pro", 1_000_000, ts=NOW - 86400)  # yesterday: not counted
        self.log("anthropic", "claude-opus-5-5", 9_000_000)               # subscription: not counted
        view = api_keys.overview(self.home, status, price, models, now=NOW)
        row = next(r for r in view["providers"] if r["id"] == "deepseek")
        self.assertEqual((row["tokens_today"], row["requests_today"], row["spent_today"]), (1_000_000, 1, 0.99))
        self.assertEqual(view["budget_left"], 1.01)
        self.assertIsNotNone(self.pick(3))
        self.log("deepseek", "deepseek-v4-pro", 2_000_000)
        self.assertIsNone(self.pick(3))

    def test_unpriced_tokens_are_reported_not_hidden(self):
        self.config(zai={"use": True})
        self.log("zai", "glm-5.2", 500_000)
        row = next(r for r in api_keys.overview(self.home, status, price, models, now=NOW)["providers"] if r["id"] == "zai")
        self.assertEqual((row["unpriced_tokens"], row["spent_today"]), (500_000, 0.0))

    def test_overview_never_carries_a_key_and_defaults_models_from_the_catalog(self):
        view = api_keys.overview(self.home, status, price, models, now=NOW)
        row = next(r for r in view["providers"] if r["id"] == "deepseek")
        self.assertEqual((row["cheap"], row["strong"], row["env"]), ("deepseek-flash", "deepseek-v4-pro", "DEEPSEEK_API_KEY"))
        self.assertNotIn("sk-", json.dumps(view))
        self.assertEqual([r["id"] for r in view["providers"]], list(api_keys.IDS))

    def test_validation(self):
        for bad in ({"enabled": "yes", "daily_budget": 1}, {"enabled": True, "daily_budget": -1},
                    {"enabled": True, "daily_budget": 1, "providers": {"nope": {}}},
                    {"enabled": True, "daily_budget": 1, "providers": {"deepseek": {"cheap": "bad model!"}}},
                    {"enabled": True, "daily_budget": 1, "providers": {"deepseek": {"price": 5000}}}):
            with self.assertRaises(ValueError):
                api_keys.validate(bad)
        saved = api_keys.validate({"enabled": True, "daily_budget": 3, "order": ["gemini", "gemini", "x"], "providers": {}})
        self.assertEqual(saved["order"][0], "gemini")
        self.assertEqual(sorted(saved["order"]), sorted(api_keys.IDS))


class DelegationFallbackTests(unittest.TestCase):
    def engine(self, near):
        return SimpleNamespace(home="h", settings=lambda: {}, loads=lambda s: {"anthropic": {"pool_near_limit": near}})

    def test_a_saved_route_moves_to_an_api_key_only_when_its_pool_is_near_the_limit(self):
        route = {"provider": "anthropic", "model": "claude-opus-5-5", "reasoning_effort": "high"}
        seen = []
        fallback = lambda home, tier: seen.append(tier) or {"provider": "deepseek", "model": "deepseek-v4-pro"}
        self.assertEqual(delegation.api_fallback(route, "local", self.engine(False), fallback), (route, "local"))
        moved, source = delegation.api_fallback(route, "local", self.engine(True), fallback)
        self.assertEqual((moved["provider"], moved["model"], moved["reasoning_effort"], source),
                         ("deepseek", "deepseek-v4-pro", "high", "local+api"))
        self.assertEqual(seen, [3])
        self.assertEqual(delegation.api_fallback(route, "local", self.engine(True), lambda h, t: None), (route, "local"))

    def test_jobs_can_name_an_api_provider(self):
        saved = delegation.validate({"classifier": "local", "jobs": [
            {"name": "Cheap batch", "description": "", "provider": "deepseek", "model": "deepseek-flash", "effort": "low"}]})
        self.assertEqual(saved["jobs"][0]["provider"], "deepseek")
        route = {"provider": "deepseek", "model": "deepseek-flash", "reasoning_effort": "low"}
        self.assertEqual(delegation.api_fallback(route, "manual", self.engine(True), lambda h, t: 1/0), (route, "manual"))



class LadderDelegationTests(unittest.TestCase):
    def engine(self, pick="anthropic:claude-opus-5-5", effort="medium", ladder=True):
        decisions = []

        def decide(goal, log=True, use_jev=True, jev_always=False):
            decisions.append((goal, log, use_jev, jev_always))
            return {"pick": pick, "effort": effort}
        engine = SimpleNamespace(home="h", settings=lambda: {"ladder": ladder}, decide=decide,
                                 loads=lambda s: {"anthropic": {"pool_near_limit": False}})
        return engine, decisions

    def test_a_task_with_no_saved_work_uses_the_ladder_pick(self):
        engine, decisions = self.engine()
        self.assertEqual(delegation.ladder_route("refactor the parser", engine),
                         {"provider": "anthropic", "model": "claude-opus-5-5", "reasoning_effort": "medium"})
        self.assertEqual(decisions, [("refactor the parser", False, True, True)])   # not logged as a chat turn, Jev always rates it

    def test_ladder_off_empty_goal_or_a_failure_keeps_hermes_default(self):
        self.assertIsNone(delegation.ladder_route("x", self.engine(ladder=False)[0]))
        self.assertIsNone(delegation.ladder_route("   ", self.engine()[0]))
        broken = SimpleNamespace(settings=lambda: {"ladder": True}, decide=lambda *a, **k: 1 / 0)
        self.assertIsNone(delegation.ladder_route("x", broken))

    def test_backend_routes_an_unsaved_task_through_the_ladder(self):
        engine, _ = self.engine()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        engine.home = tmp.name
        fake = SimpleNamespace(DelegationProvider=object, ROUTE_KEYS=frozenset({"model", "provider", "reasoning_effort"}))
        with patch.dict("sys.modules", {"agent.delegation_provider": fake}):
            backend = delegation.backend(lambda: engine)
        route = backend.route_task({"goal": "fix the failing build"}, {"model": "gpt-6-sol", "provider": "openai-codex"})
        self.assertEqual(route, {"provider": "anthropic", "model": "claude-opus-5-5", "reasoning_effort": "medium"})
        latest = json.loads((Path(tmp.name) / "harp-delegation-latest.json").read_text(encoding="utf-8"))
        self.assertEqual((latest["job"], latest["source"]), ("Cost ladder", "ladder"))

if __name__ == "__main__":
    unittest.main()
