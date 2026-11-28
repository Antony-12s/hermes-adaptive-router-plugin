import unittest
from types import SimpleNamespace
from unittest.mock import patch

import __init__ as harp

route = harp._sibling("provider_route")


class FakeEngine:
    def __init__(self, enabled=True, apply=False, pick="anthropic:claude-opus-5-5", effort="medium"):
        self.enabled, self.apply, self.pick, self.effort, self.calls = enabled, apply, pick, effort, []

    def settings(self):
        return {"enabled": self.enabled, "apply": self.apply, "ladder": True, "candidates": []}

    def decide(self, text, session="", platform="", current="", mode=None, **_kw):
        self.calls.append({"text": text, "session": session, "current": current, "mode": mode})
        return {"pick": self.pick, "effort": self.effort, "by": "jev", "tier": 3}


class ProviderRouteTests(unittest.TestCase):
    def test_each_harp_model_is_a_routing_mode(self):
        self.assertEqual([route.mode_for(m) for m in ("harp-save", "harp-balanced", "harp-best", "HARP-BEST")],
                         ["save", "balanced", "best", "best"])
        self.assertEqual(route.mode_for("something-else"), "balanced")
        self.assertEqual(route.MODELS, ("harp-save", "harp-balanced", "harp-best"))

    def test_a_bound_turn_takes_the_pick_and_its_effort(self):
        agent, bound = SimpleNamespace(reasoning_config={"effort": "xhigh"}), []
        ok, reason = route.bind_turn(agent, {"pick": "openai-codex:gpt-6-luna", "effort": "low"},
                                     lambda a, p, m: bound.append((p, m)) or True)
        self.assertEqual((ok, reason, bound), (True, "", [("openai-codex", "gpt-6-luna")]))
        self.assertEqual(agent.reasoning_config, {"enabled": True, "effort": "low"})

    def test_a_failed_bind_leaves_the_turn_and_its_effort_alone(self):
        for binder, expected in ((lambda a, p, m: False, "provider not configured"),
                                 (lambda a, p, m: (_ for _ in ()).throw(RuntimeError("no client")), "RuntimeError: no client")):
            agent = SimpleNamespace(reasoning_config={"effort": "xhigh"})
            ok, reason = route.bind_turn(agent, {"pick": "anthropic:claude-opus-5-5", "effort": "low"}, binder)
            self.assertEqual((ok, reason), (False, expected))
            self.assertEqual(agent.reasoning_config, {"effort": "xhigh"})
        self.assertEqual(route.bind_turn(SimpleNamespace(), {"pick": "nonsense", "effort": "low"}, lambda *a: True),
                         (False, "bad pick nonsense"))


class HarpProviderTurnTests(unittest.TestCase):
    def run_turn(self, engine, provider="harp", model="harp-best", depth=0):
        agent = SimpleNamespace(provider=provider, model=model, _delegate_depth=depth, reasoning_config=None)
        bound = []
        with patch.object(harp, "_engine", return_value=engine), patch.object(harp, "_find_agent", return_value=agent), \
                patch.object(route, "hermes_binder", side_effect=lambda a, p, m: bound.append((p, m)) or True), \
                patch.object(harp, "_publish_quota") as publish, patch("threading.Thread") as thread:
            self.assertIsNone(harp.route_turn(session_id="s1", user_message="fix the login bug", platform="desktop",
                                              model=model))
        return agent, bound, publish, thread

    def tearDown(self):
        harp._TURN_ROUTES.clear()

    def test_a_harp_session_binds_the_pick_for_the_turn_in_the_chosen_mode(self):
        engine = FakeEngine()
        agent, bound, publish, thread = self.run_turn(engine)
        self.assertEqual(engine.calls, [{"text": "fix the login bug", "session": "s1", "current": "harp-best", "mode": "best"}])
        self.assertEqual(bound, [("anthropic", "claude-opus-5-5")])
        self.assertEqual(agent.reasoning_config, {"enabled": True, "effort": "medium"})
        publish.assert_not_called()          # no quota notice: it would auto-switch the session off HARP
        thread.assert_not_called()
        self.assertEqual(harp._TURN_ROUTES, {})

    def test_a_harp_session_is_routed_even_when_the_router_is_off_or_in_shadow(self):
        for engine in (FakeEngine(enabled=False), FakeEngine(apply=False), FakeEngine(apply=True)):
            _agent, bound, _publish, _thread = self.run_turn(engine, model="harp-save")
            self.assertEqual(bound, [("anthropic", "claude-opus-5-5")])
            self.assertEqual(engine.calls[0]["mode"], "save")

    def test_subagents_on_harp_are_left_to_the_delegation_backend(self):
        engine = FakeEngine()
        _agent, bound, _publish, _thread = self.run_turn(engine, depth=1)
        self.assertEqual((engine.calls, bound), ([], []))

    def test_a_routing_failure_never_breaks_the_turn(self):
        engine = FakeEngine()
        engine.decide = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("jev down"))
        agent, bound, _publish, _thread = self.run_turn(engine)
        self.assertEqual((bound, agent.provider), ([], "harp"))


if __name__ == "__main__":
    unittest.main()
