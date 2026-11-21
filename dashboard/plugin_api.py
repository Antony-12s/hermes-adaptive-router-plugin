"""Authenticated Hermes Dashboard endpoint for HARP's local usage totals."""

import json
import math
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

@router.get("/accounts")
def get_accounts(provider: str):
    """Read account metadata and the active profile's quota from Hermes."""
    if provider not in {"anthropic", "jev", "openai-codex"}:
        raise HTTPException(400, "Unknown HARP provider")

    from hermes_cli.auth import read_credential_pool

    entries = read_credential_pool(provider)
    if provider == "jev" and not entries:
        entries = read_credential_pool("custom:jev")
    accounts = [
        {"label": entry.get("label") or "Unnamed account",
         "status": entry.get("last_status") or "No status recorded"}
        for entry in entries if isinstance(entry, dict)
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
    return {"accounts": accounts, "windows": windows}


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
