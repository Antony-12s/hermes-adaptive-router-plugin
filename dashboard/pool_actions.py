"""HARP-owned account operations on the Hermes credential pool.

Pure planning lives here so it can be tested without Hermes; plugin_api.py wires
it to the real pool, usage fetcher and Anthropic client.
"""

import math
import time
from dataclasses import dataclass
from typing import Optional

BENCH_MARK = "[HARP balance]"
THRESHOLD = 90.0
SESSION_LABELS = ("current session", "session", "5h")


@dataclass(frozen=True)
class Row:
    id: str
    label: str
    priority: int
    status: Optional[str]
    peak: Optional[tuple]
    benched: bool


def account_peak(snapshot):
    """Highest account-scoped usage percent and its reset epoch, or None without a reading."""
    windows = [
        w for w in getattr(snapshot, "windows", None) or []
        if getattr(w, "scope", "account") == "account"
        and isinstance(w.used_percent, (int, float)) and math.isfinite(w.used_percent)
    ]
    if not windows:
        return None
    top = max(windows, key=lambda w: w.used_percent)
    return top.used_percent, top.reset_at.timestamp() if top.reset_at else None


def fresh_entry(pool, entry):
    """An idle OAuth account's access token expires, and a read-only usage read then returns nothing
    ("No reading"), which also stops auto-balance from releasing it. Renew the token through Hermes'
    own pool refresh (cross-process locked, persisted) before reading. Private Hermes API, so any
    failure or a Hermes without it keeps the entry as it is."""
    try:
        if not pool._entry_needs_refresh(entry):
            return entry
        fresh = pool._refresh_entry(entry, force=False) or entry
        if getattr(entry, "last_status", None) == "exhausted" and getattr(fresh, "last_status", None) != "exhausted":
            # Hermes clears a credential's status when it renews the token: put the bench back as it was,
            # or an account still at 100% would rejoin the rotation until its window resets.
            pool.mark_exhausted_and_rotate(status_code=429, credential_id=entry.id, error_context={
                "reason": getattr(entry, "last_error_reason", None) or "rate_limit_error",
                "message": getattr(entry, "last_error_message", None) or "",
                "reset_at": getattr(entry, "last_error_reset_at", None) or time.time() + 3600,
            })
            fresh = next((e for e in pool.entries() if e.id == entry.id), fresh)
        return fresh
    except Exception:  # noqa: BLE001 - a read must never fail on a refresh
        return entry


def benched_by_harp(entry):
    return getattr(entry, "last_status", None) == "exhausted" and BENCH_MARK in (
        getattr(entry, "last_error_message", None) or "")


def plan_balance(rows, threshold=THRESHOLD):
    """Bench accounts at the threshold, release HARP benches below it, order by usage.

    Returns None when no account has a reading, so nothing changes on a blind run.
    """
    known = [r for r in rows if r.peak is not None and r.status not in ("dead", "parked")]
    if not known:
        return None
    full = [r for r in known if r.peak[0] >= threshold]
    if len(full) == len(known):  # never bench the whole pool for being nearly full
        full = [r for r in known if r.peak[0] >= 100]
    full_ids = {r.id for r in full}

    def rank(row):
        if row.status == "dead":
            return 1000
        if row.status == "parked":
            return 900
        if row.peak is None:
            return 500
        return row.peak[0] + (200 if row.id in full_ids else 0)

    ordered = sorted(sorted(rows, key=lambda r: r.priority), key=rank)
    return {
        "bench": [r.id for r in full],
        "release": [r.id for r in known if r.id not in full_ids and r.benched],
        "order": [r.id for r in ordered],
        "peaks": {r.id: r.peak for r in known},
    }


def apply_balance(pool, plan, threshold=THRESHOLD):
    for credential_id in plan["bench"]:
        percent, reset = plan["peaks"][credential_id]
        pool.mark_exhausted_and_rotate(status_code=429, credential_id=credential_id, error_context={
            "reason": "rate_limit_error",
            "message": f"{BENCH_MARK} usage {percent:.0f}% >= {threshold:.0f}%",
            "reset_at": reset or time.time() + 3600,
        })
    for credential_id in plan["release"]:
        pool.reset_status(credential_id)
    current = [e.id for e in sorted(pool.entries(), key=lambda e: e.priority)]
    if current != plan["order"]:
        for position, credential_id in enumerate(plan["order"]):
            pool.move_entry(credential_id, position)


def wake_ready(snapshot):
    """True only when a reading shows the session window exists and is not running."""
    for w in getattr(snapshot, "windows", None) or []:
        if str(w.label).lower() in SESSION_LABELS:
            return w.reset_at is None
    return False
