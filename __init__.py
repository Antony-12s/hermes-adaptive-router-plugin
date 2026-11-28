"""Hermes Adaptive Router Plugin."""

import json
import os
import sqlite3
import threading
import time
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


def _sibling(name):
    """Load a dashboard/ module by path; it is shared with the Dashboard API."""
    import importlib.util
    import sys

    key = f"harp_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("dashboard") / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def _hermes_home():
    from hermes_constants import get_hermes_home

    return get_hermes_home()


def _find_agent(session_id):
    """The AIAgent of this session: hooks run while its turn waits on another thread's stack."""
    import sys

    for frame in sys._current_frames().values():
        depth = 0
        while frame is not None and depth < 60:
            for name in ("agent", "self"):
                candidate = frame.f_locals.get(name)
                if (candidate is not None and hasattr(candidate, "_credential_pool")
                        and getattr(candidate, "session_id", None) == session_id):
                    return candidate
            frame = frame.f_back
            depth += 1
    return None


CREDIT_WALL_CODES = {"credits_required", "out_of_credits"}


def classify_credit_wall(provider="", model="", error_code="", error_message="", error_body=None, **_):
    """transform_api_error_classification: when an Anthropic account's allowance for a model such as
    Fable is used up, Anthropic answers HTTP 429 credits_required. Hermes reads that as a plain rate
    limit: it retries the same account with long backoffs. Report it as a usage-window billing wall
    instead, so Hermes moves to the next account at once and benches only this model on this account
    until the window resets. When no account is left the turn ends with a question for the user."""
    if provider != "anthropic":
        return None
    error = (error_body or {}).get("error") if isinstance(error_body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    details = details if isinstance(details, dict) else {}
    codes = {str(error_code or ""), str(details.get("error_code") or ""), str(details.get("disabled_reason") or "")}
    if not codes & CREDIT_WALL_CODES and "usage credits are required" not in str(error_message or "").lower():
        return None
    name = details.get("model_display_name") or model or "This model"
    return {"reason": "billing", "retryable": False, "should_rotate_credential": True,
            "should_fallback": False, "error_context": {"usage_window": True, "harp": "credit_wall", "model": model},
            "message": f"{name} is out of allowance on every Anthropic account right now. "
                       "Which model should I use instead? Pick one with /model (for example Opus 5.5 or Sonnet 5), "
                       "wait for the window to reset, or buy usage credits."}


def track_account(session_id=None, provider=None, platform=None, usage=None, model=None, response_model=None,
                  **_metadata):
    """post_api_request: count the call's tokens and session against the pool account it used."""
    record_live_request(provider=provider, usage=usage, model=model, response_model=response_model, **_metadata)
    try:
        agent = _find_agent(str(session_id or "")) if session_id else None
        pool = getattr(agent, "_credential_pool", None)
        entry = pool.current() if pool is not None else None
        pool_provider = getattr(pool, "provider", "") or ""
        if entry is None or (provider and pool_provider and provider != pool_provider):
            return
        _sibling("pool_state").record_call(
            _hermes_home() / "harp-activity.sqlite3", pool_provider, entry.label or entry.id,
            str(session_id), platform or getattr(agent, "platform", "") or "", reported_tokens(usage),
            model=str(response_model or model or getattr(agent, "model", "") or "?").split("/")[-1])
        if pool_provider in _sibling("pool_state").TIMER_PROVIDERS:
            balance_soon(pool, pool_provider, _hermes_home())
    except Exception:  # noqa: BLE001 - tracking must never break a turn
        pass


BALANCE_EVERY = 60         # seconds between chat-triggered balances per provider
_BALANCED_AT = {}


def balance_soon(pool, provider, home, clock=time.time, start=None):
    """After a chat call, balance this pool in the background (at most once a minute) when
    auto-balance is on, so an account that just crossed 90% steps aside before the next call
    instead of waiting up to 5 minutes for the HARP Timer and running into a 429."""
    state = _sibling("pool_state")
    if not state.Store(home).alerts()["auto_balance"]:
        return
    now = clock()
    if now - _BALANCED_AT.get(provider, 0) < BALANCE_EVERY:
        return
    _BALANCED_AT[provider] = now
    job = lambda: state.balance_pool(home / "harp-activity.sqlite3", pool, provider, read_usage,
                                     _sibling("pool_actions"), now, "chat")
    (start or (lambda fn: threading.Thread(target=fn, daemon=True, name="harp-balance").start()))(job)


_ENGINE = None
_TURN_ROUTES = {}          # session id -> decision to apply for this turn (apply mode only)
_LIVE_REQUESTS = {}        # request id -> bounded prompt and effort from the outgoing call
_LIVE_REQUESTS_LOCK = threading.Lock()


def request_effort(request):
    """The effort a provider request asks for. Hermes passes {"method", "body"}; Anthropic puts it in
    output_config.effort (adaptive) or a thinking budget, Codex/chat in reasoning(.effort)."""
    body = request.get("body", request) if isinstance(request, dict) else {}
    if not isinstance(body, dict):
        return ""
    pick = lambda value, key: value.get(key) if isinstance(value, dict) else None
    effort = (pick(body.get("output_config"), "effort") or pick(body.get("reasoning"), "effort")
              or body.get("reasoning_effort") or pick(pick(body.get("extra_body"), "reasoning"), "effort"))
    if effort:
        return str(effort)
    thinking = body.get("thinking") if isinstance(body.get("thinking"), dict) else {}
    if thinking.get("type") == "disabled":
        return "off"
    budget = thinking.get("budget_tokens")
    if type(budget) is int and budget > 0:
        return f"{budget // 1000}k budget" if budget >= 1000 else f"{budget} budget"
    return ""


def config_effort(reasoning_config, provider, model, max_tokens):
    """The effort the agent is configured to send, for when Hermes truncated the request payload.

    Hermes caps the payload it hands to hooks at 50,000 characters and replaces anything longer with a
    preview, so a long chat has no readable body. Anthropic goes through the adapter's own mapping so
    xhigh downgrades and models that take no thinking block (Haiku) read the same as what was sent."""
    if not isinstance(reasoning_config, dict):
        return ""
    if provider == "anthropic":
        try:
            from agent.anthropic_adapter import _thinking_kwargs
            return request_effort({"body": _thinking_kwargs(reasoning_config, str(model or ""), max_tokens or 16000)})
        except Exception:  # noqa: BLE001 - private Hermes helper; fall back to the configured value
            pass
    if reasoning_config.get("enabled") is False:
        return "off"
    return str(reasoning_config.get("effort") or "")


def remember_live_request(api_request_id=None, user_message=None, request=None, middleware_trace=None, **metadata):
    if not api_request_id:
        return
    effort = metadata.get("reasoning_effort") or request_effort(request)
    if not effort and metadata.get("session_id"):
        agent = _find_agent(str(metadata["session_id"]))
        effort = config_effort(getattr(agent, "reasoning_config", None), metadata.get("provider"),
                               metadata.get("model"), metadata.get("max_tokens"))
    text = _sibling("router").message_text(user_message)
    source = ""
    for trace in middleware_trace or ():
        if isinstance(trace, dict) and trace.get("source") == "harp-router":
            classifier = str(trace.get("reason", "")).partition(" ")[0]
            if classifier in ("jev", "local"):
                source = classifier
    with _LIVE_REQUESTS_LOCK:
        _LIVE_REQUESTS[api_request_id] = (" ".join(text.split())[:120], str(effort or "")[:20], source)
        if len(_LIVE_REQUESTS) > 128:
            _LIVE_REQUESTS.pop(next(iter(_LIVE_REQUESTS)))


def record_live_request(api_request_id=None, provider=None, model=None, response_model=None,
                        reasoning_effort=None, api_duration=None, usage=None, api_call_count=None,
                        aux_task=None, error=None, error_type=None, **_metadata):
    try:
        name = response_model or model
        if not provider or not name:
            return
        text = effort = source = ""
        if not aux_task:
            with _LIVE_REQUESTS_LOCK:
                text, effort, source = _LIVE_REQUESTS.pop(api_request_id, ("", "", ""))
        kind = f"aux:{str(aux_task)[:48]}" if aux_task else "main"
        duration = api_duration if type(api_duration) in (int, float) and 0 <= api_duration < 86400 else None
        step = api_call_count if not aux_task and type(api_call_count) is int else None
        tokens = reported_tokens(usage)
        store = _engine().store
        store.log_live(kind, text if not aux_task else "", str(provider)[:80], str(name)[:150],
                       str(reasoning_effort or effort)[:20], duration, tokens,
                       step, "error" if error or error_type else "ok", source)
        store.add_total(tokens, _sibling("pool_state").estimate_price(str(name), tokens))
    except Exception:  # noqa: BLE001 - observers must never interrupt requests
        pass


def record_auxiliary_request(aux_task=None, **kwargs):
    record_live_request(aux_task=aux_task or "unknown", **kwargs)


def _engine():
    global _ENGINE
    if _ENGINE is None:
        def jev_entries():
            from agent.credential_pool import load_pool
            return load_pool("jev").entries()

        _ENGINE = _sibling("router_engine").Engine(_hermes_home(), jev_entries)
    return _ENGINE


def _publish_quota(engine, settings, session, provider, decision):
    if not session or not provider or not decision:
        return
    from hermes_cli.plugin_events import broadcast_plugin_event

    loads = engine.loads(settings)
    active = loads.get(provider, {})
    quota = None
    if active.get("pool_near_limit"):
        pick = None
        if settings.get("ladder") and decision.get("tier") is not None:
            ladder = _sibling("ladder")
            other = next((name for name in ladder.RUNGS if name != provider
                          and loads.get(name, {}).get("pool_has_room")), None)
            if other:
                pick = ladder.rung(other, decision["tier"])
            else:   # every subscription pool is near its limit: an API key, if the user allowed one
                try:
                    pick = _sibling("api_keys").fallback(engine.home, decision["tier"])
                except Exception:  # noqa: BLE001 - no API fallback must still publish the notice
                    pick = None
            quota = {"provider": provider, "used": active["used"], "pick": pick, "auto": settings["apply"]}
            broadcast_plugin_event("harp", "quota", {"session_id": session, "quota": quota})
            return
        candidates = {f"{row['provider']}:{row['model']}": row for row in settings["candidates"]}
        ranked = [decision["pick"], *sorted(candidates, key=lambda item: -decision.get("probs", {}).get(item, 0))]
        alternate = next((candidates[item] for item in ranked if item in candidates
                          and candidates[item]["provider"] != provider
                          and loads.get(candidates[item]["provider"], {}).get("pool_has_room")), None)
        if alternate:
            effort = ("low", "medium", "high")
            level = decision["effort"] if decision["effort"] in effort else "medium"
            cap = alternate.get("cap", "high")
            pick = {"provider": alternate["provider"], "model": alternate["model"],
                    "effort": effort[min(effort.index(level), effort.index(cap))]}
        quota = {"provider": provider, "used": active["used"], "pick": pick, "auto": settings["apply"]}
    broadcast_plugin_event("harp", "quota", {"session_id": session, "quota": quota})


def _decide_turn(engine, settings, text, session, platform, model, provider):
    decision = engine.decide(text, session, platform, model)
    _quiet(_publish_quota, engine, settings, session, provider, decision)
    return decision


def _route_harp_turn(engine, agent, text, session, platform, model):
    """The session is on the `harp` provider: decide now and bind the pick onto this one turn."""
    import logging

    route = _sibling("provider_route")
    decision = engine.decide(text, session, platform, model, mode=route.mode_for(getattr(agent, "model", "") or model))
    if not decision:
        return
    bound, reason = route.bind_turn(agent, decision, route.hermes_binder)
    if not bound:
        logging.getLogger(__name__).warning("HARP could not bind %s for this turn (%s); it runs on the HARP facade",
                                            decision.get("pick"), reason)


def route_turn(session_id=None, user_message=None, platform=None, model=None, **_metadata):
    """pre_llm_call: decide provider/model/effort for this message.

    A session on the `harp` provider is always routed: the pick is bound onto the turn, any provider.
    Otherwise shadow mode (default) decides on a background thread and only records it, and apply mode
    decides now and keeps the pick for this turn when it stays on the session's own provider."""
    try:
        engine = _engine()
        session = str(session_id or "")
        _TURN_ROUTES.pop(session, None)
        agent = _find_agent(session)
        if int(getattr(agent, "_delegate_depth", 0) or 0) > 0:
            return None   # delegate_task child: its model is the delegation backend's (model_map) call
        text = _sibling("router").message_text(user_message)
        if agent is not None and _sibling("provider_route").is_harp(agent):
            _route_harp_turn(engine, agent, text, session, platform or "", model or "")
            return None
        settings = engine.settings()
        if not settings["enabled"] or not (settings["candidates"] or settings.get("ladder")):
            return None
        provider = getattr(agent, "provider", "") or ""
        if not settings["apply"]:
            import threading
            threading.Thread(target=_quiet, args=(_decide_turn, engine, settings, text, session, platform or "", model or "", provider),
                             daemon=True, name="harp-route").start()
            return None
        decision = _decide_turn(engine, settings, text, session, platform or "", model or "", provider)
        if decision and agent is not None and decision["pick"].split(":", 1)[0] == provider:
            _TURN_ROUTES[session] = decision
    except Exception:  # noqa: BLE001 - routing must never break a turn
        pass
    return None


def _quiet(function, *args):
    try:
        function(*args)
    except Exception:  # noqa: BLE001
        pass


def _thinking(model, effort, max_tokens):
    from agent.anthropic_adapter import _thinking_kwargs
    return _thinking_kwargs({"enabled": True, "effort": effort}, model, max_tokens)


def apply_turn_route(request=None, session_id=None, provider=None, api_mode=None, **_context):
    """llm_request middleware: use this turn's pick (same provider only). Any doubt: change nothing."""
    try:
        decision = _TURN_ROUTES.get(str(session_id or ""))
        if not decision or decision["pick"].split(":", 1)[0] != provider:
            return None
        model = decision["pick"].split(":", 1)[1]
        routed = _sibling("router").apply_route(request, api_mode, model, decision["effort"], _thinking)
        return {"request": routed, "source": "harp-router",
                "reason": f"{decision['by']} picked {model} ({decision['effort']})"} if routed else None
    except Exception:  # noqa: BLE001
        return None


def read_usage(provider, entry):
    """Read one pool credential's own usage windows, read-only (no credential repair)."""
    from agent.account_usage import fetch_account_usage
    from agent.account_usage_cache import _identity_id_for

    return fetch_account_usage(provider, base_url=entry.runtime_base_url, api_key=entry.runtime_api_key,
                               read_only=True, identity_id=_identity_id_for(provider, entry))


def run_tick(_args=None):
    from datetime import datetime

    from agent.credential_pool import load_pool

    state = _sibling("pool_state")
    store = state.Store(_hermes_home())
    lines = state.run_timer(store, _hermes_home() / "harp-activity.sqlite3", load_pool, datetime.now(),
                            read=read_usage, send=_sibling("harp_timer").sender(store.alerts()),
                            balance=_sibling("pool_actions"))
    for line in lines:
        print(line)


def _setup_cli(parser):
    commands = parser.add_subparsers(dest="harp_command")
    commands.add_parser("tick", help="Run the HARP timer once: park schedules, usage history, alerts")
    parser.set_defaults(func=lambda args: run_tick(args) if args.harp_command == "tick" else parser.print_help())


def register(ctx):
    ctx.register_hook("post_api_request", track_account)
    ctx.register_hook("pre_llm_call", route_turn)
    ctx.register_hook("pre_api_request", remember_live_request)
    ctx.register_hook("post_auxiliary_call", record_auxiliary_request)
    ctx.register_hook("transform_api_error_classification", classify_credit_wall)
    if hasattr(ctx, "register_delegation_provider"):
        try:
            provider = _sibling("delegation").backend(_engine)
        except ImportError:
            pass  # Older Hermes builds can still use the main-chat router.
        else:
            ctx.register_delegation_provider(provider)
    if hasattr(ctx, "register_middleware"):
        ctx.register_middleware("llm_request", apply_turn_route)
    if hasattr(ctx, "register_cli_command"):
        ctx.register_cli_command(name="harp", help="HARP account timer: hermes harp tick",
                                 setup_fn=_setup_cli)
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
