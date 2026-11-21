"""Authenticated Hermes Dashboard endpoints for HARP accounts, actions and usage."""

import importlib.util
import json
import math
import os
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
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


def _read_usage(provider, entry):
    from agent.account_usage import fetch_account_usage
    from agent.account_usage_cache import _identity_id_for

    return fetch_account_usage(provider, base_url=entry.runtime_base_url, api_key=entry.runtime_api_key,
                               read_only=True, identity_id=_identity_id_for(provider, entry))


def _reading(snapshot):
    peak = actions.account_peak(snapshot)
    return {
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
        snapshots[entry.id] = _read_usage(provider, entry)
        readings[entry.id] = _reading(snapshots[entry.id])
    LIVE[provider] = {"read_at": time.time(), "accounts": readings}
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
    accounts = [
        {"id": entry.get("id"), "priority": entry.get("priority"),
         "label": entry.get("label") or "Unnamed account",
         "status": entry.get("last_status") or "No status recorded",
         "usage": readings.get(entry.get("id"))}
        for entry in sorted((e for e in entries if isinstance(e, dict)),
                            key=lambda e: e.get("priority") if isinstance(e.get("priority"), int) else 10**6)
    ]

    windows = []
    if provider in {"anthropic", "openai-codex"}:
        from agent.account_usage import fetch_account_usage

        snapshot = fetch_account_usage(provider, read_only=True)
        if snapshot:
            for window in snapshot.windows:
                percent = window.used_percent
                windows.append({
                    "label": window.label,
                    "used_percent": percent if percent is not None and math.isfinite(percent) else None,
                    "reset_at": window.reset_at.isoformat() if window.reset_at else None,
                })
    return {"accounts": accounts, "windows": windows, "live_read_at": live.get("read_at")}


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
    with POOL_LOCK:
        pool = _pool(provider)
        snapshots = _read_all(provider, pool)
        rows = [actions.Row(e.id, e.label or e.id, e.priority, getattr(e, "last_status", None),
                            actions.account_peak(snapshots.get(e.id)), actions.benched_by_harp(e))
                for e in pool.entries()]
        plan = actions.plan_balance(rows)
        if plan is None:
            return {"changed": False, "message": "No account usage could be read; nothing changed."}
        actions.apply_balance(pool, plan)
    label = {row.id: row.label for row in rows}
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
    """Start Hermes' own `hermes auth add` and return the sign-in link or device code."""
    _expire_flows()
    provider = _provider(body)
    label = str((body or {}).get("label") or "").strip()
    if not label:
        raise HTTPException(400, "Enter a name for the account, such as its email.")
    pool = _pool(provider)
    if any((e.label or "").lower() == label.lower() for e in pool.entries()):
        raise HTTPException(400, "An account with this name is already in the pool.")
    relay = relay_mod.Relay(_hermes_command(provider, label), _hermes_env())
    info = relay.wait_for(relay_mod.parse, 90)
    if not info:
        reason = relay.reason() or "no sign-in link was printed"
        relay.close()
        raise HTTPException(502, f"Could not start the sign-in: {reason}")
    flow_id = secrets.token_urlsafe(16)
    FLOWS[flow_id] = {"relay": relay, "info": info, "provider": provider, "label": label,
                      "before": {e.id for e in pool.entries()}, "created": time.time(), "lock": threading.Lock()}
    return {"flow": flow_id, **info, "label": label, "expires_in": SIGNIN_TTL}


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
    LIVE.pop(flow["provider"], None)
    return {"status": "done", "account": added[0].label}


@router.post("/signin/cancel")
def signin_cancel(body: dict):
    _drop_flow(str((body or {}).get("flow") or ""), remove_new=True)
    return {"ok": True}


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
