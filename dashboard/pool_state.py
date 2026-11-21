"""HARP account state: parks, weekly park schedules and per-account activity.

Shared by the Dashboard API (plugin_api.py) and the agent plugin (__init__.py: the
post_api_request tracker and `hermes harp tick`). Holds labels, ids, times and token
counts only, never credentials.
"""

import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

PARK_REASON = "harp-parked"
FOREVER = 3650 * 86400          # a park without an end time: held ten years, until unparked
ACTIVE_MINUTES = 30
BUCKET = 300
KEEP = 8 * 86400
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_SPEC = re.compile(r"^\s*([a-z,\-]+)\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


# ── schedules ────────────────────────────────────────────────────────────────

def parse_schedule(spec):
    """'mon-fri 09:00-17:00' -> ({0..4}, 540, 1020). End before start means the window ends next day."""
    match = _SPEC.match(str(spec or "").lower())
    if not match:
        raise ValueError('Use days and hours, such as "mon-fri 09:00-17:00".')
    days = set()
    for part in match.group(1).split(","):
        first, _, last = part.partition("-")
        if first not in DAYS or (last and last not in DAYS):
            raise ValueError(f'Unknown day "{part}". Use mon, tue, wed, thu, fri, sat, sun.')
        a, b = DAYS.index(first), DAYS.index(last or first)
        days.update(DAYS[(a + i) % 7] for i in range((b - a) % 7 + 1))
    h1, m1, h2, m2 = (int(match.group(i)) for i in range(2, 6))
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        raise ValueError("Hours run from 00:00 to 23:59.")
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if start == end:
        raise ValueError("The start and end times must differ.")
    return {DAYS.index(d) for d in days}, start, end


def window_end(spec, now):
    """End of the schedule window that contains `now` (local time), or None outside every window."""
    days, start, end = parse_schedule(spec)
    for back in (0, 1):
        day = (now - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
        if day.weekday() not in days:
            continue
        opens = day + timedelta(minutes=start)
        closes = day + timedelta(minutes=end, days=1 if end < start else 0)
        if opens <= now < closes:
            return closes
    return None


# ── parks and schedules on disk ──────────────────────────────────────────────

class Store:
    """harp-pool.json in the Hermes home: {"parked": {provider: {id: rec}}, "schedules": {...}}."""

    def __init__(self, home):
        self.path = Path(home) / "harp-pool.json"

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {"parked": data.get("parked") or {}, "schedules": data.get("schedules") or {}}

    def _save(self, data):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _edit(self, kind, provider, entry_id, value):
        data = self._load()
        group = data[kind].setdefault(provider, {})
        if value is None:
            group.pop(entry_id, None)
        else:
            group[entry_id] = value
        if not group:
            data[kind].pop(provider, None)
        self._save(data)

    def parked(self, provider):
        return dict(self._load()["parked"].get(provider) or {})

    def schedules(self, provider=None):
        all_schedules = self._load()["schedules"]
        return dict(all_schedules.get(provider) or {}) if provider else all_schedules

    def save_park(self, provider, entry_id, record):
        self._edit("parked", provider, entry_id, record)

    def drop_park(self, provider, entry_id):
        self._edit("parked", provider, entry_id, None)

    def set_schedule(self, provider, entry_id, label, every):
        parse_schedule(every)
        self._edit("schedules", provider, entry_id, {"label": label, "every": every})

    def drop_schedule(self, provider, entry_id):
        self._edit("schedules", provider, entry_id, None)

    def forget(self, provider, entry_id):
        self.drop_park(provider, entry_id)
        self.drop_schedule(provider, entry_id)


def park(store, pool, provider, entry_id, until, by, now=None):
    """Hold a pool entry out of rotation with Hermes' own pool API until `until` (epoch) or unparked."""
    entry = next((e for e in pool.entries() if e.id == entry_id), None)
    if entry is None:
        raise ValueError("That account is not in the pool.")
    now = time.time() if now is None else now
    pool.mark_exhausted_and_rotate(status_code=None, credential_id=entry_id, error_context={
        "reason": PARK_REASON, "message": f"parked by HARP ({by})", "reset_at": until or now + FOREVER})
    store.save_park(provider, entry_id, {"label": entry.label or entry_id, "until": until, "by": by, "at": now})


def unpark(store, pool, provider, entry_id):
    if any(e.id == entry_id for e in pool.entries()):
        pool.reset_status(entry_id)
    store.drop_park(provider, entry_id)


def tick(store, pool_for, now):
    """Apply schedules and clear finished parks. Returns what changed, one line per change."""
    changes = []
    providers = set(store.schedules()) | set(store._load()["parked"])
    for provider in sorted(providers):
        pool = pool_for(provider)
        ids = {e.id for e in pool.entries()}
        for entry_id, rec in store.parked(provider).items():
            if entry_id not in ids:
                store.drop_park(provider, entry_id)
            elif rec.get("until") and rec["until"] <= now.timestamp():
                unpark(store, pool, provider, entry_id)
                changes.append(f"{provider}: {rec.get('label') or entry_id} back in rotation")
        parked = store.parked(provider)
        for entry_id, sched in store.schedules(provider).items():
            if entry_id not in ids:
                store.drop_schedule(provider, entry_id)
                continue
            closes = window_end(sched["every"], now)
            rec = parked.get(entry_id)
            if closes and rec is None:
                park(store, pool, provider, entry_id, closes.timestamp(), "schedule", now.timestamp())
                changes.append(f"{provider}: parked {sched['label']} by schedule")
            elif not closes and rec and rec.get("by") == "schedule":
                unpark(store, pool, provider, entry_id)
                changes.append(f"{provider}: {sched['label']} back in rotation")
    return changes


# ── per-account activity (post_api_request tracker) ──────────────────────────

def _open(db):
    con = sqlite3.connect(db, timeout=5)
    con.execute("CREATE TABLE IF NOT EXISTS tokens (provider TEXT, label TEXT, bucket INTEGER, tokens INTEGER,"
                " PRIMARY KEY (provider, label, bucket))")
    con.execute("CREATE TABLE IF NOT EXISTS sessions (provider TEXT, session TEXT, label TEXT, platform TEXT,"
                " last_seen REAL, PRIMARY KEY (provider, session))")
    return con


def record_call(db, provider, label, session, platform, tokens, now=None):
    now = time.time() if now is None else now
    with closing(_open(db)) as con, con:
        if tokens:
            con.execute("INSERT INTO tokens VALUES (?, ?, ?, ?) ON CONFLICT (provider, label, bucket)"
                        " DO UPDATE SET tokens = tokens + excluded.tokens",
                        (provider, label, int(now // BUCKET) * BUCKET, int(tokens)))
        if session:
            con.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?) ON CONFLICT (provider, session)"
                        " DO UPDATE SET label = excluded.label, platform = excluded.platform,"
                        " last_seen = excluded.last_seen", (provider, session, label, platform or "?", now))
        con.execute("DELETE FROM tokens WHERE bucket < ?", (now - KEEP,))
        con.execute("DELETE FROM sessions WHERE last_seen < ?", (now - KEEP,))


def account_activity(db, provider, now=None):
    """{label: {"tokens_24h": n, "used_by": [platform, ...]}} from the tracker database."""
    now = time.time() if now is None else now
    if not Path(db).is_file():
        return {}
    out = {}
    with closing(sqlite3.connect(f"{Path(db).as_uri()}?mode=ro", uri=True, timeout=5)) as con:
        for label, total in con.execute("SELECT label, SUM(tokens) FROM tokens WHERE provider = ? AND bucket >= ?"
                                        " GROUP BY label", (provider, now - 86400)):
            out.setdefault(label, {"tokens_24h": 0, "used_by": []})["tokens_24h"] = total
        rows = con.execute("SELECT label, platform FROM sessions WHERE provider = ? AND last_seen >= ?"
                           " ORDER BY platform", (provider, now - ACTIVE_MINUTES * 60))
        for label, platform in rows:
            used = out.setdefault(label, {"tokens_24h": 0, "used_by": []})["used_by"]
            if platform not in used:
                used.append(platform)
    return out
