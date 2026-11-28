import tempfile
import unittest
from pathlib import Path

from dashboard import ladder, router_engine
from training import seed, train


class SeedTests(unittest.TestCase):
    def test_seeding_teaches_a_fresh_profile_and_is_safe_to_repeat(self):
        rows = train.load_rows()
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(seed.seed(home, rows, dry_run=True), 400)
            self.assertFalse((Path(home) / "harp-router.sqlite3").exists())
            self.assertEqual(seed.seed(home, rows), 400)
            self.assertEqual(seed.seed(home, rows), 0)
            engine = router_engine.Engine(home)
            easy = engine.decide("hi there", log=False, use_jev=False)
            hard = engine.decide("design a multi-tenant architecture with tradeoffs", log=False, use_jev=False)
            self.assertEqual(easy["tier"], 0)
            self.assertGreater(hard["tier"], easy["tier"])    # without Jev the local pick is capped at FALLBACK_TOP_TIER
            self.assertEqual(engine.status()["stats"]["corrections_total"], 0)    # seeds are not user corrections

    def test_held_out_prompts_are_never_seeded(self):
        rows = train.load_rows()
        with tempfile.TemporaryDirectory() as home:
            seed.seed(home, rows)
            texts = {text for text, *_ in router_engine.Engine(home).store.tier_samples(ladder.LABELS)}
            held_out = {r["prompt"] for r in rows if r["split"] == "evaluation"}
            self.assertFalse(texts & held_out)


if __name__ == "__main__":
    unittest.main()
