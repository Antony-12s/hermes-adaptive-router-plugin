import unittest
from types import SimpleNamespace
from unittest.mock import patch

import __init__ as harp


class FakeEngine:
    def __init__(self, apply, pick="anthropic:haiku"):
        self.apply, self.pick, self.calls = apply, pick, []

    def settings(self):
        return {"enabled": True, "apply": self.apply, "candidates": [{"provider": "anthropic", "model": "haiku"}]}

    def decide(self, text, session, platform, current):
        self.calls.append((text, session, platform, current))
        return {"pick": self.pick, "effort": "low", "by": "local"}


class HookTests(unittest.TestCase):
    def tearDown(self):
        harp._TURN_ROUTES.clear()

    def run_turn(self, engine, provider="anthropic", depth=0):
        agent = SimpleNamespace(provider=provider, _delegate_depth=depth)
        with patch.object(harp, "_engine", return_value=engine), patch.object(harp, "_find_agent", return_value=agent), \
                patch("threading.Thread") as thread:
            self.assertIsNone(harp.route_turn(session_id="s1", user_message=[{"type": "text", "text": "hi"}],
                                              platform="desktop", model="opus"))
        return thread

    def test_shadow_mode_decides_in_background_and_never_routes(self):
        engine = FakeEngine(apply=False)
        thread = self.run_turn(engine)
        target, args = thread.call_args.kwargs["target"], thread.call_args.kwargs["args"]
        target(*args)
        self.assertEqual(engine.calls, [("hi", "s1", "desktop", "opus")])
        self.assertEqual(harp._TURN_ROUTES, {})

    def test_apply_mode_routes_same_provider_only(self):
        self.run_turn(FakeEngine(apply=True))
        self.assertEqual(harp._TURN_ROUTES["s1"]["pick"], "anthropic:haiku")
        request = {"model": "gpt", "reasoning": {}}
        out = harp.apply_turn_route(request=request, session_id="s1", provider="openai-codex", api_mode="codex_responses")
        self.assertIsNone(out)   # session is on another provider now: change nothing
        harp._TURN_ROUTES.clear()
        self.run_turn(FakeEngine(apply=True, pick="openai-codex:sol"))
        self.assertEqual(harp._TURN_ROUTES, {})

    def test_apply_rewrites_model_and_effort(self):
        harp._TURN_ROUTES["s1"] = {"pick": "openai-codex:mini", "effort": "low", "by": "jev"}
        out = harp.apply_turn_route(request={"model": "sol", "reasoning": {"summary": "auto"}}, session_id="s1",
                                    provider="openai-codex", api_mode="codex_responses")
        self.assertEqual(out["request"], {"model": "mini", "reasoning": {"summary": "auto", "effort": "low"}})
        self.assertEqual(out["source"], "harp-router")

    def test_subagents_are_left_to_the_delegation_backend(self):
        # delegate_task children (model_map picks their model) are never rerouted or sent to Jev
        for apply in (False, True):
            engine = FakeEngine(apply=apply)
            thread = self.run_turn(engine, depth=1)
            thread.assert_not_called()
            self.assertEqual(engine.calls, [])
            self.assertEqual(harp._TURN_ROUTES, {})

    def test_failures_never_break_the_turn(self):
        with patch.object(harp, "_engine", side_effect=RuntimeError("boom")):
            self.assertIsNone(harp.route_turn(session_id="s1", user_message="hi"))
        harp._TURN_ROUTES["s1"] = {"pick": "anthropic:haiku", "effort": "low", "by": "jev"}
        with patch.object(harp, "_thinking", side_effect=ImportError("moved")):
            self.assertIsNone(harp.apply_turn_route(request={"model": "opus"}, session_id="s1",
                                                    provider="anthropic", api_mode="anthropic_messages"))
