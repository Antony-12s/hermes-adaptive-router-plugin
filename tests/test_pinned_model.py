import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dashboard import delegation

CONFIG = {"backend": "harp", "backends": {"model_map": {"options": [
    {"name": "quick", "provider": "anthropic", "model": "claude-haiku-5-5", "reasoning_effort": "low"},
    {"name": "hard", "provider": "anthropic", "model": "claude-opus-5-5", "reasoning_effort": "high"},
    {"name": "broken", "provider": "anthropic"}]}}}


class FakeRouteError(Exception):
    pass


def backend(home):
    fake = SimpleNamespace(DelegationProvider=object, ROUTE_KEYS=frozenset({"model", "provider", "reasoning_effort"}),
                           DelegationRouteError=FakeRouteError)
    calls = []
    engine = SimpleNamespace(home=home, settings=lambda: {"ladder": True},
                             decide=lambda goal, **k: calls.append(goal) or {"pick": "anthropic:claude-opus-5-5", "effort": "medium"},
                             loads=lambda s: {})
    with patch.dict("sys.modules", {"agent.delegation_provider": fake}):
        return delegation.backend(lambda: engine), calls, fake


class PinnedModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_pin_model_bypasses_the_ladder_for_that_task(self):
        harp, calls, fake = backend(self.tmp.name)
        with patch.dict("sys.modules", {"agent.delegation_provider": fake}):
            route = harp.route_task({"goal": "plan it", "pin_model": "anthropic:claude-fable-5-2"}, CONFIG)
        self.assertEqual(route, {"provider": "anthropic", "model": "claude-fable-5-2", "reasoning_effort": "high"})
        self.assertEqual(calls, [], "the ladder was not asked")
        latest = json.loads((Path(self.tmp.name) / "harp-delegation-latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["source"], "pinned")

    def test_pin_effort_and_named_options(self):
        harp, calls, fake = backend(self.tmp.name)
        with patch.dict("sys.modules", {"agent.delegation_provider": fake}):
            self.assertEqual(harp.route_task({"goal": "x", "pin_model": "openai-codex:gpt-6-sol", "pin_effort": "xhigh"}, CONFIG)["reasoning_effort"], "xhigh")
            self.assertEqual(harp.route_task({"goal": "x", "option": "hard"}, CONFIG),
                             {"provider": "anthropic", "model": "claude-opus-5-5", "reasoning_effort": "high"})
            self.assertEqual(harp.route_task({"goal": "x", "option": "quick", "pin_effort": "medium"}, CONFIG)["reasoning_effort"], "medium")
        self.assertEqual(calls, [])

    def test_no_pin_still_uses_the_ladder(self):
        harp, calls, fake = backend(self.tmp.name)
        with patch.dict("sys.modules", {"agent.delegation_provider": fake}):
            harp.route_task({"goal": "fix the build"}, CONFIG)
        self.assertEqual(calls, ["fix the build"])

    def test_a_malformed_pin_refuses_the_task(self):
        harp, _, fake = backend(self.tmp.name)
        with patch.dict("sys.modules", {"agent.delegation_provider": fake}):
            for bad in ("claude-fable-5-2", "anthropic:", "Anthropic Fable", ":x"):
                with self.assertRaises(FakeRouteError):
                    harp.route_task({"goal": "x", "pin_model": bad}, CONFIG)

    def test_task_parameters_offer_pin_and_the_model_map_names_without_reserved_names(self):
        harp, _, _ = backend(self.tmp.name)
        params = harp.task_parameters(CONFIG)
        self.assertEqual(set(params), {"pin_model", "pin_effort", "option"})
        self.assertEqual(params["option"]["enum"], ["hard", "quick"])           # the broken row is skipped
        self.assertFalse(set(params) & {"model", "provider", "goal"})
        self.assertEqual(params, harp.task_parameters(CONFIG), "deterministic for the prompt cache")
        self.assertNotIn("option", harp.task_parameters({"backend": "harp"}))


if __name__ == "__main__":
    unittest.main()
