import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from dashboard import router_engine

CANDS = [
    {"provider": "anthropic", "model": "opus", "description": "hard analysis planning design", "cap": "high"},
    {"provider": "openai-codex", "model": "sol", "description": "code bug error crash build", "cap": "high"},
]
KEY = SimpleNamespace(id="k1", label="Tony", runtime_api_key="secret", runtime_base_url="https://api.typesafe.ai/v1", last_status=None)


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.clock = Clock()
        self.checks, self.asks = [], []
        self.check_result = {"ok": True, "status": 200, "ms": 300}

        def check(key, base_url):
            self.checks.append(key)
            return self.check_result

        def ask(key, state, questions, model, base_url):
            self.asks.append((key, state, sorted(questions), model))
            return {"route": {"choice": "openai-codex:sol", "confidence": 0.93,
                              "probabilities": {"openai-codex:sol": 0.95, "anthropic:opus": 0.05}},
                    "difficulty": {"score": 2.0}}, 210

        self.entries = [KEY]
        self.engine = router_engine.Engine(self.home, lambda: self.entries, check, ask, self.clock)
        self.engine.save({"candidates": CANDS})

    def tearDown(self):
        self.tmp.cleanup()

    def test_key_state_is_cached_for_five_minutes(self):
        self.assertEqual(self.engine.jev_status()["state"], "ok")
        self.engine.jev_status()
        self.assertEqual(len(self.checks), 1)
        self.clock.now += 301
        self.check_result = {"ok": False, "status": 401}
        self.assertEqual(self.engine.jev_status()["state"], "bad")
        self.entries = []
        self.assertEqual(self.engine.jev_status(), {"state": "none"})

    def test_unsure_local_asks_jev_and_jev_teaches_the_learner(self):
        d = self.engine.decide("my build crashes", "s1", "desktop", "opus")
        self.assertEqual((d["by"], d["pick"], d["jev_ms"]), ("jev", "openai-codex:sol", 210))
        self.assertEqual(self.asks[0][0], "secret")
        self.assertEqual(self.asks[0][3], "jev-latest")
        self.assertEqual(self.engine.store.jev_calls_today(), 1)
        self.assertEqual(self.engine.status()["samples"], 3)   # 2 seeds + Jev's label

    def test_no_key_or_cap_reached_stays_local(self):
        self.entries = []
        self.assertEqual(self.engine.decide("my build crashes")["by"], "local")
        self.entries = [KEY]
        self.engine.save({"jev_daily_cap": 0})
        self.assertEqual(self.engine.decide("my build crashes")["by"], "local")
        self.assertEqual(self.asks, [])

    def test_jev_error_falls_back_and_marks_down(self):
        def broken(*args, **kwargs):
            raise OSError("timeout")

        self.engine._ask = broken
        d = self.engine.decide("my build crashes")
        self.assertEqual(d["by"], "local")
        self.assertEqual(self.engine.jev_status()["state"], "down")

    def test_disabled_or_empty_decides_nothing(self):
        self.assertIsNone(self.engine.decide("   "))
        self.engine.save({"enabled": False})
        self.assertIsNone(self.engine.decide("hello"))

    def test_load_uses_plans_and_expires(self):
        self.engine.save({"plans": {"openai-codex": {"c1": "x1"}}})
        self.engine.record_load("openai-codex", {"c1": 92})
        self.assertEqual(self.engine.loads()["openai-codex"]["used"], 92)
        d = self.engine.decide("my build crashes")
        self.assertEqual((d["pick"], d["moved_from"]), ("anthropic:opus", "openai-codex:sol"))
        self.clock.now += 3601
        self.assertEqual(self.engine.loads(), {})
