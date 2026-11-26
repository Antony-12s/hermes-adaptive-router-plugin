import unittest
from unittest.mock import Mock

import __init__ as harp

FABLE_BODY = {"type": "error", "error": {
    "type": "rate_limit_error", "message": "Usage credits are required for this model.",
    "details": {"error_code": "credits_required", "disabled_reason": "out_of_credits",
                "model": "claude-fable-5-2", "model_display_name": "Fable"}}}


class CreditWallTests(unittest.TestCase):
    def classify(self, **overrides):
        kwargs = {"provider": "anthropic", "model": "claude-fable-5-2", "status_code": 429,
                  "error_type": "RateLimitError", "error_code": "",
                  "error_message": "Usage credits are required for this model.", "error_body": FABLE_BODY}
        kwargs.update(overrides)
        return harp.classify_credit_wall(**kwargs)

    def test_a_credit_wall_stops_without_retry_or_account_switch_and_asks_for_a_model(self):
        verdict = self.classify()
        self.assertEqual(verdict["reason"], "model_entitlement")
        self.assertIs(verdict["retryable"], False)
        self.assertIs(verdict["should_rotate_credential"], False)
        self.assertIs(verdict["should_fallback"], False)
        self.assertIn("Fable", verdict["message"])
        self.assertIn("/model", verdict["message"])
        self.assertIn("?", verdict["message"])

    def test_the_wall_is_found_from_the_error_code_alone(self):
        self.assertIsNotNone(self.classify(error_body={}, error_message="", error_code="credits_required"))

    def test_an_ordinary_rate_limit_is_left_to_hermes(self):
        body = {"type": "error", "error": {"type": "rate_limit_error",
                                           "message": "This request would exceed your account's rate limit."}}
        self.assertIsNone(self.classify(error_body=body, error_message=body["error"]["message"]))

    def test_other_providers_are_left_alone(self):
        self.assertIsNone(self.classify(provider="openai-codex"))

    def test_odd_payloads_never_raise(self):
        self.assertIsNone(harp.classify_credit_wall(provider="anthropic", error_body={"error": "text"}))
        self.assertIsNone(harp.classify_credit_wall())

    def test_the_hook_is_registered_and_declared(self):
        ctx = Mock(spec=["register_hook"])
        harp.register(ctx)
        events = [call.args[0] for call in ctx.register_hook.call_args_list]
        self.assertIn("transform_api_error_classification", events)
        with open("plugin.yaml", encoding="utf-8") as manifest:
            self.assertIn("transform_api_error_classification", manifest.read())


if __name__ == "__main__":
    unittest.main()
