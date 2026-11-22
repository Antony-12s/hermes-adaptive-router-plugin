"""HARP router: pick a provider, model and effort for each user message.

Pure logic, no Hermes imports. The deciding signal comes from Jev (TypeSafe) when a working key
exists, otherwise from a local learner that is trained on Jev's past decisions and the user's
corrections. Account load (plan weight x usage) moves work away from providers near their limit.
"""

import json
import math
import re
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

EFFORTS = ["low", "medium", "high"]
MODES = {   # effort ceiling, confidence needed before trusting a pick
    "save": {"cap": 0, "sure": 0.55},
    "balanced": {"cap": 1, "sure": 0.70},
    "best": {"cap": 2, "sure": 0.85},
}
PLANS = {"x1": 1, "x5": 5, "x10": 10, "x20": 20}
ANTHROPIC_DISPLAY_PLANS = ("Pro", "Max", "Max 5x", "Max 20x")
DIFFICULTY = [
    "Trivial: a greeting, small talk, or a one-line factual answer",
    "Moderate: an ordinary task with some reasoning, a short explanation or summary",
    "Hard: multi-step work, debugging, design, planning or long analysis",
]
DEFAULTS = {
    "enabled": True, "apply": False, "mode": "balanced", "limit": 85, "heavyweight": "",
    "jev_model": "jev-latest", "jev_daily_cap": 300, "plans": {},
    "anthropic_display_plans": {},
    "candidates": [   # strongest first; editable on the Diagram page
        {"provider": "anthropic", "model": "claude-opus-5-5", "cap": "high",
         "description": "Hard work: analysis, planning, design, long or careful writing"},
        {"provider": "openai-codex", "model": "gpt-6-sol", "cap": "high",
         "description": "Code: writing programs, fixing bugs, build errors, app crashes"},
        {"provider": "anthropic", "model": "claude-sonnet-5", "cap": "medium",
         "description": "Everyday tasks: short questions, chat, summaries, explanations, translation"},
    ],
}
TEXT_KEEP = 2000
MIN_SAMPLES = 30          # the local learner's confidence is damped until it has seen this many


def candidate_id(candidate):
    return f"{candidate['provider']}:{candidate['model']}"


# --------------------------------------------------------------------------- settings
def load_settings(path):
    try:
        stored = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    return json.loads(json.dumps({**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}))


def validate(settings):
    if settings["mode"] not in MODES:
        raise ValueError("mode must be save, balanced or best")
    if not (50 <= int(settings["limit"]) <= 100):
        raise ValueError("limit must be 50 to 100")
    if not isinstance(settings.get("heavyweight", ""), str):
        raise ValueError("heavyweight must be a candidate ID or empty for Auto")
    seen = set()
    for c in settings["candidates"]:
        if not (isinstance(c, dict) and c.get("provider") and c.get("model")):
            raise ValueError("each candidate needs a provider and a model")
        if c.get("cap", "high") not in EFFORTS:
            raise ValueError("cap must be low, medium or high")
        if candidate_id(c) in seen:
            raise ValueError(f"{candidate_id(c)} is listed twice")
        seen.add(candidate_id(c))
    for provider, plans in settings["plans"].items():
        if any(p not in PLANS for p in plans.values()):
            raise ValueError("plans must be x1, x5, x10 or x20")
    return settings


def save_settings(path, settings):
    settings = validate({**DEFAULTS, **settings})
    Path(path).write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    return settings


# --------------------------------------------------------------------------- load
def provider_load(peaks, plans):
    """peaks: {entry id: highest window percent}; plans: {entry id: "x5"}. Weighted percent used."""
    if not peaks:
        return None
    capacity = sum(PLANS.get(plans.get(entry, "x1"), 1) for entry in peaks)
    left = sum(PLANS.get(plans.get(entry, "x1"), 1) * max(0.0, 100 - (peak or 0)) / 100 for entry, peak in peaks.items())
    return {"used": round(100 - left / capacity * 100), "left": round(left, 2), "capacity": capacity}


# --------------------------------------------------------------------------- local learner
def _features(text):
    text = re.sub(r"\s+", " ", (text or "").lower()).strip()[:TEXT_KEEP]
    grams = Counter(text[i:i + n] for n in (2, 3) for i in range(max(0, len(text) - n + 1)))
    grams.update(f"w:{w}" for w in re.findall(r"[a-z0-9_]{2,}", text))
    grams[f"len:{min(6, len(text).bit_length() // 2)}"] += 3
    return grams


class Learner:
    """TF-IDF centroids over character n-grams (works for Thai, which has no spaces).

    An option scores by cosine similarity to everything it was taught; an option with no data
    scores 0, so it never wins by default. Softmax turns scores into probabilities."""

    TEMPERATURE = 12.0

    def __init__(self):
        self.counts, self.docs, self.df, self.n = defaultdict(Counter), Counter(), Counter(), 0

    def learn(self, text, label, weight=1.0):
        grams = _features(text)
        for gram, count in grams.items():
            self.counts[label][gram] += count * weight
        self.df.update(grams.keys())
        self.docs[label] += weight
        self.n += 1

    @property
    def samples(self):
        return sum(self.docs.values())

    def _idf(self, gram):
        return math.log((self.n + 1) / (self.df[gram] + 1)) + 1

    def _vector(self, counts):
        vector = {g: c * self._idf(g) for g, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
        return vector, norm

    def predict(self, text, labels, seeds=0):
        """Probabilities over `labels` and a confidence damped by how much real data exists."""
        if not labels:
            return {}, 0.0
        query, qnorm = self._vector(_features(text))
        scores = {}
        for label in labels:
            if not self.counts[label]:
                scores[label] = 0.0
                continue
            centroid, cnorm = self._vector(self.counts[label])
            scores[label] = sum(v * centroid.get(g, 0.0) for g, v in query.items()) / (qnorm * cnorm)
        top = max(scores.values())
        exp = {l: math.exp(self.TEMPERATURE * (s - top)) for l, s in scores.items()}
        total = sum(exp.values())
        probs = {l: v / total for l, v in exp.items()}
        real = max(0.0, self.samples - seeds)
        return probs, confidence(probs) * min(1.0, real / MIN_SAMPLES)


def confidence(probs):
    """1 when all probability is on one option, 0 when it is spread evenly (like Jev's)."""
    values = [p for p in probs.values() if p > 0]
    if len(probs) < 2:
        return 1.0
    entropy = -sum(p * math.log(p) for p in values)
    return max(0.0, 1 - entropy / math.log(len(probs)))


# --------------------------------------------------------------------------- store
class Store:
    def __init__(self, path):
        self.path = Path(path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, ts REAL, session TEXT, platform TEXT,
                text TEXT, pick TEXT, effort TEXT, by TEXT, confidence REAL, probs TEXT, difficulty REAL,
                moved_from TEXT, current TEXT, applied INTEGER DEFAULT 0, jev_ms INTEGER, feedback TEXT);
              CREATE TABLE IF NOT EXISTS samples (id INTEGER PRIMARY KEY, ts REAL, text TEXT, label TEXT,
                difficulty INTEGER, weight REAL, source TEXT);
              CREATE TABLE IF NOT EXISTS verified_routes (decision_id INTEGER PRIMARY KEY);
              CREATE TABLE IF NOT EXISTS jev_calls (day TEXT PRIMARY KEY, calls INTEGER);
              CREATE TABLE IF NOT EXISTS live_calls (id INTEGER PRIMARY KEY, ts REAL NOT NULL,
                kind TEXT NOT NULL, text TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                effort TEXT NOT NULL, duration REAL, tokens INTEGER, step INTEGER, status TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '');
            """)

    def _db(self):
        return closing(sqlite3.connect(self.path))

    def log_live(self, kind, text, provider, model, effort, duration, tokens, step, status, source):
        with self._db() as db, db:
            db.execute("INSERT INTO live_calls (ts, kind, text, provider, model, effort, duration, tokens, step, status, source)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (time.time(), kind, text, provider, model, effort, duration, tokens, step, status, source))
            db.execute("DELETE FROM live_calls WHERE id <= (SELECT MAX(id) - 100 FROM live_calls)")

    def live(self, limit=6):
        with self._db() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM live_calls ORDER BY id DESC LIMIT ?", (limit,))]

    def log(self, decision, text, session="", platform="", current="", applied=False):
        with self._db() as db, db:
            cur = db.execute("INSERT INTO decisions (ts, session, platform, text, pick, effort, by, confidence, probs,"
                             " difficulty, moved_from, current, applied, jev_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (time.time(), session, platform, (text or "")[:TEXT_KEEP], decision["pick"], decision["effort"],
                              decision["by"], decision["confidence"], json.dumps(decision["probs"]),
                              decision.get("difficulty"), decision.get("moved_from"), current, int(applied),
                              decision.get("jev_ms")))
            if decision["by"] == "jev" and decision.get("label"):
                db.execute("INSERT INTO samples (ts, text, label, difficulty, weight, source) VALUES (?,?,?,?,?,?)",
                           (time.time(), (text or "")[:TEXT_KEEP], decision["label"],
                            round(decision.get("difficulty") or 0), 1.0, "jev"))
            return cur.lastrowid

    def feedback(self, decision_id, label):
        """label = the candidate id that should have been used ("" = the pick was right)."""
        with self._db() as db, db:
            row = db.execute("SELECT text, pick, difficulty FROM decisions WHERE id=?", (decision_id,)).fetchone()
            if row is None:
                raise KeyError(decision_id)
            text, pick, difficulty = row
            correct = label or pick
            db.execute("UPDATE decisions SET feedback=? WHERE id=?", (correct if label else "ok", decision_id))
            db.execute("INSERT INTO samples (ts, text, label, difficulty, weight, source) VALUES (?,?,?,?,?,?)",
                       (time.time(), text, correct, round(difficulty or 0), 3.0, "user"))
        return correct

    def verify_route(self, decision_id):
        with self._db() as db, db:
            row = db.execute("SELECT text, pick, difficulty FROM decisions WHERE id=?", (decision_id,)).fetchone()
            if row is None:
                raise KeyError(decision_id)
            if db.execute("INSERT OR IGNORE INTO verified_routes VALUES (?)", (decision_id,)).rowcount:
                text, pick, difficulty = row
                db.execute("INSERT INTO samples (ts, text, label, difficulty, weight, source) VALUES (?,?,?,?,?,?)",
                           (time.time(), text, pick, round(difficulty or 0), 2.0, "verified"))
            return row[1]

    def verified_route_for(self, task_group):
        with self._db() as db:
            rows = db.execute(
                "SELECT d.text, d.pick, d.effort FROM verified_routes v JOIN decisions d ON d.id=v.decision_id"
                " WHERE d.feedback IS NULL OR d.feedback='ok' ORDER BY d.id DESC LIMIT 100"
            ).fetchall()
        return next(((pick, effort) for text, pick, effort in rows if task_group_for(text) == task_group), None)

    def learner(self, candidates):
        learner, difficulty = Learner(), Learner()
        for c in candidates:    # seed each option with its description so a fresh install still guesses
            if c.get("description"):
                learner.learn(c["description"], candidate_id(c), 1.0)
        with self._db() as db:
            for text, label, level, weight in db.execute("SELECT text, label, difficulty, weight FROM samples"):
                learner.learn(text, label, weight)
                difficulty.learn(text, str(level), weight)
        return learner, difficulty

    def examples(self, label, limit=3):
        """Recent user-confirmed messages for one option: they go to Jev as examples."""
        with self._db() as db:
            return [r[0][:200] for r in db.execute(
                "SELECT text FROM samples WHERE label=? AND source IN ('user','verified') ORDER BY source='user' DESC, ts DESC LIMIT ?", (label, limit))]

    def recent(self, limit=20):
        with self._db() as db:
            rows = db.execute("SELECT id, ts, platform, text, pick, effort, by, confidence, moved_from, current,"
                              " applied, feedback, EXISTS(SELECT 1 FROM verified_routes WHERE decision_id=decisions.id)"
                              " FROM decisions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        keys = ["id", "ts", "platform", "text", "pick", "effort", "by", "confidence", "moved_from", "current", "applied", "feedback", "verified"]
        return [dict(zip(keys, (*r[:3], (r[3] or "")[:120], *r[4:]))) for r in rows]

    def stats(self, days=7):
        since = time.time() - days * 86400
        with self._db() as db:
            total, local, jev = db.execute(
                "SELECT COUNT(*), SUM(by='local'), SUM(by='jev') FROM decisions WHERE ts>?", (since,)).fetchone()
            samples, users = db.execute("SELECT COUNT(*), SUM(source='user') FROM samples").fetchone()
            corrections = db.execute(
                "SELECT pick, feedback, COUNT(*) FROM decisions WHERE feedback IS NOT NULL AND feedback!='ok'"
                " GROUP BY pick, feedback ORDER BY 3 DESC LIMIT 5").fetchall()
            rated, right = db.execute(
                "SELECT COUNT(*), SUM(feedback='ok') FROM decisions WHERE feedback IS NOT NULL").fetchone()
        return {"decisions": total or 0, "local": local or 0, "jev": jev or 0, "samples": samples or 0,
                "corrections_total": users or 0, "rated": rated or 0, "right": right or 0,
                "corrections": [{"from": a, "to": b, "times": n} for a, b, n in corrections]}

    def jev_calls_today(self):
        with self._db() as db:
            row = db.execute("SELECT calls FROM jev_calls WHERE day=?", (time.strftime("%Y-%m-%d"),)).fetchone()
        return row[0] if row else 0

    def count_jev_call(self):
        with self._db() as db, db:
            db.execute("INSERT INTO jev_calls VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET calls=calls+1",
                       (time.strftime("%Y-%m-%d"),))


# --------------------------------------------------------------------------- Jev questions
def jev_questions(candidates, examples=lambda label: []):
    criteria = {}
    for c in candidates:
        what = c.get("description") or f"{c['provider']} {c['model']}"
        shown = examples(candidate_id(c))
        criteria[candidate_id(c)] = {"what": what, "examples": shown} if shown else what
    return {
        "route": {"type": "choice", "instructions": "Which assistant model should answer this user message?",
                  "criteria": criteria},
        "difficulty": {"type": "score", "instructions": "How much thinking does answering this message need?",
                       "criteria": DIFFICULTY},
    }


def read_jev(answers, candidates):
    route, level = answers.get("route") or {}, answers.get("difficulty") or {}
    ids = [candidate_id(c) for c in candidates]
    probs = {k: float(v) for k, v in (route.get("probabilities") or {}).items() if k in ids}
    if not probs or route.get("choice") not in ids:
        return None
    return {"probs": probs, "pick": route["choice"], "confidence": float(route.get("confidence") or 0),
            "difficulty": float(level.get("score") or 0)}


# --------------------------------------------------------------------------- decision
def decide(text, settings, learner, difficulty_learner, loads, jev=None, seeds=0):
    """Return the pick for one message. `jev` is Jev's parsed answer or None (local learner decides).

    loads: {provider: provider_load(...) or None}. Candidate order is strength order: first is strongest.
    """
    candidates = settings["candidates"]
    if not candidates:
        return None
    mode = MODES[settings["mode"]]
    ids = [candidate_id(c) for c in candidates]
    by_id = dict(zip(ids, candidates))
    if jev:
        probs, conf, level, by = jev["probs"], jev["confidence"], jev["difficulty"], "jev"
    else:
        probs, conf = learner.predict(text, ids, seeds)
        level_probs, _ = difficulty_learner.predict(text, ["0", "1", "2"])
        level = sum(int(k) * p for k, p in level_probs.items()) if difficulty_learner.samples else _length_level(text)
        by = "local"
    def quota(cid):
        return loads.get(by_id[cid]["provider"]) or {}

    def hot(cid):
        usage = quota(cid)
        if usage.get("pool_has_room"):
            return False
        return usage.get("pool_near_limit") or usage.get("used", 0) >= settings["limit"]

    label = max(probs, key=probs.get)
    unsure = conf < mode["sure"]
    pick = ids[0] if unsure else label
    moved = None
    alternate = None
    coding = by_id[pick]["provider"] == "openai-codex" and re.fullmatch(
        r"(?:gpt-[\w.]+-)?sol", by_id[pick]["model"])
    if coding:
        alternate = next((cid for cid in ids if by_id[cid]["provider"] == "anthropic"
                          and re.fullmatch(r"(?:claude-)?opus(?:-[\w.-]+)?", by_id[cid]["model"])), None)
    # Only compare interchangeable coding candidates with known capacity.
    heavyweight = settings.get("heavyweight")
    hard_work = coding or (not unsure and by_id[pick]["provider"] == "anthropic" and
                           re.fullmatch(r"(?:claude-)?opus(?:-[\w.-]+)?", by_id[pick]["model"]))
    if hard_work and heavyweight in by_id and not hot(heavyweight):
        if heavyweight != pick:
            moved, pick = pick, heavyweight
    elif alternate and not hot(alternate):
        current_left, alternate_left = quota(pick).get("left"), quota(alternate).get("left")
        if current_left is not None and alternate_left is not None and alternate_left > current_left:
            moved, pick = pick, alternate
    if hot(pick):
        order = [ids[0]] + [i for i in ids if i != ids[0]] if unsure else sorted(ids, key=lambda i: -probs.get(i, 0))
        cool = next((i for i in order if not hot(i)), None)
        if cool and cool != pick:
            moved, pick = pick, cool
    cap = EFFORTS.index(by_id[pick].get("cap", "high"))
    want = round(level) if not unsure else max(1, mode["cap"])
    if coding:
        want = max(1, want)
    effort = EFFORTS[max(0, min(want, cap, mode["cap"]))]
    return {"pick": pick, "label": label, "effort": effort, "by": by, "confidence": round(conf, 3),
            "probs": {k: round(v, 3) for k, v in probs.items()}, "difficulty": round(level, 2),
            "unsure": unsure, "moved_from": moved}


def task_group_for(text):
    """Group short weather lookups with known equivalent phrasing."""
    normalized = re.sub(r"[?？.!]+$", "", " ".join(text.casefold().split())).strip()
    if re.fullmatch(
        r"(?:วันนี้\s*(?:สภาพ)?อากาศ(?:เป็น)?(?:อย่างไร|ยังไง)|"
        r"วันนี้\s*ฝน(?:จะ)?ตก(?:ไหม|มั้ย)|"
        r"(?:what(?:'s| is) the weather(?: like)?|(?:will it|is it going to) rain) today)"
        r"(?:\s*(?:ครับ|คะ|ค่ะ))?", normalized
    ):
        return "weather_lookup"
    return None


def reusable_route(store, text, settings, loads):
    task_group = task_group_for(text)
    if not task_group:
        return None
    verified = store.verified_route_for(task_group)
    if not verified:
        return None
    pick, effort = verified
    candidate = next((item for item in settings["candidates"] if candidate_id(item) == pick), None)
    if not candidate or effort not in EFFORTS:
        return None
    usage = loads.get(candidate["provider"]) or {}
    if usage.get("pool_near_limit") or (not usage.get("pool_has_room") and usage.get("used", 0) >= settings["limit"]):
        return None
    cap = min(MODES[settings["mode"]]["cap"], EFFORTS.index(candidate.get("cap", "high")))
    return pick, EFFORTS[min(EFFORTS.index(effort), cap)]


def _length_level(text):
    n = len((text or "").strip())
    return 0 if n < 40 else 1 if n < 400 else 2


def message_text(message):
    """The text of a user message that may be a string or a list of content parts."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return " ".join(p.get("text", "") for p in message if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def apply_route(request, api_mode, model, effort, thinking=None):
    """Return a copy of a provider request using `model` and `effort`, or None if it can't be done safely.

    thinking(model, effort, max_tokens) -> Anthropic thinking kwargs (Hermes' own mapping), needed for
    anthropic_messages so the new model gets parameters it accepts."""
    if not isinstance(request, dict) or not request.get("model"):
        return None
    out = {**request, "model": model}
    if api_mode == "anthropic_messages":
        if thinking is None:
            return None
        out.pop("thinking", None)
        out.pop("output_config", None)
        out.update(thinking(model, effort, request.get("max_tokens") or 16000))
    elif api_mode == "codex_responses":
        out["reasoning"] = {**(request.get("reasoning") or {}), "effort": effort}
    elif api_mode == "chat_completions":
        if "reasoning_effort" in request:
            out["reasoning_effort"] = effort
    else:
        return None
    return out
