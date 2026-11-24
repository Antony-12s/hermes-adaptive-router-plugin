"""Authenticated Hermes Dashboard endpoints for HARP accounts, actions and usage."""

import importlib.util
import json
import math
import os
import secrets
import shutil
import subprocess
import sqlite3
import sys
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _sibling(name):
    """Load a module beside this file; the Dashboard loads plugin_api.py by path, not as a package."""
    module = sys.modules.get(f"harp_{name}")
    if module is None:
        spec = importlib.util.spec_from_file_location(f"harp_{name}", Path(__file__).with_name(f"{name}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


contract = _sibling("action_contract")
actions = _sibling("pool_actions")
relay_mod = _sibling("signin_relay")
pool_state = _sibling("pool_state")
timer = _sibling("harp_timer")
typesafe = _sibling("typesafe")
anthropic_plan = _sibling("anthropic_plan")
compression = _sibling("compression")
router_engine = _sibling("router_engine")
delegation = _sibling("delegation")
api_keys = _sibling("api_keys")

POOL_LOCK = threading.Lock()
LIVE = {}            # provider -> {"read_at": epoch, "accounts": {entry id: reading}}
FLOWS = {}           # sign-in flow id -> state
WAKE_AT = {}         # entry id -> last wake epoch
WAKE_COOLDOWN = 600
SIGNIN_TTL = 600
WAKE_MODEL = "claude-haiku-4-5"


def _provider(body):
    try:
        return contract.require_provider((body or {}).get("provider"))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def _pool(provider):
    from agent.credential_pool import load_pool

    return load_pool(provider)


def _home():
    from hermes_constants import get_hermes_home

    return get_hermes_home()


def _store():
    return pool_state.Store(_home())


def _db():
    return _home() / "harp-activity.sqlite3"


def _log(provider, kind, text):
    try:
        pool_state.log_event(_db(), provider, kind, text, "page")
    except Exception:  # noqa: BLE001 - the log must never fail an action
        pass


def _sync_timer():
    """Install the HARP Timer while any schedule or alert channel needs it; remove it otherwise."""
    if pool_state.timer_needed(_store()):
        timer.install(str(_home()))
    else:
        timer.remove()


def _is_parked(entry, parked):
    return entry.id in parked or getattr(entry, "last_error_reason", None) == pool_state.PARK_REASON


def _read_usage(provider, entry):
    from agent.account_usage import fetch_account_usage
    from agent.account_usage_cache import _identity_id_for

    return fetch_account_usage(provider, base_url=entry.runtime_base_url, api_key=entry.runtime_api_key,
                               read_only=True, identity_id=_identity_id_for(provider, entry))


def _reading(snapshot):
    peak = actions.account_peak(snapshot)
    return {
        "plan": str(getattr(snapshot, "plan", "") or "")[:80],
        "peak": peak[0] if peak else None,
        "windows": [{
            "label": w.label,
            "used_percent": w.used_percent if isinstance(w.used_percent, (int, float)) and math.isfinite(w.used_percent) else None,
            "reset_at": w.reset_at.isoformat() if w.reset_at else None,
        } for w in getattr(snapshot, "windows", None) or [] if getattr(w, "scope", "account") == "account"],
    }


def _read_all(provider, pool):
    readings, snapshots = {}, {}
    for entry in pool.entries():
        if getattr(entry, "last_status", None) == "dead":
            continue
        if provider in contract.KEY_PROVIDERS:
            readings[entry.id] = {"peak": None, "windows": [],
                                  "check": typesafe.check(entry.runtime_api_key, entry.runtime_base_url or typesafe.BASE_URL)}
            continue
        snapshots[entry.id] = _read_usage(provider, entry)
        readings[entry.id] = _reading(snapshots[entry.id])
        if provider == "anthropic" and not readings[entry.id]["plan"]:   # Hermes' usage API has no plan
            readings[entry.id]["plan"] = anthropic_plan.fetch_plan(entry.runtime_api_key) or ""
    LIVE[provider] = {"read_at": time.time(), "accounts": readings}
    if provider not in contract.KEY_PROVIDERS:
        try:   # the router's load view: weighted by each account's plan
            eligible = {
                entry.id: readings.get(entry.id, {}).get("peak") for entry in pool.entries()
                if getattr(entry, "last_status", None) not in ("dead", "exhausted")
                and getattr(entry, "last_error_reason", None) != "parked"
            }
            detected = {}
            if provider == "anthropic":
                for account in eligible:
                    plan = readings.get(account, {}).get("plan", "").casefold().replace("×", "x").replace(" ", "")
                    for suffix, weight in (("20x", "x20"), ("10x", "x10"), ("5x", "x5")):
                        if "max" in plan and suffix in plan:
                            detected[account] = weight
                            break
            _engine().record_load(provider, eligible, detected)
            for account, weight in detected.items():
                readings[account]["weight"] = weight
        except Exception:  # noqa: BLE001 - load is advisory
            pass
    return snapshots


@router.get("/accounts")
def get_accounts(provider: str):
    """Read account metadata and the active profile's quota from Hermes."""
    if provider not in {"anthropic", "jev", "openai-codex"}:
        raise HTTPException(400, "Unknown HARP provider")

    from hermes_cli.auth import read_credential_pool

    entries = read_credential_pool(provider)
    if provider == "jev" and not entries:
        entries = read_credential_pool("custom:jev")
    live = LIVE.get(provider) or {}
    readings = live.get("accounts", {})
    store = _store()
    parked, schedules = store.parked(provider), store.schedules(provider)
    try:
        activity = pool_state.account_activity(_home() / "harp-activity.sqlite3", provider)
    except Exception:  # noqa: BLE001 - activity is informational
        activity = {}
    display_plans = _engine().settings()["anthropic_display_plans"] if provider == "anthropic" else {}
    accounts = [
        {"id": entry.get("id"), "priority": entry.get("priority"),
         "display_plan": display_plans.get(entry.get("id")),
         "label": entry.get("label") or "Unnamed account",
         "status": entry.get("last_status") or "active",
         "usage": readings.get(entry.get("id")),
         "tokens_24h": (activity.get(entry.get("label") or entry.get("id")) or {}).get("tokens_24h"),
         "requests_24h": (activity.get(entry.get("label") or entry.get("id")) or {}).get("requests_24h"),
         "price_24h": (activity.get(entry.get("label") or entry.get("id")) or {}).get("price_24h"),
         "used_by": (activity.get(entry.get("label") or entry.get("id")) or {}).get("used_by", []),
         "parked": parked.get(entry.get("id")),
         "schedule": (schedules.get(entry.get("id")) or {}).get("every")}
        for entry in sorted((e for e in entries if isinstance(e, dict)),
                            key=lambda e: e.get("priority") if isinstance(e.get("priority"), int) else 10**6)
    ]

    return {"accounts": accounts, "live_read_at": live.get("read_at"),
            "timer": bool(schedules) and timer.installed()}


def _target(body):
    provider = _provider(body)
    entry_id = str((body or {}).get("id") or "")
    if not entry_id:
        raise HTTPException(400, "Choose an account.")
    return provider, entry_id


@router.post("/pool/anthropic/display-plan")
def set_anthropic_display_plan(body: dict):
    from hermes_cli.auth import read_credential_pool

    account_id = body.get("id")
    if not account_id or not any(entry.get("id") == account_id for entry in read_credential_pool("anthropic")
                                 if isinstance(entry, dict)):
        raise HTTPException(404, "Choose an existing Anthropic account.")
    try:
        _engine().set_anthropic_display_plan(account_id, body.get("plan"))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"ok": True}


@router.post("/pool/park")
def pool_park(body: dict):
    """Hold an account out of rotation until `until` (epoch seconds) or until it is unparked."""
    provider, entry_id = _target(body)
    until = (body or {}).get("until")
    if until is not None and (not isinstance(until, (int, float)) or until <= time.time()):
        raise HTTPException(400, "Choose a time in the future, or no end time.")
    with POOL_LOCK:
        try:
            pool_state.park(_store(), _pool(provider), provider, entry_id, until, "user")
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    label = (_store().parked(provider).get(entry_id) or {}).get("label", entry_id)
    _log(provider, "park", f"Parked {label}" + (f" until {datetime.fromtimestamp(until):%a %H:%M}" if until else ""))
    return {"ok": True}


@router.post("/pool/unpark")
def pool_unpark(body: dict):
    provider, entry_id = _target(body)
    with POOL_LOCK:
        pool = _pool(provider)
        label = next((e.label for e in pool.entries() if e.id == entry_id), entry_id)
        pool_state.unpark(_store(), pool, provider, entry_id)
    _log(provider, "unpark", f"{label} back in rotation")
    return {"ok": True}


@router.post("/pool/remove")
def pool_remove(body: dict):
    """Delete an account's saved login from Hermes' pool."""
    provider, entry_id = _target(body)
    with POOL_LOCK:
        pool = _pool(provider)
        index, entry, error = pool.resolve_target(entry_id)
        if entry is None or entry.id != entry_id:
            raise HTTPException(400, error or "That account is not in the pool.")
        pool.remove_index(index)
        _store().forget(provider, entry_id)
    LIVE.get(provider, {}).get("accounts", {}).pop(entry_id, None)
    _log(provider, "remove", f"Removed {entry.label or entry_id}")
    return {"removed": entry.label or entry_id}


@router.post("/pool/schedule")
def pool_schedule(body: dict):
    """Set (every="mon-fri 09:00-17:00") or clear (every="") an account's weekly park schedule."""
    provider, entry_id = _target(body)
    every = str((body or {}).get("every") or "").strip()
    store = _store()
    with POOL_LOCK:
        pool = _pool(provider)
        entry = next((e for e in pool.entries() if e.id == entry_id), None)
        if entry is None:
            raise HTTPException(400, "That account is not in the pool.")
        if every:
            try:
                store.set_schedule(provider, entry_id, entry.label or entry_id, every)
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
        else:
            store.drop_schedule(provider, entry_id)
            if (store.parked(provider).get(entry_id) or {}).get("by") == "schedule":
                pool_state.unpark(store, pool, provider, entry_id)
        from agent.credential_pool import load_pool
        from datetime import datetime as _datetime
        changes = pool_state.tick(store, load_pool, _datetime.now())
    _log(provider, "schedule", f"Schedule for {entry.label or entry_id}: {every}" if every
         else f"Removed the schedule of {entry.label or entry_id}")
    try:
        _sync_timer()
    except RuntimeError as error:
        return {"ok": True, "changes": changes, "warning": str(error)}
    return {"ok": True, "changes": changes}


@router.post("/pool/live")
def pool_live(body: dict):
    """Read each pool account's own provider usage, serially and without repairing credentials."""
    provider = _provider(body)
    with POOL_LOCK:
        _read_all(provider, _pool(provider))
    return LIVE[provider]


@router.post("/pool/balance")
def pool_balance(body: dict):
    """HARP balance: bench accounts at 90% or more, release HARP benches, order by usage."""
    provider = _provider(body)
    if provider in contract.KEY_PROVIDERS:
        raise HTTPException(400, "Type-safe keys have no usage percent to balance by.")
    with POOL_LOCK:
        pool = _pool(provider)
        snapshots = _read_all(provider, pool)
        parked = _store().parked(provider)
        rows = [actions.Row(e.id, e.label or e.id, e.priority,
                            "parked" if _is_parked(e, parked) else getattr(e, "last_status", None),
                            actions.account_peak(snapshots.get(e.id)), actions.benched_by_harp(e))
                for e in pool.entries()]
        plan = actions.plan_balance(rows)
        if plan is None:
            return {"changed": False, "message": "No account usage could be read; nothing changed."}
        actions.apply_balance(pool, plan)
    label = {row.id: row.label for row in rows}
    _log(provider, "balance", "Balanced: " + " > ".join(label[i] for i in plan["order"])
         + (f"; benched {', '.join(label[i] for i in plan['bench'])}" if plan["bench"] else "")
         + (f"; released {', '.join(label[i] for i in plan['release'])}" if plan["release"] else ""))
    return {"changed": True, "benched": [label[i] for i in plan["bench"]],
            "released": [label[i] for i in plan["release"]], "order": [label[i] for i in plan["order"]]}


def _wake_error(error):
    code = getattr(error, "status_code", None)
    if code == 429:
        return "rate-limited: the window is already running"
    if code == 401:
        return "sign-in rejected: sign in again"
    return f"HTTP {code}" if code else type(error).__name__


@router.post("/pool/wake")
def pool_wake(body: dict):
    """Send one tiny Anthropic request per chosen idle account to start its 5-hour window."""
    provider = _provider(body)
    if provider != "anthropic":
        raise HTTPException(400, "Wake is available for Anthropic accounts only.")
    try:
        targets = contract.require_account_ids((body or {}).get("ids"))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

    from agent.anthropic_adapter import _CLAUDE_CODE_SYSTEM_PREFIX, build_anthropic_client

    results = []
    with POOL_LOCK:
        entries = {e.id: e for e in _pool(provider).entries()}
        for target in targets:
            entry = entries.get(target)
            name = (entry.label or target) if entry else target
            if entry is None or getattr(entry, "last_status", None) == "dead":
                results.append({"account": name, "result": "skipped: not usable"})
                continue
            if getattr(entry, "last_error_reason", None) == pool_state.PARK_REASON:
                results.append({"account": name, "result": "skipped: parked"})
                continue
            if time.time() - WAKE_AT.get(target, 0) < WAKE_COOLDOWN:
                results.append({"account": name, "result": "skipped: woken in the last 10 minutes"})
                continue
            if not actions.wake_ready(_read_usage(provider, entry)):
                results.append({"account": name, "result": "skipped: window running or no reading"})
                continue
            try:
                client = build_anthropic_client(entry.runtime_api_key, timeout=60)
                client.messages.create(model=WAKE_MODEL, max_tokens=1,
                                       system=[{"type": "text", "text": _CLAUDE_CODE_SYSTEM_PREFIX}],
                                       messages=[{"role": "user", "content": "hi"}])
            except Exception as error:  # noqa: BLE001 - report the provider outcome, never the token
                results.append({"account": name, "result": f"failed: {_wake_error(error)}"})
                continue
            WAKE_AT[target] = time.time()
            results.append({"account": name, "result": "woke"})
    if results:
        _log(provider, "wake", "Wake: " + "; ".join(f"{r['account']} {r['result']}" for r in results))
    return {"results": results}


def _hermes_command(provider, label):
    args = ["auth", "add", provider, "--type", "oauth", "--label", label, "--no-browser"]
    launcher = shutil.which("hermes")
    return [launcher, *args] if launcher else [sys.executable, "-m", "hermes_cli.main", *args]


def _hermes_env():
    from hermes_constants import get_hermes_home

    env = dict(os.environ, HERMES_HOME=str(get_hermes_home()))
    import hermes_cli
    root = str(Path(hermes_cli.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [root, env.get("PYTHONPATH")]))
    return env


def _drop_new(flow):
    """Remove an account the cancelled command may have saved."""
    try:
        with POOL_LOCK:
            pool = _pool(flow["provider"])
            for entry in [e for e in pool.entries() if e.id not in flow["before"] and e.label == flow["label"]]:
                index, _, _ = pool.resolve_target(entry.id)
                if index is not None:
                    pool.remove_index(index)
    except Exception:  # noqa: BLE001 - cleanup must not fail the request
        pass


def _drop_flow(flow_id, remove_new):
    flow = FLOWS.pop(flow_id, None)
    if flow is None:
        return
    running = flow["relay"].returncode() is None
    flow["relay"].close()
    if remove_new and running:
        _drop_new(flow)


def _expire_flows():
    for flow_id in [k for k, v in FLOWS.items() if time.time() - v["created"] > SIGNIN_TTL]:
        _drop_flow(flow_id, remove_new=True)


@router.post("/signin/start")
def signin_start(body: dict):
    """Start Hermes' own `hermes auth add` and return the sign-in link or device code.

    With "replace": <entry id> it signs that account in again: the new login takes the old
    one's label, position and schedule, and the old login is deleted only after success."""
    _expire_flows()
    provider = _provider(body)
    if provider in contract.KEY_PROVIDERS:
        raise HTTPException(400, "Type-safe accounts are API keys: add or replace the key instead.")
    pool = _pool(provider)
    replace_id = str((body or {}).get("replace") or "")
    old = next((e for e in pool.entries() if e.id == replace_id), None) if replace_id else None
    if replace_id and old is None:
        raise HTTPException(400, "That account is not in the pool.")
    label = (old.label or old.id) if old else str((body or {}).get("label") or "").strip()
    if not label:
        raise HTTPException(400, "Enter a name for the account, such as its email.")
    if not old and any((e.label or "").lower() == label.lower() for e in pool.entries()):
        raise HTTPException(400, "An account with this name is already in the pool.")
    relay = relay_mod.Relay(_hermes_command(provider, label), _hermes_env())
    info = relay.wait_for(relay_mod.parse, 90)
    if not info:
        reason = relay.reason() or "no sign-in link was printed"
        relay.close()
        raise HTTPException(502, f"Could not start the sign-in: {reason}")
    flow_id = secrets.token_urlsafe(16)
    FLOWS[flow_id] = {"relay": relay, "info": info, "provider": provider, "label": label,
                      "before": {e.id for e in pool.entries()}, "created": time.time(), "lock": threading.Lock(),
                      "replace": old.id if old else None}
    return {"flow": flow_id, **info, "label": label, "expires_in": SIGNIN_TTL}


def _take_place(provider, old_id, new_entry):
    """After a successful sign-in again: delete the old login, move the new one to its position."""
    store = _store()
    with POOL_LOCK:
        pool = _pool(provider)
        index, old, _ = pool.resolve_target(old_id)
        if old is None or old.id != old_id:
            return
        pool.remove_index(index)
        pool.move_entry(new_entry.id, old.priority)
        schedule = store.schedules(provider).get(old_id)
        store.forget(provider, old_id)
        if schedule:
            store.set_schedule(provider, new_entry.id, schedule["label"], schedule["every"])


@router.post("/signin/step")
def signin_step(body: dict):
    """Send the pasted code, or check whether a device sign-in finished."""
    _expire_flows()
    flow_id = str((body or {}).get("flow") or "")
    flow = FLOWS.get(flow_id)
    if flow is None:
        return {"status": "error", "final": True, "message": "This sign-in expired or was cancelled. Start again."}
    if not flow["lock"].acquire(blocking=False):
        return {"status": "pending"}
    try:
        relay, code = flow["relay"], str((body or {}).get("code") or "")
        if flow["info"]["kind"] == "code" and relay.returncode() is None:
            try:
                relay.send(relay_mod.check_code(flow["info"]["url"], code))
            except ValueError as error:
                return {"status": "error", "final": False, "message": str(error)}
            result = relay.returncode(wait=90)
        else:
            result = relay.returncode()
        if result is None:
            if flow["info"]["kind"] == "code":
                _drop_flow(flow_id, remove_new=True)
                return {"status": "error", "final": True, "message": "Sign-in did not finish after the code. Start again."}
            return {"status": "pending"}
        FLOWS.pop(flow_id, None)
        relay.close()
    finally:
        flow["lock"].release()
    if result != 0:
        _drop_new(flow)
        return {"status": "error", "final": True,
                "message": f"Sign-in failed: {relay.reason() or f'exit code {result}'}. Start again."}
    added = [e for e in _pool(flow["provider"]).entries() if e.id not in flow["before"]]
    if not added:
        return {"status": "error", "final": True, "message": "Sign-in finished, but no new account is in the pool."}
    if flow.get("replace"):
        _take_place(flow["provider"], flow["replace"], added[0])
    _log(flow["provider"], "relogin" if flow.get("replace") else "add",
         f"{'Signed in again' if flow.get('replace') else 'Added'} {added[0].label}")
    LIVE.pop(flow["provider"], None)
    return {"status": "done", "account": added[0].label}


@router.post("/signin/cancel")
def signin_cancel(body: dict):
    _drop_flow(str((body or {}).get("flow") or ""), remove_new=True)
    return {"ok": True}


def _ensure_typesafe_provider():
    """Create Hermes' `providers.jev` (TypeSafe) once, so its API keys have a pool. The user asked for this."""
    from hermes_cli.auth_commands import _configured_provider_entry
    from hermes_cli.config import set_config_value

    if _configured_provider_entry("jev") is None:
        set_config_value("providers.jev.name", "TypeSafe")
        set_config_value("providers.jev.base_url", typesafe.BASE_URL)


@router.post("/pool/add-key")
def pool_add_key(body: dict):
    """Add a Type-safe API key to Hermes' pool, or replace one ("replace": entry id) in place.

    The key is checked with TypeSafe first (free GET /v1/models) and is never returned or logged."""
    provider = _provider(body)
    if provider not in contract.KEY_PROVIDERS:
        raise HTTPException(400, "This provider signs in through its website: use Add account.")
    key = str((body or {}).get("key") or "").strip()
    if not key or any(c.isspace() for c in key):
        raise HTTPException(400, "Paste the whole API key.")
    _ensure_typesafe_provider()
    from agent.credential_pool import PooledCredential
    from hermes_cli.auth_commands import AUTH_TYPE_API_KEY, SOURCE_MANUAL, _provider_base_url

    pool = _pool(provider)
    replace_id = str((body or {}).get("replace") or "")
    old = next((e for e in pool.entries() if e.id == replace_id), None) if replace_id else None
    if replace_id and old is None:
        raise HTTPException(400, "That key is not in the pool.")
    label = (old.label or old.id) if old else str((body or {}).get("label") or "").strip()
    if not label:
        raise HTTPException(400, "Enter a name for the key.")
    if not old and any((e.label or "").lower() == label.lower() for e in pool.entries()):
        raise HTTPException(400, "A key with this name is already in the pool.")
    base_url = _provider_base_url(provider) or typesafe.BASE_URL
    result = typesafe.check(key, base_url)
    if not typesafe.key_accepted(result):
        raise HTTPException(400 if result["status"] else 502,
                            f"TypeSafe did not accept the key ({result['problem']}). Nothing was saved.")
    import uuid
    with POOL_LOCK:
        entry = pool.add_entry(PooledCredential(provider=provider, id=uuid.uuid4().hex[:6], label=label,
                                                auth_type=AUTH_TYPE_API_KEY, priority=0, source=SOURCE_MANUAL,
                                                access_token=key, base_url=base_url))
    if old:
        _take_place(provider, old.id, entry)
    LIVE.setdefault(provider, {"read_at": time.time(), "accounts": {}})["accounts"][entry.id] = {
        "peak": None, "windows": [], "check": result}
    _log(provider, "relogin" if old else "add", f"{'Replaced the key of' if old else 'Added key'} {label}")
    return {"account": label, "models": result["models"], "replaced": bool(old)}


_ENGINE = []
ROUTER_PROVIDERS = ("anthropic", "openai-codex")


def _engine():
    if not _ENGINE:
        _ENGINE.append(router_engine.Engine(_home(), lambda: _pool("jev").entries()))
    return _ENGINE[0]


def _models(provider):
    try:
        from hermes_cli.models import curated_models_for_provider
        return [m[0] if isinstance(m, (tuple, list)) else str(m) for m in curated_models_for_provider(provider)][:40]
    except Exception:  # noqa: BLE001
        return []


@router.get("/router")
def get_router():
    """Router state for the Desktop page: settings, Jev key state, load, learning stats, recent picks."""
    engine = _engine()
    status = engine.status()
    accounts = {}
    for provider in ROUTER_PROVIDERS:
        readings = (LIVE.get(provider) or {}).get("accounts", {})
        try:
            entries = _pool(provider).entries()
        except Exception:  # noqa: BLE001
            entries = []
        accounts[provider] = [{"id": e.id, "label": e.label or e.id, "peak": (readings.get(e.id) or {}).get("peak"),
                               "plan": (readings.get(e.id) or {}).get("plan"),
                               "weight": (readings.get(e.id) or {}).get("weight")}
                              for e in entries if getattr(e, "last_status", None) != "dead"]
    return {**status, "accounts": accounts, "models": {p: _models(p) for p in ROUTER_PROVIDERS}}


@router.get("/routing/live")
def get_live_routing():
    return {"calls": _engine().store.live()}


@router.get("/tokens/total")
def get_running_total():
    return _engine().store.total()


@router.post("/tokens/total/reset")
def reset_running_total():
    _log("anthropic", "schedule", "Running token total reset")
    return _engine().store.reset_total()


@router.get("/delegation")
def get_delegation():
    rules = delegation.settings(_home())
    saved = {job["provider"] for job in rules["jobs"]}
    usable = [p for p in delegation.PROVIDERS
              if p in delegation.SUBSCRIPTIONS or p in saved or api_keys.hermes_status(p)["configured"]]
    return {"settings": rules,
            "latest": delegation.read(_home(), "harp-delegation-latest.json", None),
            "models": {provider: _models(provider) for provider in usable},
            "labels": {provider: api_keys.LABEL.get(provider, provider) for provider in usable}}


@router.get("/api-keys")
def get_api_keys():
    return api_keys.overview(_home())


@router.post("/api-keys/key")
def add_api_key(body: dict):
    # The key is passed on to Hermes and never stored, logged or echoed back.
    try:
        status = api_keys.add_key(str(body.get("provider", "")), body.get("key"))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    except Exception:  # noqa: BLE001 - never echo Hermes' error text: it can contain the key
        raise HTTPException(500, "Hermes could not store that key. Check the Hermes log.") from None
    return {"provider": body.get("provider"), **status}


@router.post("/api-keys/key/remove")
def remove_api_key(body: dict):
    try:
        status = api_keys.remove_key(str(body.get("provider", "")))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    return {"provider": body.get("provider"), **status}


@router.post("/api-keys")
def save_api_keys(body: dict):
    try:
        api_keys.save(_home(), body)
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    return api_keys.overview(_home())


@router.post("/delegation")
def save_delegation(body: dict):
    try:
        settings = delegation.validate(body)
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    delegation.write(_home(), "harp-delegation.json", settings)
    return {"settings": settings}


def _hermes_cli(args, timeout=30):
    launcher = shutil.which("hermes")
    command = [launcher, *args] if launcher else [sys.executable, "-m", "hermes_cli.main", *args]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=_hermes_env(), stdin=subprocess.DEVNULL)


@router.get("/compression")
def get_compression():
    """Auto-compress setting, read through `hermes config get` (the same resolver Hermes uses)."""
    values = {}
    for key in ("compression.enabled", "compression.threshold"):
        try:
            result = _hermes_cli(["config", "get", key], timeout=15)
            values[key] = result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            values[key] = ""
    enabled = values["compression.enabled"].lower() != "false"
    try:
        threshold = float(values["compression.threshold"])
    except ValueError:
        threshold = None
    return compression.parse_settings({"compression": {"enabled": enabled, "threshold": threshold}})


@router.post("/compression")
def set_compression(body: dict):
    try:
        writes = compression.settings_change(body)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    for key, value in writes:
        result = _hermes_cli(["config", "set", key, value])
        if result.returncode:
            raise HTTPException(500, "Could not save auto-compress: " + (result.stderr or result.stdout).strip()[:200])
    return get_compression()


@router.post("/router/settings")
def save_router(body: dict):
    allowed = set(router_engine.router.DEFAULTS) - {"anthropic_display_plans"}
    changes = {k: v for k, v in (body or {}).items() if k in allowed}
    try:
        settings = _engine().save(changes)
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error))
    _log("anthropic", "schedule", f"Router settings changed: {', '.join(sorted(changes)) or 'nothing'}")
    return {"settings": settings}


@router.post("/router/try")
def try_router(body: dict):
    """Decide for a sample message without recording it. use_jev=true spends one Jev call."""
    text = str((body or {}).get("text") or "")[:2000]
    decision = _engine().decide(text, log=False, use_jev=bool((body or {}).get("use_jev")))
    return {"decision": decision}


@router.post("/router/feedback")
def router_feedback(body: dict):
    try:
        correct = _engine().feedback(int((body or {}).get("id")), str((body or {}).get("label") or ""))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Unknown decision.")
    return {"learned": correct}


@router.post("/router/verify")
def router_verify(body: dict):
    try:
        return {"learned": _engine().verify(int(body.get("id")))}
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Unknown decision.")


@router.post("/router/check-key")
def router_check_key(body: dict | None = None):
    return {"jev": _engine().jev_status(force=True)}


@router.get("/report")
def get_report(provider: str, hours: float = 24):
    if provider not in {"anthropic", "jev", "openai-codex"}:
        raise HTTPException(400, "Unknown HARP provider")
    if not 0 < hours <= 7 * 24:
        raise HTTPException(400, "hours must be between 0 and 168.")
    return pool_state.report(_db(), provider, hours)


@router.get("/activity")
def get_activity(provider: str, hours: float = 24):
    if provider not in {"anthropic", "jev", "openai-codex"}:
        raise HTTPException(400, "Unknown HARP provider")
    if not 0 < hours <= 30 * 24:
        raise HTTPException(400, "hours must be between 0 and 720.")
    return {"hours": hours, "events": pool_state.read_events(_db(), provider, time.time() - hours * 3600)}


@router.get("/activity/heat")
def get_activity_heat(span: str = "week", tz: str = "UTC"):
    """Tokens-by-hour grid across every configured provider, for the Summary tab."""
    days = {"day": 1, "week": 7, "month": 30}.get(span)
    if days is None:
        raise HTTPException(400, "span must be day, week or month.")
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        if tz == "Asia/Bangkok":
            zone = timezone(timedelta(hours=7))
        else:
            tz, zone = "UTC", timezone.utc
    return {"span": span, "timezone": tz,
            "days": pool_state.combined_chart(_db(), list(ROUTER_PROVIDERS) + ["jev"], days, zone=zone)}


@router.get("/health")
def get_health(provider: str):
    """Checks from local state only: no provider call."""
    if provider not in {"anthropic", "jev", "openai-codex"}:
        raise HTTPException(400, "Unknown HARP provider")
    store = _store()
    pool_key = provider if provider != "jev" or _pool("jev").entries() else "custom:jev"
    entries = _pool(pool_key).entries()
    live = LIVE.get(provider) or {}
    facts = {
        "now": time.time(), "timer_needed": pool_state.timer_needed(store), "timer_installed": timer.installed(),
        "last_tick": store.last_tick(), "tracker_last": pool_state.last_seen(_db()),
        "account_pool_timer": timer.task_exists("Hermes Account Balance"),
        "live_read_at": live.get("read_at"), "live_ids": set((live.get("accounts") or {}).keys()),
        "can_read": provider in contract.PROVIDERS,
        "accounts": [{"id": e.id, "label": e.label or e.id, "status": getattr(e, "last_status", None),
                      "reason": getattr(e, "last_error_reason", None), "message": getattr(e, "last_error_message", None),
                      "reset_at": getattr(e, "last_error_reset_at", None)} for e in entries],
    }
    active = store.alert_state().get(provider, {})
    return {"checks": pool_state.health_checks(facts), "timer": {"needed": facts["timer_needed"],
            "installed": facts["timer_installed"], "last_tick": facts["last_tick"]},
            "live_read_at": facts["live_read_at"], "alerts": len(active)}


@router.get("/alerts")
def get_alerts(provider: str):
    store = _store()
    active = store.alert_state().get(provider, {})
    return {"settings": store.alerts(), "timer_installed": timer.installed(),
            "active": sorted(({"key": k, **v} for k, v in active.items()), key=lambda a: a["since"])}


@router.post("/alerts")
def set_alerts(body: dict):
    changes = {k: v for k, v in (body or {}).items() if k in pool_state.ALERT_DEFAULTS}
    try:
        settings = _store().set_alerts(changes)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    for provider in pool_state.TIMER_PROVIDERS:
        _log(provider, "alert", "Alert settings: " + ", ".join(f"{k} {v}" for k, v in changes.items()))
    try:
        _sync_timer()
    except RuntimeError as error:
        return {"settings": settings, "warning": str(error)}
    return {"settings": settings}


@router.post("/alerts/test")
def test_alert(body: dict):
    settings = _store().alerts()
    if not (settings["desktop"] or settings["telegram"]):
        raise HTTPException(400, "Turn on desktop notifications or Telegram first.")
    sent = timer.sender(settings)("HARP: test alert", "Alerts from HARP arrive here.")
    return {"sent": sent}


@router.get("/usage")
def get_usage():
    day = datetime.now(timezone.utc).date().isoformat()
    result = {"configured": False, "day": day, "accounts": [], "usage": []}
    config_path = os.environ.get("HARP_CONFIG")
    if not config_path:
        return result
    path = Path(config_path).resolve()
    try:
        with path.open(encoding="utf-8-sig") as config_file:
            config = json.load(config_file)
        quotas = config.get("daily_token_quotas", {})
        if not isinstance(quotas, dict) or any(
            not account or account.strip() != account or account == "Unassigned"
            or type(limit) is not int or limit <= 0
            for account, limit in quotas.items()
        ):
            raise ValueError("invalid daily_token_quotas")
    except (OSError, ValueError, AttributeError) as error:
        raise HTTPException(503, "HARP quota configuration is invalid or unreadable") from error

    result["configured"] = bool(quotas)
    database = path.with_suffix(".usage.sqlite3")
    if quotas and database.is_file():
        try:
            with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute("""
                    SELECT account, tag, provider, model, tokens, requests, unmetered
                    FROM daily_usage WHERE day = ?
                    ORDER BY account, tokens DESC, tag, provider, model
                """, (day,))
                result["usage"] = [dict(row) for row in rows]
        except sqlite3.Error as error:
            raise HTTPException(503, "HARP usage storage is unavailable") from error

    accounts = {
        account: {"account": account, "quota_tokens": limit,
                  "tokens": 0, "requests": 0, "unmetered": 0}
        for account, limit in sorted(quotas.items())
    }
    for row in result["usage"]:
        account = row["account"]
        totals = accounts.setdefault(account, {
            "account": account, "quota_tokens": None,
            "tokens": 0, "requests": 0, "unmetered": 0,
        })
        for field in ("tokens", "requests", "unmetered"):
            totals[field] += row[field]
    result["accounts"] = list(accounts.values())
    return result
