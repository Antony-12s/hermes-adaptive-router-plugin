"""API-key providers: a pay-per-token fallback once the subscription pools are near their limit.

HARP never stores or reads an API key. Hermes keeps the keys (environment variables or its credential
pool) and reports only whether one is set. Spend is an estimate: tokens from the live routing log times
a blended per-million price (Hermes' bundled price, or one the user entered). A provider whose price is
unknown is never picked automatically, so the daily budget always means something.
"""

import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

PROVIDERS = (   # (Hermes provider id, label, the environment variable Hermes reads)
    ("deepseek", "DeepSeek", "DEEPSEEK_API_KEY"),
    ("zai", "GLM (Z.AI)", "GLM_API_KEY"),
    ("alibaba", "Qwen (DashScope)", "DASHSCOPE_API_KEY"),
    ("gemini", "Gemini", "GEMINI_API_KEY"),
    ("openrouter", "OpenRouter", "OPENROUTER_API_KEY"),
    ("openai-api", "OpenAI API", "OPENAI_API_KEY"),
)
IDS = tuple(p[0] for p in PROVIDERS)
LABEL = {p[0]: p[1] for p in PROVIDERS}
ENV = {p[0]: p[2] for p in PROVIDERS}
FILE = "harp-api.json"
CHEAP_TIER = 2            # tiers up to this use a provider's cheap model; harder work uses its strong model
MAX_BUDGET = 1000.0
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,149}")
DEFAULT_MODELS = {        # (cheap, strong) when the user has not picked; used only if Hermes lists the model
    "deepseek": ("deepseek-flash", "deepseek-v4-pro"),
    "zai": ("glm-5", "glm-5.2"),
    "alibaba": ("qwen3.6-flash", "qwen3.8-max"),
    "gemini": ("gemini-3.1-flash-lite", "gemini-4-hydrogen"),
    "openrouter": ("deepseek/deepseek-v4-flash-0731", "anthropic/claude-opus-5.5"),
    "openai-api": ("gpt-6-luna", "gpt-6.2-sol"),
}
DEFAULTS = {"enabled": False, "daily_budget": 5.0, "order": list(IDS), "providers": {}}


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError
    return float(value)


def validate(body):
    if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
        raise ValueError("Send enabled, daily_budget, order and providers.")
    try:
        budget = _number(body.get("daily_budget"), 0, MAX_BUDGET)
    except ValueError:
        raise ValueError(f"The daily budget must be between 0 and {MAX_BUDGET:g} USD.") from None
    order = [p for p in dict.fromkeys(body.get("order") or []) if p in IDS]
    order += [p for p in IDS if p not in order]
    providers = {}
    for provider, row in (body.get("providers") or {}).items():
        if provider not in IDS or not isinstance(row, dict):
            raise ValueError(f"Unknown API provider: {provider}")
        cheap, strong = (str(row.get(key) or "").strip() for key in ("cheap", "strong"))
        if any(name and not MODEL_NAME.fullmatch(name) for name in (cheap, strong)):
            raise ValueError(f"{LABEL[provider]}: invalid model name.")
        price = row.get("price")
        if price in (None, ""):
            price = None
        else:
            try:
                price = _number(price, 0, 1000)
            except ValueError:
                raise ValueError(f"{LABEL[provider]}: the price must be 0 to 1000 USD per million tokens.") from None
        providers[provider] = {"use": row.get("use") is True, "cheap": cheap, "strong": strong, "price": price}
    return {"enabled": body["enabled"], "daily_budget": budget, "order": order, "providers": providers}


KEY_SHAPE = re.compile(r"\S{8,300}")


def add_key(provider, key, pool=None, adder=None):
    """Store a key in Hermes' credential pool for `provider`. HARP keeps no copy.

    The key goes straight to Hermes' own helper in this process, so it never appears in a command line,
    a log line or a response. Returns the same status view as hermes_status()."""
    if provider not in IDS:
        raise ValueError(f"Unknown API provider: {provider}")
    key = key.strip() if isinstance(key, str) else ""
    if not KEY_SHAPE.fullmatch(key):
        raise ValueError("Paste the whole key: 8 to 300 characters, no spaces.")
    if adder is None:
        from types import SimpleNamespace
        from hermes_cli.auth_commands import _add_api_key_credential as adder_fn
        from contextlib import redirect_stdout
        import io

        def adder(provider_id, token):
            with redirect_stdout(io.StringIO()):   # Hermes prints a confirmation line; keep it out of logs
                adder_fn(SimpleNamespace(api_key=token, label="HARP"), provider_id, pool or _load(provider_id))
    adder(provider, key)
    return hermes_status(provider)


def _load(provider):
    from agent.credential_pool import load_pool
    return load_pool(provider)


def settings(home):
    try:
        return validate(json.loads((Path(home) / FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return {**DEFAULTS, "order": list(IDS), "providers": {}}


def save(home, body):
    value = validate(body)
    home = Path(home)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=home, delete=False) as handle:
        json.dump(value, handle)
    os.replace(handle.name, home / FILE)
    return value


# --------------------------------------------------------------------------- Hermes adapters
def hermes_status(provider):
    """{"configured", "key_source"} without the key itself; never raises."""
    try:
        from hermes_cli.auth import get_api_key_provider_status
        status = get_api_key_provider_status(provider)
    except Exception:  # noqa: BLE001 - an unknown provider on this Hermes build is simply not configured
        status = {}
    configured = bool(status.get("configured"))
    if not configured and os.environ.get(ENV[provider]):
        configured, status = True, {"key_source": ENV[provider]}
    source = str(status.get("key_source") or "") if configured else ""
    return {"configured": configured, "key_source": source if re.fullmatch(r"[\w:.-]{0,80}", source) else ""}


def hermes_price(model, provider):
    """Blended $ per million tokens (3 parts input, 1 part output) from Hermes' price table, or None."""
    try:
        from agent.usage_pricing import get_pricing_entry
        entry = get_pricing_entry(model, provider=provider)
        cost_in, cost_out = entry.input_cost_per_million, entry.output_cost_per_million
        if cost_in is None or cost_out is None:
            return None
        return round((3 * float(cost_in) + float(cost_out)) / 4, 4)
    except Exception:  # noqa: BLE001
        return None


def hermes_models(provider):
    try:
        from hermes_cli.models import curated_models_for_provider
        return [m[0] if isinstance(m, (tuple, list)) else str(m) for m in curated_models_for_provider(provider)][:40]
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- spend and picks
def _midnight(now):
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def usage_today(home, now=None):
    """{provider: {model: (tokens, requests)}} for API providers since local midnight, from live routing."""
    now = time.time() if now is None else now
    path = Path(home) / "harp-router.sqlite3"
    if not path.is_file():
        return {}
    out = {}
    marks = ",".join("?" * len(IDS))
    with closing(sqlite3.connect(path)) as db:
        try:
            rows = db.execute(f"SELECT provider, model, COALESCE(SUM(tokens), 0), COUNT(*) FROM live_calls"
                              f" WHERE provider IN ({marks}) AND ts >= ? GROUP BY provider, model",
                              (*IDS, _midnight(now))).fetchall()
        except sqlite3.OperationalError:
            return {}
    for provider, model, tokens, requests in rows:
        out.setdefault(provider, {})[model] = (int(tokens), int(requests))
    return out


def model_price(provider, model, config, price=hermes_price):
    """The user's price wins; otherwise Hermes' table; None when neither knows."""
    override = (config.get("providers", {}).get(provider) or {}).get("price")
    return override if override is not None else (price(model, provider) if model else None)


def default_model(provider, index, catalog):
    preferred = DEFAULT_MODELS.get(provider, ("", ""))[index]
    if preferred and (preferred in catalog or not catalog):
        return preferred
    return (catalog[-1] if index == 0 else catalog[0]) if catalog else ""


def overview(home, status=hermes_status, price=hermes_price, models=hermes_models, now=None):
    config = settings(home)
    usage = usage_today(home, now)
    rows, total = [], 0.0
    for provider in config["order"]:
        row = config["providers"].get(provider) or {"use": False, "cheap": "", "strong": "", "price": None}
        catalog = models(provider)
        cheap = row["cheap"] or default_model(provider, 0, catalog)
        strong = row["strong"] or default_model(provider, 1, catalog)
        spent, tokens, requests, unpriced = 0.0, 0, 0, 0
        for model, (used, count) in usage.get(provider, {}).items():
            rate = model_price(provider, model, config, price)
            tokens, requests = tokens + used, requests + count
            if rate is None:
                unpriced += used
            else:
                spent += used / 1_000_000 * rate
        total += spent
        rows.append({"id": provider, "label": LABEL[provider], "env": ENV[provider], **status(provider),
                     "use": row["use"], "cheap": cheap, "strong": strong, "price": row["price"],
                     "prices": {"cheap": model_price(provider, cheap, config, price),
                                "strong": model_price(provider, strong, config, price)},
                     "models": catalog, "spent_today": round(spent, 4), "tokens_today": tokens,
                     "requests_today": requests, "unpriced_tokens": unpriced})
    return {"settings": config, "providers": rows, "spent_today": round(total, 4),
            "budget_left": round(max(0.0, config["daily_budget"] - total), 4)}


def effort_for(tier):
    return "low" if tier <= 1 else "medium" if tier <= CHEAP_TIER else "high"


def fallback(home, tier, status=hermes_status, price=hermes_price, models=hermes_models, now=None):
    """The rung to use when no subscription pool has room: {"provider", "model", "effort"} or None.

    Only providers the user switched on, with a key in Hermes, a known price and budget left today."""
    view = overview(home, status, price, models, now)
    config = view["settings"]
    if not config["enabled"] or view["budget_left"] <= 0:
        return None
    for row in view["providers"]:
        size = "cheap" if tier <= CHEAP_TIER else "strong"
        if row["use"] and row["configured"] and row[size] and row["prices"][size] is not None:
            return {"provider": row["id"], "model": row[size], "effort": effort_for(tier), "api": True}
    return None
