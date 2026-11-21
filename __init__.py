"""Hermes Adaptive Router Plugin."""

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RoutingPolicy:
    provider: str
    primary_model: str
    fast_model: str
    max_input_chars: int
    allow_tools: bool = False

    def __post_init__(self):
        for field in ("provider", "primary_model", "fast_model"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value or value.strip() != value:
                raise ValueError(f"{field} must be a non-empty string without surrounding whitespace")
        if self.primary_model == self.fast_model:
            raise ValueError("primary_model and fast_model must differ")
        if type(self.max_input_chars) is not int or self.max_input_chars < 1:
            raise ValueError("max_input_chars must be a positive integer")
        if type(self.allow_tools) is not bool:
            raise ValueError("allow_tools must be a boolean")


def route_request(policy, *, request, provider, model, api_mode):
    """Override only short text requests on the configured primary route."""
    if api_mode != "chat_completions" or provider != policy.provider:
        return None
    if model != policy.primary_model or request.get("model") != policy.primary_model:
        return None
    if request.get("response_format"):
        return None
    if not policy.allow_tools and (request.get("tools") or request.get("functions")):
        return None
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    if any(
        not isinstance(message, dict)
        or message.get("role") not in ("system", "developer", "user", "assistant")
        or not isinstance(message.get("content"), str)
        or message.get("tool_calls")
        or message.get("function_call")
        for message in messages
    ):
        return None
    if messages[-1].get("role") != "user" or not messages[-1]["content"].strip():
        return None
    if sum(len(message["content"]) for message in messages) > policy.max_input_chars:
        return None
    return {
        "request": {**request, "model": policy.fast_model},
        "source": "harp",
        "reason": "text-only chat within configured input budget",
    }


def register(ctx):
    config_path = os.environ.get("HARP_CONFIG")
    if not config_path:
        return
    path = Path(config_path).resolve()
    with path.open(encoding="utf-8-sig") as config_file:
        config = json.load(config_file)
    quotas = config.pop("daily_token_quotas", {})
    if not isinstance(quotas, dict) or any(
        not account or account.strip() != account or account == "Unassigned"
        or type(limit) is not int or limit <= 0
        for account, limit in quotas.items()
    ):
        raise ValueError("daily_token_quotas must map account labels to positive integers")
    policy = RoutingPolicy(**config)

    def on_llm_request(request, provider, model, api_mode, **_metadata):
        return route_request(
            policy, request=request, provider=provider, model=model, api_mode=api_mode
        )

    ctx.register_middleware("llm_request", on_llm_request)

    if quotas:
        register_usage(ctx, path, policy.provider, quotas)


def register_usage(ctx, config_path, tracked_provider, quotas):
    database = config_path.with_suffix(".usage.sqlite3")
    configured_account = os.environ.get("HARP_ACCOUNT", "").strip()
    account = configured_account if configured_account in quotas else "Unassigned"
    tag = os.environ.get("HARP_USAGE_TAG", "").strip() or "default"
    with closing(sqlite3.connect(database)) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage (
                day TEXT NOT NULL,
                account TEXT NOT NULL,
                tag TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                requests INTEGER NOT NULL,
                unmetered INTEGER NOT NULL,
                PRIMARY KEY (day, account, tag, provider, model)
            )
        """)

    def on_response(provider=None, model=None, response_model=None, usage=None, **_metadata):
        if not isinstance(provider, str) or not provider:
            return
        effective_model = response_model or model
        if not isinstance(effective_model, str) or not effective_model:
            return
        tokens = reported_tokens(usage)
        attributed_account = account if provider == tracked_provider else "Unassigned"
        day = datetime.now(timezone.utc).date().isoformat()
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("""
                INSERT INTO daily_usage VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT (day, account, tag, provider, model) DO UPDATE SET
                    tokens = tokens + excluded.tokens,
                    requests = requests + 1,
                    unmetered = unmetered + excluded.unmetered
            """, (day, attributed_account, tag, provider, effective_model,
                  tokens if tokens is not None else 0, int(tokens is None)))

    ctx.register_hook("post_api_request", on_response)


def reported_tokens(usage):
    if not isinstance(usage, dict):
        return None
    total = usage.get("total_tokens")
    if type(total) is int and 0 <= total < 2**62:
        return total
    for input_key, output_key in (
        ("input_tokens", "output_tokens"),
        ("prompt_tokens", "completion_tokens"),
    ):
        counts = (usage.get(input_key), usage.get(output_key))
        if all(type(count) is int and 0 <= count < 2**61 for count in counts):
            return sum(counts)
    return None
