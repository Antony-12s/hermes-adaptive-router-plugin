import io
import json
import unittest
import urllib.error

from dashboard import anthropic_plan


def profile(organization_type=None, rate_limit_tier=None, has_max=None, has_pro=None):
    return {"account": {"has_claude_max": has_max, "has_claude_pro": has_pro},
            "organization": {"organization_type": organization_type, "rate_limit_tier": rate_limit_tier}}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PlanFromProfileTests(unittest.TestCase):
    def test_max_tiers_come_from_the_rate_limit_tier(self):
        self.assertEqual(anthropic_plan.plan_from_profile(profile("claude_max", "default_claude_max_5x", True, False)), "Max 5x")
        self.assertEqual(anthropic_plan.plan_from_profile(profile("claude_max", "default_claude_max_20x", True)), "Max 20x")

    def test_max_without_a_known_tier_is_plain_max(self):
        self.assertEqual(anthropic_plan.plan_from_profile(profile("claude_max", "something_new", True)), "Max")

    def test_pro_from_organization_type_or_account_flag(self):
        self.assertEqual(anthropic_plan.plan_from_profile(profile("claude_pro", "default_claude_ai", False, True)), "Pro")
        self.assertEqual(anthropic_plan.plan_from_profile(profile(None, None, False, True)), "Pro")

    def test_unknown_or_broken_profile_reports_nothing(self):
        for value in (profile("claude_team", "default_raven"), {}, None, {"account": "x", "organization": []}):
            self.assertIsNone(anthropic_plan.plan_from_profile(value))


class FetchPlanTests(unittest.TestCase):
    def setUp(self):
        anthropic_plan.CACHE.clear()
        self.requests = []

    def opener(self, body):
        def open_(request, timeout):
            self.requests.append(request)
            return Response(json.dumps(body).encode())
        return open_

    def test_reads_the_oauth_profile_with_the_account_token(self):
        plan = anthropic_plan.fetch_plan("token-a", opener=self.opener(profile("claude_max", "default_claude_max_5x", True)))
        self.assertEqual(plan, "Max 5x")
        request = self.requests[0]
        self.assertEqual(request.full_url, "https://api.anthropic.com/api/oauth/profile")
        self.assertEqual(request.get_header("Authorization"), "Bearer token-a")
        self.assertEqual(request.get_header("Anthropic-beta"), "oauth-2025-04-20")

    def test_caches_per_token_so_usage_reads_do_not_refetch(self):
        opener = self.opener(profile("claude_pro"))
        anthropic_plan.fetch_plan("token-a", opener=opener, now=1000)
        anthropic_plan.fetch_plan("token-a", opener=opener, now=1000 + anthropic_plan.TTL - 1)
        self.assertEqual(len(self.requests), 1)
        anthropic_plan.fetch_plan("token-a", opener=opener, now=1000 + anthropic_plan.TTL + 1)
        anthropic_plan.fetch_plan("token-b", opener=opener, now=1000)
        self.assertEqual(len(self.requests), 3)

    def test_failures_and_missing_tokens_return_none_without_raising(self):
        def fail(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "no", {}, None)
        self.assertIsNone(anthropic_plan.fetch_plan("token-a", opener=fail))
        self.assertIsNone(anthropic_plan.fetch_plan("", opener=fail))
        self.assertIsNone(anthropic_plan.fetch_plan("sk-ant-api03-key", opener=fail))

    def test_does_not_keep_raw_tokens_as_cache_keys(self):
        anthropic_plan.fetch_plan("token-secret", opener=self.opener(profile("claude_pro")))
        self.assertNotIn("token-secret", anthropic_plan.CACHE)


if __name__ == "__main__":
    unittest.main()
