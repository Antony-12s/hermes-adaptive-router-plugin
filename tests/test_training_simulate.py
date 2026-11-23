import unittest

from dashboard import ladder
from training import simulate, train


class SimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results, cls.summary, cls.info = simulate.run(train.load_rows(), temperature=400)

    def test_every_prompt_gets_a_route_tier_and_agent(self):
        self.assertEqual(len(self.results), 500)
        for r in self.results:
            self.assertIn(r["route"]["provider"], (ladder.ANTHROPIC, ladder.CODEX))
            self.assertIn(r["route"]["effort"], ladder.EFFORTS)
            self.assertIn(r["learner"]["agent"], simulate.GROUP_ROLE.values())
            self.assertLessEqual(r["learner"]["tier"], ladder.MODE_TOP_TIER["balanced"])

    def test_held_out_scores_are_well_above_chance(self):
        held = self.summary["splits"]["held-out"]
        self.assertGreaterEqual(held["near"][0], 70)       # within one tier
        self.assertGreaterEqual(held["tier"][0], 50)

    def test_everyday_and_light_work_is_never_split(self):
        for r in self.results:
            if r["learner"]["work"] in ("everyday", "light"):
                self.assertEqual(r["sub_agents"], [], r["id"])

    def test_sub_agents_are_capped_and_follow_quota_room(self):
        for r in self.results:
            self.assertLessEqual(len(r["sub_agents"]), simulate.MAX_SUB_AGENTS)
            if r["input"]["case"] in ("auto_both_hot", "prefer_both_hot"):
                self.assertEqual(r["sub_agents"], [], r["id"])

    def test_a_multi_part_coding_job_is_split(self):
        subs, _ = simulate.plan_sub_agents("Track down the bug, fix it and write a regression test.", "coding", 3, {}, 85)
        self.assertEqual([s[0] for s in subs], ["Investigator", "Fixer", "Tester"])
        self.assertEqual([s[2] for s in subs], [3, 3, 2])       # the tester needs a lighter model
        subs, _ = simulate.plan_sub_agents("Rename the variable.", "coding", 3, {}, 85)
        self.assertEqual(subs, [])


if __name__ == "__main__":
    unittest.main()
