import tempfile
import unittest
from pathlib import Path

from dashboard import router

CANDS = [
    {"provider": "anthropic", "model": "opus", "description": "hard analysis planning design", "cap": "high"},
    {"provider": "openai-codex", "model": "sol", "description": "code bug error crash build", "cap": "high"},
    {"provider": "anthropic", "model": "haiku", "description": "hello thanks short chat translate", "cap": "low"},
]


def settings(**over):
    return {**router.DEFAULTS, "candidates": CANDS, **over}


def jev(pick, conf=0.95, level=2.0):
    probs = {router.candidate_id(c): 0.02 for c in CANDS}
    probs[pick] = 0.96
    return {"probs": probs, "pick": pick, "confidence": conf, "difficulty": level}


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.learner, self.levels = router.Learner(), router.Learner()

    def test_jev_pick_with_effort_from_difficulty_and_mode_ceiling(self):
        d = router.decide("x", settings(), self.learner, self.levels, {}, jev("openai-codex:sol", level=2.0))
        self.assertEqual((d["pick"], d["effort"], d["by"]), ("openai-codex:sol", "medium", "jev"))  # balanced caps at medium
        d = router.decide("x", settings(mode="best"), self.learner, self.levels, {}, jev("openai-codex:sol", level=2.0))
        self.assertEqual(d["effort"], "high")

    def test_candidate_cap_limits_effort(self):
        d = router.decide("x", settings(mode="best"), self.learner, self.levels, {}, jev("anthropic:haiku", level=2.0))
        self.assertEqual(d["effort"], "low")

    def test_unsure_goes_to_the_strongest_candidate(self):
        d = router.decide("x", settings(), self.learner, self.levels, {}, jev("anthropic:haiku", conf=0.3))
        self.assertEqual((d["pick"], d["unsure"]), ("anthropic:opus", True))

    def test_hot_provider_moves_work_to_the_next_best_cool_one(self):
        loads = {"openai-codex": {"used": 90}, "anthropic": {"used": 40}}
        d = router.decide("x", settings(), self.learner, self.levels, loads, jev("openai-codex:sol"))
        self.assertEqual((d["pick"], d["moved_from"]), ("anthropic:opus", "openai-codex:sol"))

    def test_everything_hot_keeps_the_pick(self):
        loads = {"openai-codex": {"used": 99}, "anthropic": {"used": 99}}
        d = router.decide("x", settings(), self.learner, self.levels, loads, jev("openai-codex:sol"))
        self.assertEqual((d["pick"], d["moved_from"]), ("openai-codex:sol", None))

    def test_local_learner_learns_from_labels(self):
        ids = [router.candidate_id(c) for c in CANDS]
        for _ in range(40):
            self.learner.learn("ทำไมแอปเด้ง build พัง", "openai-codex:sol")
            self.learner.learn("สวัสดีครับ ขอบคุณ", "anthropic:haiku")
        probs, conf = self.learner.predict("แอปเด้งอีกแล้ว", ids)
        self.assertEqual(max(probs, key=probs.get), "openai-codex:sol")
        self.assertGreater(conf, 0.3)
        d = router.decide("แอปเด้งอีกแล้ว", settings(mode="save"), self.learner, self.levels, {})
        self.assertEqual(d["by"], "local")

    def test_fresh_learner_is_unsure(self):
        _, conf = self.learner.predict("anything", ["a", "b"])
        self.assertEqual(conf, 0.0)

    def test_no_candidates_means_no_decision(self):
        self.assertIsNone(router.decide("x", settings(candidates=[]), self.learner, self.levels, {}))


class LoadAndSettingsTests(unittest.TestCase):
    def test_plan_weight(self):
        load = router.provider_load({"a": 62, "b": 91}, {"a": "x5", "b": "x1"})
        self.assertEqual(load, {"used": 67, "left": 1.99, "capacity": 6})
        self.assertIsNone(router.provider_load({}, {}))

    def test_settings_round_trip_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.json"
            self.assertEqual(router.load_settings(path)["mode"], "balanced")
            router.save_settings(path, {"mode": "best", "candidates": CANDS, "plans": {"anthropic": {"a": "x5"}}})
            self.assertEqual(router.load_settings(path)["plans"], {"anthropic": {"a": "x5"}})
            for bad in ({"mode": "fast"}, {"limit": 10}, {"candidates": [CANDS[0], CANDS[0]]},
                        {"plans": {"anthropic": {"a": "x3"}}}, {"candidates": [{"provider": "anthropic"}]}):
                with self.assertRaises(ValueError):
                    router.save_settings(path, bad)


class StoreTests(unittest.TestCase):
    def test_jev_decisions_and_feedback_train_the_learner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = router.Store(Path(tmp) / "r.sqlite3")
            d = router.decide("fix crash", settings(), router.Learner(), router.Learner(), {}, jev("openai-codex:sol"))
            first = store.log(d, "fix crash", "s1", "desktop", "opus")
            local = {**d, "by": "local"}
            second = store.log(local, "hello there", "s1", "desktop", "opus")
            self.assertEqual(store.feedback(second, "anthropic:haiku"), "anthropic:haiku")
            store.feedback(first, "")
            learner, levels = store.learner(CANDS)
            self.assertEqual(learner.docs["openai-codex:sol"], 1 + 1 + 3)   # seed + jev label + user ok
            self.assertEqual(learner.docs["anthropic:haiku"], 1 + 3)
            stats = store.stats()
            self.assertEqual((stats["decisions"], stats["jev"], stats["local"], stats["rated"], stats["right"]), (2, 1, 1, 2, 1))
            self.assertEqual(stats["corrections"], [{"from": "openai-codex:sol", "to": "anthropic:haiku", "times": 1}])
            self.assertEqual(store.examples("anthropic:haiku"), ["hello there"])
            self.assertEqual([r["id"] for r in store.recent()], [second, first])
            store.count_jev_call(); store.count_jev_call()
            self.assertEqual(store.jev_calls_today(), 2)
            with self.assertRaises(KeyError):
                store.feedback(999, "")

    def test_jev_questions_carry_user_examples(self):
        q = router.jev_questions(CANDS, lambda label: ["fix my build"] if label == "openai-codex:sol" else [])
        self.assertEqual(q["route"]["criteria"]["openai-codex:sol"], {"what": CANDS[1]["description"], "examples": ["fix my build"]})
        self.assertEqual(q["route"]["criteria"]["anthropic:haiku"], CANDS[2]["description"])
        self.assertEqual(q["difficulty"]["type"], "score")
        parsed = router.read_jev({"route": {"choice": "openai-codex:sol", "confidence": 0.9,
                                            "probabilities": {"openai-codex:sol": 0.9, "anthropic:opus": 0.1, "zzz": 0}},
                                  "difficulty": {"score": 1.4}}, CANDS)
        self.assertEqual((parsed["pick"], parsed["difficulty"], "zzz" in parsed["probs"]), ("openai-codex:sol", 1.4, False))
        self.assertIsNone(router.read_jev({"route": {"choice": "nope"}}, CANDS))


class ApplyTests(unittest.TestCase):
    def test_anthropic_uses_hermes_thinking_mapping_for_the_new_model(self):
        request = {"model": "opus", "max_tokens": 8000, "thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}, "messages": []}
        seen = []
        out = router.apply_route(request, "anthropic_messages", "haiku", "low",
                                 lambda m, e, t: seen.append((m, e, t)) or {})
        self.assertEqual(seen, [("haiku", "low", 8000)])
        self.assertEqual(out, {"model": "haiku", "max_tokens": 8000, "messages": []})
        self.assertEqual(request["model"], "opus")   # original untouched
        self.assertIsNone(router.apply_route(request, "anthropic_messages", "haiku", "low"))

    def test_codex_and_chat_effort(self):
        out = router.apply_route({"model": "a", "reasoning": {"summary": "auto"}}, "codex_responses", "b", "high")
        self.assertEqual(out["reasoning"], {"summary": "auto", "effort": "high"})
        self.assertEqual(router.apply_route({"model": "a"}, "chat_completions", "b", "low"), {"model": "b"})
        self.assertEqual(router.apply_route({"model": "a", "reasoning_effort": "high"}, "chat_completions", "b", "low")["reasoning_effort"], "low")
        self.assertIsNone(router.apply_route({"model": "a"}, "bedrock", "b", "low"))
        self.assertIsNone(router.apply_route({}, "codex_responses", "b", "low"))

    def test_message_text(self):
        self.assertEqual(router.message_text("hi"), "hi")
        self.assertEqual(router.message_text([{"type": "text", "text": "a"}, {"type": "image"}, {"text": "b"}]), "a b")
        self.assertEqual(router.message_text(None), "")
