"""Anthropic plan names (Pro, Max 5x, Max 20x) from the OAuth profile endpoint.

Hermes' Anthropic usage reader calls /api/oauth/usage, which has no plan field, so its
snapshot plan is always empty. /api/oauth/profile (the endpoint Claude Code uses) carries
organization_type and rate_limit_tier. The token is sent only in the Authorization header;
the cache is keyed by a hash so raw tokens are never kept.
"""

import hashlib
import json
import time
import urllib.request

PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
TTL = 6 * 3600            # plans change rarely; one profile read per account every 6 h
CACHE = {}                # sha256(token) -> (read_at, plan or None)
MAX_TIERS = (("20x", "Max 20x"), ("5x", "Max 5x"))


def plan_from_profile(profile):
    if not isinstance(profile, dict):
        return None
    account = profile.get("account") if isinstance(profile.get("account"), dict) else {}
    organization = profile.get("organization") if isinstance(profile.get("organization"), dict) else {}
    kind = str(organization.get("organization_type") or "")
    tier = str(organization.get("rate_limit_tier") or "")
    if kind == "claude_max" or account.get("has_claude_max") is True:
        return next((name for suffix, name in MAX_TIERS if tier.endswith("_" + suffix)), "Max")
    if kind == "claude_pro" or account.get("has_claude_pro") is True:
        return "Pro"
    return None


def fetch_plan(token, opener=urllib.request.urlopen, now=None):
    token = str(token or "").strip()
    if not token or token.startswith("sk-ant-api"):   # API keys have no Claude plan
        return None
    now = time.time() if now is None else now
    key = hashlib.sha256(token.encode()).hexdigest()
    cached = CACHE.get(key)
    if cached and now - cached[0] < TTL:
        return cached[1]
    request = urllib.request.Request(PROFILE_URL, headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json", "User-Agent": "claude-code/2.1.0"})
    try:
        with opener(request, timeout=15) as response:
            plan = plan_from_profile(json.loads(response.read() or b"{}"))
    except Exception:  # noqa: BLE001 - the plan is a label; a failed read must not break usage
        return cached[1] if cached else None
    CACHE[key] = (now, plan)
    return plan
