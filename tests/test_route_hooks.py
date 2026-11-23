import unittest
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
        harp._LIVE_REQUESTS.clear()

    def test_live_log_pairs_text_with_the_actual_request_and_labels_auxiliary_calls(self):
        with tempfile.TemporaryDirectory() as home:
            from pathlib import Path

            store = harp._sibling("router").Store(Path(home) / "router.sqlite3")
            with patch.object(harp, "_engine", return_value=SimpleNamespace(store=store)):
                harp.remember_live_request(api_request_id="one", user_message="review\n the PR",
                                           request={"reasoning": {"effort": "medium"}},
                                           middleware_trace=[{"source": "harp-router", "reason": "jev picked sol (medium)"}])
                harp.remember_live_request(api_request_id="two", user_message="another request")
                harp.record_auxiliary_request(api_request_id="one", aux_task="next_prompt",
                                              provider="anthropic", model="haiku", usage={"total_tokens": 15})
                harp.track_account(api_request_id="one", provider="openai-codex", model="sol",
                                   response_model="sol-actual", api_duration=1.5, api_call_count=2)
                harp.track_account(api_request_id="unknown", provider="anthropic", model="opus")
            unknown, main, auxiliary = store.live()
            self.assertEqual(unknown["text"], "")
            self.assertEqual((main["text"], main["model"], main["source"], main["effort"], main["step"]),
                             ("review the PR", "sol-actual", "jev", "medium", 2))
            self.assertEqual((auxiliary["kind"], auxiliary["text"], auxiliary["tokens"]), ("aux:next_prompt", "", 15))

    def test_effort_is_read_from_the_body_hermes_passes_to_pre_api_request(self):
        wrap = lambda body: {"method": "POST", "body": body}
        cases = [
            (wrap({"thinking": {"type": "adaptive"}, "output_config": {"effort": "xhigh"}}), "xhigh"),   # Anthropic 4.6+
            (wrap({"thinking": {"type": "enabled", "budget_tokens": 16000}}), "16k budget"),            # older Claude
            (wrap({"thinking": {"type": "disabled"}}), "off"),
            (wrap({"reasoning": {"effort": "high", "summary": "auto"}}), "high"),                     # Codex Responses
            (wrap({"reasoning_effort": "low"}), "low"),                                                # chat completions
            (wrap({"extra_body": {"reasoning": {"effort": "medium"}}}), "medium"),                     # OpenRouter
            (wrap({"model": "claude-haiku-4-5"}), ""),                                                  # no thinking sent
            ({"reasoning": {"effort": "medium"}}, "medium"),                                           # bare body
            (None, ""),
        ]
        for request, want in cases:
            with self.subTest(request=request):
                self.assertEqual(harp.request_effort(request), want)

    def test_effort_falls_back_to_the_agent_reasoning_config_when_hermes_truncates_the_payload(self):
        self.assertEqual(harp.request_effort({"_truncated": True, "original_type": "dict", "preview": '{"method": "POST", "bo'}), "")
        self.assertEqual(harp.config_effort({"enabled": True, "effort": "high"}, "openai-codex", "gpt-6-sol", 4096), "high")
        self.assertEqual(harp.config_effort({"enabled": False}, "openai-codex", "gpt-6-sol", 4096), "off")
        self.assertEqual(harp.config_effort(None, "anthropic", "claude-opus-5-5", 4096), "")
        self.assertEqual(harp.config_effort({}, "openai-codex", "gpt-6-sol", 4096), "")

    def test_anthropic_effort_is_what_the_adapter_would_actually_send(self):
        adapter = SimpleNamespace(_thinking_kwargs=lambda cfg, model, tokens: (
            {} if "haiku" in model else {"thinking": {"type": "adaptive"}, "output_config": {"effort": "max" if cfg["effort"] == "xhigh" else cfg["effort"]}}))
        with patch.dict(sys.modules, {"agent": SimpleNamespace(), "agent.anthropic_adapter": adapter}):
            self.assertEqual(harp.config_effort({"effort": "xhigh"}, "anthropic", "claude-opus-4-6", 8000), "max")
            self.assertEqual(harp.config_effort({"effort": "high"}, "anthropic", "claude-haiku-5-5", 8000), "")

    def test_live_log_reads_effort_from_the_session_agent_when_the_request_body_is_gone(self):
        agent = SimpleNamespace(reasoning_config={"enabled": True, "effort": "high"})
        with patch.object(harp, "_find_agent", return_value=agent):
            harp.remember_live_request(api_request_id="big", user_message="hi", session_id="s1", provider="openai-codex",
                                       model="gpt-6-sol", max_tokens=4096, request={"_truncated": True, "preview": "{"})
        self.assertEqual(harp._LIVE_REQUESTS["big"][1], "high")

    def test_live_card_lists_four_calls(self):
        with tempfile.TemporaryDirectory() as home:
            from pathlib import Path

            store = harp._sibling("router").Store(Path(home) / "router.sqlite3")
            for index in range(6):
                store.log_live("main", f"call {index}", "anthropic", "haiku", "", 1.0, 10, index, "ok", "")
            self.assertEqual([call["text"] for call in store.live()], ["call 5", "call 4", "call 3", "call 2"])

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

    def test_ladder_quota_event_offers_the_same_tier_on_the_other_provider(self):
        broadcast = Mock()
        settings = {"apply": True, "ladder": True, "candidates": []}
        decision = {"pick": "anthropic:claude-opus-5-5", "effort": "xhigh", "tier": 4}
        anthropic = {"pool_near_limit": True, "pool_has_room": False, "used": 96}
        codex = {"pool_has_room": True, "used": 21}
        engine = SimpleNamespace(loads=lambda _: {"anthropic": anthropic, "openai-codex": codex})
        with patch.dict(sys.modules, {"hermes_cli.plugin_events": SimpleNamespace(broadcast_plugin_event=broadcast)}):
            harp._publish_quota(engine, settings, "chat", "anthropic", decision)
            notice = broadcast.call_args.args[2]["quota"]
            self.assertEqual(notice["pick"], {"provider": "openai-codex", "model": "gpt-6.2-sol", "effort": "xhigh"})
            codex["pool_has_room"] = False
            harp._publish_quota(engine, settings, "chat", "anthropic", decision)
            self.assertIsNone(broadcast.call_args.args[2]["quota"]["pick"])

    def test_quota_event_preserves_a_spare_account_and_caps_the_alternate_effort(self):
        broadcast = Mock()
        settings = {"apply": True, "candidates": [
            {"provider": "anthropic", "model": "opus", "cap": "high"},
            {"provider": "openai-codex", "model": "sol", "cap": "medium"},
        ]}
        decision = {"pick": "anthropic:opus", "effort": "high"}
        anthropic = {"pool_near_limit": False, "pool_has_room": True, "used": 92}
        codex = {"pool_has_room": True, "used": 21}
        engine = SimpleNamespace(loads=lambda _: {"anthropic": anthropic, "openai-codex": codex})
        with patch.dict(sys.modules, {"hermes_cli.plugin_events": SimpleNamespace(broadcast_plugin_event=broadcast)}):
            harp._publish_quota(engine, settings, "chat", "anthropic", decision)
            broadcast.assert_called_once_with("harp", "quota", {"session_id": "chat", "quota": None})
            anthropic["pool_near_limit"] = True
            harp._publish_quota(engine, settings, "chat", "anthropic", decision)
            notice = broadcast.call_args.args[2]["quota"]
            self.assertEqual(notice["pick"], {"provider": "openai-codex", "model": "sol", "effort": "medium"})
            codex["pool_has_room"] = False
            harp._publish_quota(engine, settings, "chat", "anthropic", decision)
            self.assertIsNone(broadcast.call_args.args[2]["quota"]["pick"])

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
