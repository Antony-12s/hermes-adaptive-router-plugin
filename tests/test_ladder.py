import unittest

from dashboard import ladder, router


def load(peaks, plans=None):
    peaks = {f"a{i}": p for i, p in enumerate(peaks)}
    plans = {f"a{i}": w for i, w in enumerate(plans or [])}
    value = router.provider_load(peaks, plans)
    value["pool_near_limit"] = min(peaks.values()) >= 85
    value["pool_has_room"] = min(peaks.values()) < 85
    return value


class LadderTests(unittest.TestCase):
    def test_each_provider_has_a_rung_for_every_tier_and_cost_never_falls(self):
        for provider, rungs in ladder.RUNGS.items():
            self.assertEqual(len(rungs), len(ladder.TIERS))
            costs = [ladder.cost(*rung) for rung in rungs]
            self.assertEqual(costs, sorted(costs), provider)
            self.assertTrue(all(effort in ladder.EFFORTS for _, effort in rungs))

    def test_cheap_models_think_harder_before_work_moves_to_a_pricier_model(self):
        haiku_xhigh = ladder.cost("claude-haiku-5-5", "xhigh")
        self.assertLess(haiku_xhigh, ladder.cost("claude-sonnet-5-5", "low"))
        self.assertLess(haiku_xhigh, ladder.cost("claude-opus-5-5", "low"))

    def test_from_the_routine_tier_the_provider_with_more_weighted_quota_runs_the_job(self):
        loads = {ladder.ANTHROPIC: load([30], ["x5"]), ladder.CODEX: load([30], ["x1"])}
        self.assertEqual(ladder.pick_provider(3, "", loads, 85), ladder.ANTHROPIC)
        loads = {ladder.ANTHROPIC: load([30], ["x1"]), ladder.CODEX: load([30], ["x5"])}
        self.assertEqual(ladder.pick_provider(3, "", loads, 85), ladder.CODEX)
        self.assertEqual(ladder.pick_provider(1, "", loads, 85), ladder.ANTHROPIC)    # easy work stays put

    def test_preference_applies_to_demanding_tiers_only_while_it_has_room(self):
        loads = {ladder.ANTHROPIC: load([10]), ladder.CODEX: load([80])}
        self.assertEqual(ladder.pick_provider(3, ladder.CODEX, loads, 85), ladder.CODEX)
        self.assertEqual(ladder.pick_provider(1, ladder.CODEX, loads, 85), ladder.ANTHROPIC)
        loads[ladder.CODEX] = load([95])
        self.assertEqual(ladder.pick_provider(3, ladder.CODEX, loads, 85), ladder.ANTHROPIC)

    def test_provider_without_room_is_skipped_and_both_full_keeps_the_default(self):
        full = load([95])
        self.assertEqual(ladder.pick_provider(0, "", {ladder.ANTHROPIC: full, ladder.CODEX: load([10])}, 85),
                         ladder.CODEX)
        self.assertEqual(ladder.pick_provider(0, "", {ladder.ANTHROPIC: full, ladder.CODEX: full}, 85),
                         ladder.ANTHROPIC)
        self.assertEqual(ladder.pick_provider(3, "", {}, 85), ladder.ANTHROPIC)

    def test_corrections_map_to_tiers(self):
        self.assertEqual(ladder.tier_of_label("4"), 4)
        self.assertEqual(ladder.tier_of_label("anthropic:claude-haiku-5-5"), 0)
        self.assertEqual(ladder.tier_of_label("openai-codex:gpt-6.2-sol"), 3)
        self.assertEqual(ladder.tier_of_route("claude-opus-5-5", "xhigh"), 4)
        with self.assertRaises(ValueError):
            ladder.tier_of_label("anthropic:unknown-model")

    def test_jev_answers_are_read_as_tier_probabilities(self):
        answers = {"tier": {"choice": "3", "confidence": 0.8, "probabilities": {"2": 0.1, "3": 0.8, "9": 0.1}}}
        self.assertEqual(ladder.read_jev(answers), {"probs": {"2": 0.1, "3": 0.8}, "confidence": 0.8})
        self.assertIsNone(ladder.read_jev({"tier": {"choice": "x", "probabilities": {"3": 1}}}))
        self.assertEqual(ladder.heavyweight_provider("anthropic:claude-opus-5-5"), ladder.ANTHROPIC)
        self.assertEqual(ladder.heavyweight_provider(""), "")

    def test_a_mode_caps_the_tier(self):
        decision = ladder.decide("x", router.Learner(), "save", "", {}, 85, tier=5)
        self.assertEqual(decision["tier"], ladder.MODE_TOP_TIER["save"])
        self.assertEqual((decision["model"], decision["effort"]), ladder.RUNGS[ladder.ANTHROPIC][2])

    def test_unsure_leans_one_tier_up_but_never_past_the_mode_ceiling(self):
        probs = {"0": 0.34, "1": 0.33, "2": 0.33, "3": 0.0, "4": 0.0, "5": 0.0, "6": 0.0}
        tier, unsure, _ = ladder.tier_from_probs(probs, 0.1, "balanced")
        self.assertTrue(unsure)
        self.assertEqual(tier, ladder.UNSURE_TIER)       # almost no confidence: the neutral tier, not noise
        sure_enough = {"0": 0.0, "1": 0.0, "2": 0.9, "3": 0.1, "4": 0.0, "5": 0.0, "6": 0.0}
        self.assertEqual(ladder.tier_from_probs(sure_enough, 0.69, "balanced")[0], 3)    # leans up from 2.1
        top = {"6": 1.0, **{str(t): 0.0 for t in range(6)}}
        self.assertEqual(ladder.tier_from_probs(top, 0.99, "balanced")[0], ladder.MODE_TOP_TIER["balanced"])


if __name__ == "__main__":
    unittest.main()
