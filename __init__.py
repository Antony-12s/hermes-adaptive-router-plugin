"""Hermes Adaptive Router Plugin."""

import json
import os
from dataclasses import dataclass
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
    with Path(config_path).open(encoding="utf-8-sig") as config_file:
        policy = RoutingPolicy(**json.load(config_file))

    def on_llm_request(request, provider, model, api_mode, **_metadata):
        return route_request(
            policy, request=request, provider=provider, model=model, api_mode=api_mode
        )

    ctx.register_middleware("llm_request", on_llm_request)
