"""Input contract for HARP-owned account operations."""

PROVIDERS = frozenset({"anthropic", "openai-codex", "jev"})
KEY_PROVIDERS = frozenset({"jev"})      # accounts are API keys: no OAuth sign-in, no usage windows


def require_provider(provider):
    if provider not in PROVIDERS:
        raise ValueError("Unsupported provider for account actions")
    return provider


def require_account_ids(targets):
    if not isinstance(targets, list) or not targets or any(
        not isinstance(target, str) or not target or target.strip() != target
        for target in targets
    ) or len(set(targets)) != len(targets):
        raise ValueError("Choose distinct account ids")
    return targets
