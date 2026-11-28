import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from dashboard import ladder, router, router_engine

KEY = SimpleNamespace(id="k1", label="Tony", runtime_api_key="secret", runtime_base_url="https://api.typesafe.ai/v1",
                      last_status=None)


def load(peaks, plans=None):
    peaks = {f"a{i}": p for i, p in enumerate(peaks)}
    plans = {f"a{i}": w for i, w in enumerate(plans or [])}
    value = router.provider_load(peaks, plans)
    value["pool_near_limit"] = min(peaks.values()) >= 85
    value["pool_has_room"] = min(peaks.values()) < 85
    return value


class LadderEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.asks = []

        def ask(key, state, questions, model, base_url):
            self.asks.append(sorted(questions))
            return {"tier": {"choice": "4", "confidence": 0.9,
                             "probabilities": {"3": 0.05, "4": 0.9, "5": 0.05}}}, 180

        self.engine = router_engine.Engine(Path(self.tmp.name), lambda: [KEY],
                                           lambda key, base_url: {"ok": True, "status": 200, "ms": 100}, ask)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_ladder_is_on_by_default_and_needs_no_candidate_list(self):
        settings = self.engine.settings()
        self.assertTrue(settings["ladder"])
        d = self.engine.decide("summarize this", log=False, use_jev=False)
        self.assertTrue(d["ladder"])
        provider, model = d["pick"].split(":", 1)
        self.assertIn((model, d["effort"]), ladder.RUNGS[provider])

    def test_the_tier_learner_has_its_own_temperature(self):
        before = router.Learner.TEMPERATURE
        self.assertEqual(self.engine.tier_learner().TEMPERATURE, ladder.LEARNER_TEMPERATURE)
        self.assertEqual(router.Learner.TEMPERATURE, before)

    def test_a_cold_learner_is_unsure_and_lands_on_a_middle_tier(self):
        for text in ("zzz qqq", "hi there", "design a multi-tenant architecture"):
            d = self.engine.decide(text, log=False, use_jev=False)
            self.assertTrue(d["unsure"])
            self.assertEqual(d["by"], "local-capped", text)
            self.assertEqual(d["tier"], min(ladder.UNSURE_TIER, router_engine.FALLBACK_TOP_TIER), text)

    def test_a_known_weather_lookup_goes_to_the_cheapest_tier(self):
        d = self.engine.decide("will it rain today?", log=False, use_jev=False)
        self.assertEqual((d["pick"], d["effort"], d["tier"]), ("anthropic:claude-haiku-5-5", "low", 0))

    def test_unsure_asks_jev_one_tier_question_and_jev_teaches_the_learner(self):
        d = self.engine.decide("rebuild the billing pipeline end to end", "s1", "desktop", "")
        self.assertEqual((d["by"], d["tier"], d["jev_ms"]), ("jev", 4, 180))
        self.assertEqual(self.asks, [["tier"]])
        again = self.engine.decide("rebuild the billing pipeline end to end", log=False, use_jev=False)
        self.assertGreater(again["probs"]["4"], again["probs"]["0"])

    def test_jev_always_asks_jev_even_when_the_learner_is_sure(self):
        from unittest.mock import patch

        with patch.dict(ladder.MODE_SURE, {"save": 0.0, "balanced": 0.0, "best": 0.0}):
            sure = self.engine.decide("rebuild the billing pipeline end to end", log=False)
            self.assertEqual((sure["by"], self.asks), ("local", []))
            asked = self.engine.decide("rebuild the billing pipeline end to end", log=False, jev_always=True)
        self.assertEqual((asked["by"], asked["tier"], self.asks), ("jev", 4, [["tier"]]))

    def test_a_mode_passed_to_decide_overrides_the_saved_mode_and_switch(self):
        # The HARP provider's model (harp-save/balanced/best) is the mode, and picking it is consent to route.
        self.engine.save({"mode": "save", "enabled": False})
        self.assertIsNone(self.engine.decide("rebuild the billing pipeline end to end", log=False))
        best = self.engine.decide("rebuild the billing pipeline end to end", log=False, jev_always=True, mode="best")
        self.assertEqual((best["by"], best["tier"]), ("jev", 4))
        save = self.engine.decide("rebuild the billing pipeline end to end", log=False, jev_always=True, mode="save")
        self.assertLessEqual(save["tier"], ladder.MODE_TOP_TIER["save"])

    def test_the_mode_sets_the_top_tier_and_heavyweight_sets_the_provider(self):
        self.engine.save({"mode": "save"})
        d = self.engine.decide("rebuild the billing pipeline end to end", log=False)
        self.assertLessEqual(d["tier"], ladder.MODE_TOP_TIER["save"])
        self.engine.save({"mode": "balanced", "heavyweight": "openai-codex:gpt-6.2-sol"})
        self.engine.record_load("anthropic", {"a": 10})
        self.engine.record_load("openai-codex", {"c": 20})
        d = self.engine.decide("rebuild the billing pipeline end to end", log=False)
        self.assertEqual(d["pick"].split(":")[0], "openai-codex")

    def test_quota_decides_from_the_routine_tier_and_reports_the_move(self):
        self.engine.save({"mode": "best"})
        self.engine.record_load("anthropic", {"a": 90})
        self.engine.record_load("openai-codex", {"c": 10})
        self.engine.save({"plans": {"anthropic": {"a": "x1"}, "openai-codex": {"c": "x1"}}})
        d = self.engine.decide("rebuild the billing pipeline end to end", log=False)
        self.assertEqual(d["pick"].split(":")[0], "openai-codex")
        self.assertTrue(d["moved_from"].startswith("anthropic:"))

    def test_corrections_and_verified_runs_teach_tiers_not_models(self):
        d = self.engine.decide("rename the helper function", "s1", "desktop", "", use_jev=False)
        learned = self.engine.feedback(d["id"], "anthropic:claude-haiku-5-5")
        self.assertEqual(learned, "anthropic:claude-haiku-5-5")
        labels = {row[1] for row in self.engine.store.tier_samples(ladder.LABELS)}
        self.assertEqual(labels, {"0"})
        second = self.engine.decide("rename the helper function again", "s1", "desktop", "", use_jev=False)
        self.engine.verify(second["id"])
        labels = {row[1] for row in self.engine.store.tier_samples(ladder.LABELS)}
        self.assertTrue(labels <= set(ladder.LABELS))
        with self.assertRaises(ValueError):
            self.engine.feedback(second["id"], "anthropic:not-in-the-ladder")

    def test_status_carries_the_ladder_for_the_desktop_page(self):
        status = self.engine.status()
        self.assertEqual(len(status["ladder"]["tiers"]), len(ladder.TIERS))
        self.assertEqual(status["ladder"]["efforts"], list(ladder.EFFORTS))
        self.engine.save({"ladder": False})
        self.assertIsNone(self.engine.status()["ladder"])

    def test_turning_the_ladder_off_without_candidates_disables_routing(self):
        self.engine.save({"ladder": False, "candidates": []})
        self.assertIsNone(self.engine.decide("anything", log=False))
        with self.assertRaises(ValueError):
            self.engine.save({"ladder": "yes"})


if __name__ == "__main__":
    unittest.main()
