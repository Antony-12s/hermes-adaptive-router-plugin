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
        return {"parked": data.get("parked") or {}, "schedules": data.get("schedules") or {},
                "alerts": data.get("alerts") or {}, "alert_state": data.get("alert_state") or {},
                "last_tick": data.get("last_tick")}

    def _put(self, key, value):
        data = self._load()
        data[key] = value
        self._save(data)

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

    def alerts(self):
        return {**ALERT_DEFAULTS, **self._load()["alerts"]}

    def set_alerts(self, changes):
        merged = {**self.alerts(), **changes}
        if set(merged) != set(ALERT_DEFAULTS) or not all(type(merged[k]) is bool for k in ("desktop", "telegram")) \
                or type(merged["threshold"]) not in (int, float) or not 50 <= merged["threshold"] <= 100:
            raise ValueError("Alerts: desktop and telegram are on or off; the threshold is 50 to 100%.")
        self._put("alerts", merged)
        return merged

    def alert_state(self):
        return self._load()["alert_state"]

    def set_alert_state(self, provider, active):
        data = self._load()["alert_state"]
        data[provider] = active
        self._put("alert_state", {k: v for k, v in data.items() if v})

    def last_tick(self):
        return self._load()["last_tick"]


ALERT_DEFAULTS = {"desktop": False, "telegram": False, "threshold": 90}
TIMER_PROVIDERS = ("anthropic", "openai-codex")


def timer_needed(store):
    settings = store.alerts()
    return bool(store.schedules()) or settings["desktop"] or settings["telegram"]


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
# calls: one row per (account, session, source, model, 5-minute bucket). The older `tokens`
# table (tracker before calls existed) is still read so nothing counted earlier is lost.

EVENT_KEEP = 30 * 86400


def _open(db):
    con = sqlite3.connect(db, timeout=5)
    con.execute("CREATE TABLE IF NOT EXISTS tokens (provider TEXT, label TEXT, bucket INTEGER, tokens INTEGER,"
                " PRIMARY KEY (provider, label, bucket))")
    con.execute("CREATE TABLE IF NOT EXISTS sessions (provider TEXT, session TEXT, label TEXT, platform TEXT,"
                " last_seen REAL, PRIMARY KEY (provider, session))")
    con.execute("CREATE TABLE IF NOT EXISTS calls (provider TEXT, label TEXT, session TEXT, platform TEXT, model TEXT,"
                " bucket INTEGER, tokens INTEGER, requests INTEGER,"
                " PRIMARY KEY (provider, label, session, platform, model, bucket))")
    con.execute("CREATE TABLE IF NOT EXISTS events (ts REAL, provider TEXT, kind TEXT, by TEXT, text TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS snapshots (ts REAL, provider TEXT, label TEXT, pct REAL)")
    return con


def _read(db):
    return closing(sqlite3.connect(f"{Path(db).as_uri()}?mode=ro", uri=True, timeout=5))


def record_call(db, provider, label, session, platform, tokens, now=None, model=None):
    now = time.time() if now is None else now
    with closing(_open(db)) as con, con:
        con.execute("INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, 1) ON CONFLICT"
                    " (provider, label, session, platform, model, bucket)"
                    " DO UPDATE SET tokens = tokens + excluded.tokens, requests = requests + 1",
                    (provider, label, session or "?", platform or "?", model or "?",
                     int(now // BUCKET) * BUCKET, int(tokens or 0)))
        if session:
            con.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?) ON CONFLICT (provider, session)"
                        " DO UPDATE SET label = excluded.label, platform = excluded.platform,"
                        " last_seen = excluded.last_seen", (provider, session, label, platform or "?", now))
        con.execute("DELETE FROM tokens WHERE bucket < ?", (now - KEEP,))
        con.execute("DELETE FROM calls WHERE bucket < ?", (now - KEEP,))
        con.execute("DELETE FROM sessions WHERE last_seen < ?", (now - KEEP,))


def account_activity(db, provider, now=None):
    """{label: {"tokens_24h": n, "used_by": [platform, ...]}} from the tracker database."""
    now = time.time() if now is None else now
    if not Path(db).is_file():
        return {}
    out = {}
    with closing(_open(db)), _read(db) as con:
        for label, total in con.execute(
                "SELECT label, SUM(tokens) FROM (SELECT label, tokens FROM tokens WHERE provider = ? AND bucket >= ?"
                " UNION ALL SELECT label, tokens FROM calls WHERE provider = ? AND bucket >= ?) GROUP BY label",
                (provider, now - 86400, provider, now - 86400)):
            if total:
                out.setdefault(label, {"tokens_24h": 0, "used_by": []})["tokens_24h"] = total
        rows = con.execute("SELECT label, platform FROM sessions WHERE provider = ? AND last_seen >= ?"
                           " ORDER BY platform", (provider, now - ACTIVE_MINUTES * 60))
        for label, platform in rows:
            used = out.setdefault(label, {"tokens_24h": 0, "used_by": []})["used_by"]
            if platform not in used:
                used.append(platform)
    return out


def last_seen(db):
    """When the tracker last counted a call, or None."""
    if not Path(db).is_file():
        return None
    with closing(_open(db)), _read(db) as con:
        return con.execute("SELECT MAX(last_seen) FROM sessions").fetchone()[0]


# ── report ───────────────────────────────────────────────────────────────────

def _groups(rows, total):
    out = [{"name": name or "?", "tokens": tokens or 0, "requests": requests or 0,
            "share": round((tokens or 0) / total * 100, 1) if total else 0.0} for name, tokens, requests in rows]
    return sorted(out, key=lambda r: (-r["tokens"], -r["requests"], r["name"]))


def report(db, provider, hours, now=None):
    """Tokens and requests by account, model, source and session for the last `hours`, a 7-day heatmap,
    and each account's peak usage and minutes at 90% or more from the timer's usage snapshots."""
    now = time.time() if now is None else now
    start = now - hours * 3600
    empty = {"hours": hours, "total": {"tokens": 0, "requests": 0}, "accounts": [], "models": [],
             "sources": [], "sessions": [], "chart": _chart([], now), "snapshots": 0}
    if not Path(db).is_file():
        return empty
    with closing(_open(db)), _read(db) as con:
        # Rows from the older tracker carry no session, source, model or request count.
        src = ("(SELECT provider, label, session, platform, model, bucket, tokens, requests FROM calls UNION ALL"
               " SELECT provider, label, '?', '?', '?', bucket, tokens, 0 FROM tokens)")
        where, args = "provider = ? AND bucket >= ?", (provider, start)
        tokens, requests = con.execute(f"SELECT SUM(tokens), SUM(requests) FROM {src} WHERE {where}", args).fetchone()
        total = tokens or 0
        by = lambda column: con.execute(  # noqa: E731
            f"SELECT {column}, SUM(tokens), SUM(requests) FROM {src} WHERE {where} GROUP BY {column}", args).fetchall()
        accounts = _groups(by("label"), total)
        models, sources = _groups(by("model"), total), _groups(by("platform"), total)
        sessions = [{"session": s, "platform": p, "accounts": a.split(","), "models": m.split(","), "tokens": t,
                     "requests": r, "first": f, "last": l + BUCKET}
                    for s, p, a, m, t, r, f, l in con.execute(
                        f"SELECT session, MAX(platform), GROUP_CONCAT(DISTINCT label), GROUP_CONCAT(DISTINCT model),"
                        f" SUM(tokens), SUM(requests), MIN(bucket), MAX(bucket) FROM calls WHERE {where}"
                        f" GROUP BY session ORDER BY SUM(tokens) DESC LIMIT 15", args)]
        week = con.execute(f"SELECT bucket, tokens, requests FROM {src} WHERE {where}",
                           (provider, now - 7 * 86400)).fetchall()
        snaps = con.execute("SELECT label, ts, pct FROM snapshots WHERE provider = ? AND ts >= ? ORDER BY ts",
                            (provider, start)).fetchall()
    by_label = {}
    for label, ts, pct in snaps:
        by_label.setdefault(label, []).append((ts, pct))
    for row in accounts:
        points = by_label.pop(row["name"], [])
        row["peak"] = max((p for _, p in points), default=None)
        row["hot_min"] = round(sum(min(b[0] - a[0], 900) for a, b in zip(points, points[1:]) if a[1] >= 90) / 60)
    return dict(empty, total={"tokens": total, "requests": requests or 0}, accounts=accounts,
                models=models, sources=sources, sessions=sessions, chart=_chart(week, now), snapshots=len(snaps))


def _chart(rows, now):
    """Seven local days (oldest first), each with 24 hourly token and request totals."""
    today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    days = [today - timedelta(days=back) for back in range(6, -1, -1)]
    chart = [{"date": d.strftime("%m-%d"), "dow": DAYS[d.weekday()].capitalize(), "tokens": [0] * 24,
              "requests": [0] * 24} for d in days]
    index = {d.date(): i for i, d in enumerate(days)}
    for bucket, tokens, requests in rows:
        moment = datetime.fromtimestamp(bucket)
        if moment.date() in index:
            chart[index[moment.date()]]["tokens"][moment.hour] += tokens or 0
            chart[index[moment.date()]]["requests"][moment.hour] += requests or 0
    return chart


def record_snapshot(db, provider, label, pct, now=None):
    now = time.time() if now is None else now
    with closing(_open(db)) as con, con:
        con.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)", (now, provider, label, pct))
        con.execute("DELETE FROM snapshots WHERE ts < ?", (now - KEEP,))


# ── activity log ─────────────────────────────────────────────────────────────

def log_event(db, provider, kind, text, by, now=None):
    now = time.time() if now is None else now
    with closing(_open(db)) as con, con:
        con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)", (now, provider, kind, by, text))
        con.execute("DELETE FROM events WHERE ts < ?", (now - EVENT_KEEP,))


def read_events(db, provider, since, limit=500):
    if not Path(db).is_file():
        return []
    with closing(_open(db)), _read(db) as con:
        return [{"ts": ts, "kind": kind, "by": by, "text": text} for ts, kind, by, text in con.execute(
            "SELECT ts, kind, by, text FROM events WHERE provider = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
            (provider, since, limit))]


# ── alerts ───────────────────────────────────────────────────────────────────

def evaluate_alerts(active, accounts, threshold, now):
    """Return (active alerts, keys raised now, keys cleared now).

    accounts: [{label, status, session, week, week_reset}]. One alert per account whose session
    reaches the threshold, one per account per week (the key carries the week's reset), one when
    an account needs to sign in again, and one when every readable account is at the threshold."""
    wanted = {}
    readable = [a for a in accounts if isinstance(a.get("session"), (int, float)) and a.get("status") != "dead"]
    for a in accounts:
        if a.get("status") == "dead":
            wanted[f"relogin:{a['label']}"] = f"{a['label']} needs to sign in again"
        if isinstance(a.get("session"), (int, float)) and a["session"] >= threshold:
            wanted[f"session:{a['label']}"] = f"{a['label']} 5-hour window at {a['session']:.0f}%"
        if isinstance(a.get("week"), (int, float)) and a["week"] >= threshold:
            wanted[f"week:{a['label']}:{a.get('week_reset')}"] = f"{a['label']} weekly limit at {a['week']:.0f}%"
    if len(readable) > 1 and all(a["session"] >= threshold for a in readable):
        wanted["all"] = f"Every account is at {threshold:.0f}% or more"
    raised = [key for key in wanted if key not in active]
    cleared = [key for key in active if key not in wanted]
    result = {key: active.get(key) or {"text": text, "since": now} for key, text in wanted.items()}
    return result, raised, cleared


def _ago(now, ts):
    minutes = max(0, int((now - ts) // 60))
    return f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} h ago"


def health_checks(f):
    """[{level: ok|warn|fail, text}] from local facts (see plugin_api.get_health). No provider call."""
    checks, now = [], f["now"]
    add = lambda level, text: checks.append({"level": level, "text": text})  # noqa: E731
    if f["timer_needed"] and not f["timer_installed"]:
        add("fail", "HARP Timer: not installed, so schedules and alerts do not run. Save a schedule or alert setting again.")
    elif f["timer_installed"]:
        last = f["last_tick"]
        if last and now - last < 15 * 60:
            add("ok", f"HARP Timer: running, last run {_ago(now, last)}")
        else:
            add("warn", "HARP Timer: installed but has not run in the last 15 minutes" + (f" (last {_ago(now, last)})" if last else ""))
    else:
        add("ok", "HARP Timer: not needed (no schedule and no alert channel)")
    last = f["tracker_last"]
    if last and now - last < 86400:
        add("ok", f"Tracker: counting tokens, last call {_ago(now, last)}")
    else:
        add("warn", "Tracker: no call counted in 24 h. Sessions started before HARP was enabled are not counted until they restart.")
    if f["account_pool_timer"]:
        add("warn", "Two balance owners: the account-pool timer \"Hermes Account Balance\" is active too and may reorder the pool against HARP.")
    labels = [a["label"].lower() for a in f["accounts"]]
    for label in sorted({l for l in labels if labels.count(l) > 1}):
        add("warn", f"Duplicate name: {label} appears {labels.count(label)} times")
    for a in f["accounts"]:
        reason, message = a.get("reason"), a.get("message") or ""
        if a["status"] == "dead":
            add("fail", f"{a['label']} needs to sign in again (use the key button)")
        elif a["status"] == "exhausted" and reason == PARK_REASON:
            add("ok", f"{a['label']}: parked")
        elif a["status"] == "exhausted" and "[HARP balance]" in message:
            add("ok", f"{a['label']}: benched by HARP balance until its window resets")
        elif a["status"] == "exhausted":
            until = a.get("reset_at")
            add("warn", f"{a['label']} is rate-limited" + (f" until {datetime.fromtimestamp(until):%a %H:%M}" if isinstance(until, (int, float)) else ""))
        elif f["can_read"] and f["live_read_at"] and a["id"] not in f["live_ids"]:
            add("warn", f"No usage reading for {a['label']}: its usage could not be read")
        else:
            add("ok", f"{a['label']}: usable")
    if f["can_read"] and not f["live_read_at"]:
        add("warn", "No usage reading yet: open Accounts & usage or press Live usage")
    return checks


def _windows(snapshot):
    session = week = None
    for w in getattr(snapshot, "windows", None) or []:
        if getattr(w, "scope", "account") != "account" or not isinstance(w.used_percent, (int, float)):
            continue
        label = str(w.label).lower()
        if week is None and "week" in label:
            week = w
        elif session is None and ("session" in label or "5h" in label or "hour" in label):
            session = w
    return session, week


def run_timer(store, db, pool_for, now, read, send):
    """One timer run: apply park schedules, then (alerts on) read each account's usage, keep a
    snapshot for the report, raise and clear alerts, and send each new alert once."""
    lines = tick(store, pool_for, now)
    epoch = now.timestamp()
    for line in lines:
        provider, _, text = line.partition(": ")
        log_event(db, provider, "schedule", text, "timer", epoch)
    settings = store.alerts()
    if settings["desktop"] or settings["telegram"]:
        for provider in TIMER_PROVIDERS:
            accounts = []
            for entry in pool_for(provider).entries():
                status = getattr(entry, "last_status", None)
                parked = getattr(entry, "last_error_reason", None) == PARK_REASON
                row = {"label": entry.label or entry.id, "status": status, "session": None, "week": None,
                       "week_reset": None}
                if status != "dead" and not parked:
                    try:
                        session, week = _windows(read(provider, entry))
                    except Exception:  # noqa: BLE001 - one unreadable account must not stop the run
                        session = week = None
                    if session is not None:
                        row["session"] = session.used_percent
                    if week is not None:
                        row["week"] = week.used_percent
                        row["week_reset"] = week.reset_at.date().isoformat() if week.reset_at else None
                    peak = max((v for v in (row["session"], row["week"]) if v is not None), default=None)
                    if peak is not None:
                        record_snapshot(db, provider, row["label"], peak, epoch)
                if not parked:
                    accounts.append(row)
            if not accounts:
                continue
            before = store.alert_state().get(provider, {})
            active, raised, cleared = evaluate_alerts(before, accounts, settings["threshold"], epoch)
            for key in raised:
                text = active[key]["text"]
                send(f"HARP: {provider}", text)
                log_event(db, provider, "alert", text, "timer", epoch)
                lines.append(f"{provider}: alert {text}")
            for key in cleared:
                log_event(db, provider, "alert", f"Cleared: {before[key]['text']}", "timer", epoch)
            store.set_alert_state(provider, active)
    store._put("last_tick", epoch)
    return lines
