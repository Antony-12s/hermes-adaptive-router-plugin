import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import __init__ as harp


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(
            provider="openrouter", primary_model="main", fast_model="fast",
            max_input_chars=10,
        )
        self.request = {
            "model": "main",
            "messages": [{"role": "user", "content": "สวัสดี"}],
            "stream": True,
        }
        self.payload = dict(
            request=self.request, provider="openrouter", model="main",
            api_mode="chat_completions",
        )

    def test_registers_and_routes_without_mutating_request(self):
        ctx = Mock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "harp.json"
            path.write_text(json.dumps(self.config), encoding="utf-8")
            with patch.dict(os.environ, HARP_CONFIG=str(path)):
                harp.register(ctx)
        ctx.register_middleware.assert_called_once()
        kind, callback = ctx.register_middleware.call_args.args
        self.assertEqual(kind, "llm_request")
        result = callback(**self.payload, middleware_schema_version="hermes.middleware.v1")
        self.assertEqual(result["request"], {**self.request, "model": "fast"})
        self.assertEqual(self.request["model"], "main")

    def test_no_config_leaves_middleware_disabled(self):
        ctx = Mock()
        with patch.dict(os.environ, HARP_CONFIG=""):
            harp.register(ctx)
        ctx.register_middleware.assert_not_called()

    def test_other_provider_model_or_api_mode_passes_through(self):
        policy = harp.RoutingPolicy(**self.config)
        for override in (
            {"provider": "backup"},
            {"model": "backup", "request": {**self.request, "model": "backup"}},
            {"request": {**self.request, "model": "previous-override"}},
            {"api_mode": "anthropic_messages"},
        ):
            with self.subTest(override=override):
                self.assertIsNone(harp.route_request(policy, **{**self.payload, **override}))

    def test_unsafe_or_oversized_requests_pass_through(self):
        policy = harp.RoutingPolicy(**self.config)
        for override in (
            {"tools": [{"type": "function"}]},
            {"response_format": {"type": "json_object"}},
            {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]},
            {"messages": [{"role": "tool", "content": "done"}]},
            {"messages": [
                {"role": "system", "content": "12345"},
                *self.request["messages"],
            ]},
        ):
            with self.subTest(override=override):
                request = {**self.request, **override}
                self.assertIsNone(harp.route_request(policy, **{**self.payload, "request": request}))

    def test_explicit_tool_opt_in(self):
        policy = harp.RoutingPolicy(**self.config, allow_tools=True)
        self.request["tools"] = [{"type": "function"}]
        result = harp.route_request(policy, **self.payload)
        self.assertEqual(result["request"]["tools"], self.request["tools"])
        self.assertEqual(result["request"]["model"], "fast")

    def test_invalid_config_fails_before_registration(self):
        for override in (
            {"provider": ""},
            {"fast_model": "main"},
            {"max_input_chars": True},
            {"allow_tools": "false"},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                harp.RoutingPolicy(**{**self.config, **override})
