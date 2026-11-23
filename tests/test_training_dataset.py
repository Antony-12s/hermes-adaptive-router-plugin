import unittest
from collections import Counter

from dashboard import ladder
from training import train
from training.generate_dataset import CASES, GROUPS


class RoutingDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = train.load_rows()

    def test_size_split_and_unique_ids_and_prompts(self):
        self.assertEqual(len(self.rows), 500)
        self.assertEqual(Counter(r["split"] for r in self.rows), {"train": 400, "evaluation": 100})
        self.assertEqual(len({r["id"] for r in self.rows}), 500)
        self.assertEqual(len({r["prompt"].casefold() for r in self.rows}), 500)

    def test_every_group_has_25_prompts_and_both_languages(self):
        counts = Counter(r["task_group"] for r in self.rows)
        self.assertEqual(set(counts), {g[0] for g in GROUPS})
        self.assertTrue(all(n == 25 for n in counts.values()), counts)
        self.assertEqual(Counter(r["language"] for r in self.rows), {"th": 250, "en": 250})
        for group, *_ in GROUPS:
            self.assertEqual(sum(r["split"] == "evaluation" and r["task_group"] == group for r in self.rows), 5, group)

    def test_evaluation_prompts_never_share_a_template_with_training(self):
        trained = {r["template"] for r in self.rows if r["split"] == "train"}
        for r in self.rows:
            if r["split"] == "evaluation" and r["template"] != "seed":
                self.assertNotIn(r["template"], trained, r["id"])

    def test_tiers_cover_the_easy_to_hard_range_and_use_four_effort_levels(self):
        self.assertTrue({0, 1, 2, 3, 4} <= {r["tier"] for r in self.rows})
        efforts = {r["scenario"]["expected_route"]["effort"] for r in self.rows}
        self.assertTrue({"low", "medium", "high", "xhigh"} <= efforts, efforts)
        for r in self.rows:
            route = r["scenario"]["expected_route"]
            self.assertEqual((route["model"], route["effort"]), ladder.RUNGS[route["provider"]][r["tier"]])

    def test_every_case_is_covered_for_every_work_type(self):
        for cls in ("coding", "analysis", "everyday", "light"):
            cases = {r["scenario"]["case"] for r in self.rows if r["route_class"] == cls}
            self.assertEqual(cases, set(CASES), cls)

    def test_ladder_provider_policy_matches_the_dataset_with_perfect_tiers(self):
        for row in self.rows:
            tier, want = train.target(row, "balanced")
            scenario = row["scenario"]
            got = ladder.decide(row["prompt"], None, "balanced", scenario["heavyweight"], train.build_loads(scenario),
                                scenario["limit"], tier=tier)
            self.assertEqual({k: got[k] for k in ("provider", "model", "effort")}, want, row["id"])

    def test_easy_prompts_use_cheap_models(self):
        for row in self.rows:
            if row["tier"] <= 1:
                self.assertIn(row["scenario"]["expected_route"]["model"], ("claude-haiku-5-5", "gpt-6-luna"))


if __name__ == "__main__":
    unittest.main()
