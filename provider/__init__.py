"""HARP as a Hermes model provider.

Pick `HARP` in the model picker with one of `harp-save`, `harp-balanced`, `harp-best` (the model is the routing
mode). HARP's pre_llm_call hook (the standalone `harp` plugin) binds the real provider and model it picks onto
each turn. This profile only supplies the client for what is not bound: auxiliary tasks whose provider is
`auto` (titles, compression) and a turn whose bind failed. Those go to one cheap rung through Hermes'
own `call_llm`, so HARP never touches a credential.

Install: copy this folder to `$HERMES_HOME/plugins/model-providers/harp/` and set `HARP_ROUTER_KEY=local`
in `$HERMES_HOME/.env` (a placeholder, not a secret: the picker lists a provider only once it is configured).
"""

from types import SimpleNamespace

from providers import register_provider
from providers.base import ProviderProfile

MODELS = ("harp-save", "harp-balanced", "harp-best")
FACADE_ROUTE = ("anthropic", "claude-haiku-5-5")   # unbound calls: cheapest rung
CONTEXT_LENGTH = 200_000                             # smallest context window among the rungs HARP binds


def _stream_chunk(response):
    """A completed chat response as the single delta chunk a streaming caller expects."""
    choice = (getattr(response, "choices", None) or [None])[0]
    message = getattr(choice, "message", None)
    tool_calls = [
        SimpleNamespace(index=getattr(call, "index", index), id=getattr(call, "id", None),
                        type=getattr(call, "type", None) or "function",
                        function=SimpleNamespace(name=getattr(call.function, "name", None),
                                                 arguments=getattr(call.function, "arguments", None)))
        for index, call in enumerate(getattr(message, "tool_calls", None) or [])
    ] or None
    delta = SimpleNamespace(content=getattr(message, "content", None), tool_calls=tool_calls,
                            **{key: getattr(message, key, None) for key in ("reasoning_content", "reasoning", "reasoning_details")})
    return SimpleNamespace(id=getattr(response, "id", None), model=getattr(response, "model", None),
                           usage=getattr(response, "usage", None),
                           choices=[SimpleNamespace(index=0, delta=delta,
                                                    finish_reason=getattr(choice, "finish_reason", None) or "stop")])


class _Completions:
    def __init__(self, call=None):
        self._call = call

    def create(self, **kwargs):
        call = self._call
        if call is None:
            from agent.auxiliary_client import call_llm as call
        response = call(provider=FACADE_ROUTE[0], model=FACADE_ROUTE[1], messages=kwargs.get("messages") or [],
                        tools=kwargs.get("tools"), max_tokens=kwargs.get("max_tokens") or 4096,
                        temperature=kwargs.get("temperature"))
        return iter((_stream_chunk(response),)) if kwargs.get("stream") else response


class HarpFacade:
    """OpenAI-client-shaped: `chat.completions.create(**kwargs)`."""

    base_url = "harp://local"
    api_key = "local"

    def __init__(self, call=None):
        self.chat = SimpleNamespace(completions=_Completions(call))

    def close(self):
        pass


class HarpProfile(ProviderProfile):
    def create_client(self, **client_kwargs):
        return HarpFacade()

    def get_model_context_length(self, model):
        return CONTEXT_LENGTH


register_provider(HarpProfile(
    name="harp",
    display_name="HARP",
    description="Routes every message to the right Claude or Codex model and effort",
    env_vars=("HARP_ROUTER_KEY",),
    base_url="harp://local",
    api_mode="chat_completions",
    auth_type="api_key",
    supports_model_listing=False,
    supports_health_check=False,
    fallback_models=MODELS,
))
