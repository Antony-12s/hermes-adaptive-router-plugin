"""HARP router engine: settings, Jev key state, learner cache and logging for one Hermes home.

Hermes-specific pieces (credential pool) are injected so the engine is testable without Hermes.
"""

import json
import threading
import time
from pathlib import Path

try:
    from . import ladder, router, typesafe
except ImportError:  # loaded by file path (plugin_api's _sibling) or as a top-level test module
    import importlib.util as _util
    import sys as _sys

    def _load(name):
        key = f"harp_{name}"   # same key as the plugin's _sibling(), so both share one module
        if key not in _sys.modules:
            spec = _util.spec_from_file_location(key, Path(__file__).with_name(f"{name}.py"))
            module = _util.module_from_spec(spec)
            _sys.modules[key] = module
            spec.loader.exec_module(module)
        return _sys.modules[key]

    ladder, router, typesafe = _load("ladder"), _load("router"), _load("typesafe")

KEY_RECHECK = 300          # seconds between Jev key checks
LOAD_MAX_AGE = 3600        # usage readings older than this do not count as load


def ladder_table():
    """The ladder as the Desktop page shows it."""
    tiers = []
    for index, name in enumerate(ladder.TIERS):
        rungs = {provider: {**ladder.rung(provider, index), "cost": round(ladder.cost_of(ladder.rung(provider, index)), 1)}
                 for provider in ladder.RUNGS}
        tiers.append({"tier": index, "name": name, **rungs})
    return {"tiers": tiers, "modes": ladder.MODE_TOP_TIER, "efforts": list(ladder.EFFORTS)}


class Engine:
    def __init__(self, home, jev_entries=lambda: [], check=typesafe.check, ask=typesafe.system_one, clock=time.time):
        self.home = Path(home)
        self.settings_path = self.home / "harp-router.json"
        self.load_path = self.home / "harp-load.json"
        self.store = router.Store(self.home / "harp-router.sqlite3")
        self._jev_entries, self._check, self._ask, self._clock = jev_entries, check, ask, clock
        self._key = None
        self._learner = (None, None, None)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- settings / load
    def settings(self):
        return router.load_settings(self.settings_path)

    def save(self, changes):
        with self._lock:
            return router.save_settings(self.settings_path, {**self.settings(), **changes})

    def set_anthropic_display_plan(self, account_id, plan):
        if plan is not None and plan not in router.ANTHROPIC_DISPLAY_PLANS:
            raise ValueError("Choose a supported Anthropic plan.")
        with self._lock:
            settings = self.settings()
            plans = settings["anthropic_display_plans"]
            if plan is None:
                plans.pop(account_id, None)
            else:
                plans[account_id] = plan
            router.save_settings(self.settings_path, settings)

    def record_load(self, provider, peaks, plans=None):
        """Called after a usage read: {entry id: highest window percent}."""
        try:
            data = json.loads(self.load_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[provider] = {"at": self._clock(), "peaks": peaks, "detected_plans": plans or {}}
        self.load_path.write_text(json.dumps(data), encoding="utf-8")

    def loads(self, settings=None):
        settings = settings or self.settings()
        try:
            data = json.loads(self.load_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        fresh = {p: v for p, v in data.items() if self._clock() - v.get("at", 0) < LOAD_MAX_AGE}
        result = {}
        for provider, readings in fresh.items():
            peaks = readings.get("peaks") or {}
            known = {account: peak for account, peak in peaks.items() if peak is not None}
            plans = {**(readings.get("detected_plans") or {}), **settings["plans"].get(provider, {})}
            load = router.provider_load(known, plans)
            if load is not None:
                quota_fresh = 0 <= self._clock() - readings["at"] <= 600
                load["pool_near_limit"] = quota_fresh and len(known) == len(peaks) and min(known.values()) >= settings["limit"]
                load["pool_has_room"] = quota_fresh and min(known.values()) < settings["limit"]
                result[provider] = load
        return result

    # ---------------------------------------------------------------- Jev key
    def jev_status(self, force=False):
        """{"state": none|ok|bad|down, "label", "ms", "checked_at"}; rechecked every 5 minutes."""
        entries = [e for e in self._jev_entries() if getattr(e, "last_status", None) != "dead"]
        if not entries:
            self._key = None
            return {"state": "none"}
        entry = entries[0]
        cached = self._key
        if not force and cached and cached["id"] == entry.id and self._clock() - cached["checked_at"] < KEY_RECHECK:
            return {k: v for k, v in cached.items() if k != "id"}
        result = self._check(entry.runtime_api_key, entry.runtime_base_url or typesafe.BASE_URL)
        self._key = {"id": entry.id, "state": typesafe.key_state(result), "label": entry.label or entry.id,
                     "ms": result.get("ms"), "problem": result.get("problem"), "checked_at": self._clock()}
        return {k: v for k, v in self._key.items() if k != "id"}

    def _jev_key(self):
        entries = [e for e in self._jev_entries() if getattr(e, "last_status", None) != "dead"]
        return (entries[0].runtime_api_key, entries[0].runtime_base_url or typesafe.BASE_URL) if entries else (None, None)

    def classify_delegation(self, goal, options):
        settings = self.settings()
        key, base_url = self._jev_key()
        if not key or self.store.jev_calls_today() >= settings["jev_daily_cap"]:
            return None
        criteria = {label: f"{job['name']}: {job['description']}" for label, job in options.items()}
        criteria["none"] = "No configured task matches the goal."
        self.store.count_jev_call()
        answers, _ = self._ask(key, goal[:router.TEXT_KEEP], {"route": {
            "type": "choice", "instructions": "Which configured work type matches this delegated goal?",
            "criteria": criteria,
        }}, model=settings["jev_model"], base_url=base_url)
        selected = answers.get("route")
        return selected.get("choice") if isinstance(selected, dict) else None

    # ---------------------------------------------------------------- deciding
    def learners(self, settings):
        signature = (self.store.stats(days=36500)["samples"], json.dumps(settings["candidates"], sort_keys=True))
        with self._lock:
            if self._learner[0] != signature:
                learner, levels = self.store.learner(settings["candidates"])
                self._learner = (signature, learner, levels)
            return self._learner[1], self._learner[2]

    def tier_learner(self):
        """The difficulty learner: the tier hints plus everything Jev, corrections and verified runs taught."""
        signature = ("tier", self.store.stats(days=36500)["samples"])
        with self._lock:
            if self._learner[0] != signature:
                learner = router.Learner()
                learner.TEMPERATURE = ladder.LEARNER_TEMPERATURE
                for label, hint in zip(ladder.LABELS, ladder.TIER_HINTS):
                    learner.learn(hint, label, 1.0)
                for text, label, weight in self.store.tier_samples(ladder.LABELS):
                    learner.learn(text, label, weight)
                self._learner = (signature, learner, None)
            return self._learner[1]

    def feedback(self, decision_id, label):
        if not self.settings()["ladder"]:
            return self.store.feedback(decision_id, label)
        row = self.store.decision(decision_id)
        tier = ladder.tier_of_label(label) if label else ladder.tier_of_route(row["pick"].split(":", 1)[-1], row["effort"])
        return self.store.feedback(decision_id, label, sample_label=str(tier))

    def verify(self, decision_id):
        if not self.settings()["ladder"]:
            return self.store.verify_route(decision_id)
        row = self.store.decision(decision_id)
        tier = ladder.tier_of_route(row["pick"].split(":", 1)[-1], row["effort"])
        return self.store.verify_route(decision_id, sample_label=str(tier))

    def decide(self, text, session="", platform="", current="", log=True, use_jev=True):
        settings = self.settings()
        if not settings["enabled"] or not (settings["candidates"] or settings["ladder"]) or not (text or "").strip():
            return None
        if settings["ladder"]:
            decision = self._decide_ladder(text, settings, self.loads(settings), use_jev)
            if log:
                decision["id"] = self.store.log(decision, text, session, platform, current, applied=settings["apply"])
            return decision
        learner, levels = self.learners(settings)
        seeds = sum(1 for c in settings["candidates"] if c.get("description"))
        loads = self.loads(settings)
        local = router.decide(text, settings, learner, levels, loads, seeds=seeds)
        decision = local
        verified = router.reusable_route(self.store, text, settings, loads)
        if verified:
            pick, effort = verified
            decision = {**local, "pick": pick, "label": pick, "effort": effort, "unsure": False, "moved_from": None}
        elif use_jev and local["unsure"] and self.jev_status()["state"] == "ok" \
                and self.store.jev_calls_today() < settings["jev_daily_cap"]:
            decision = self._ask_jev(text, settings, learner, levels, loads, seeds) or local
        if log:
            decision["id"] = self.store.log(decision, text, session, platform, current, applied=settings["apply"])
        return decision

    def _decide_ladder(self, text, settings, loads, use_jev):
        """Tier from the learner (or Jev when it is unsure), provider from quota, model and effort from the ladder."""
        mode, limit = settings["mode"], settings["limit"]
        heavyweight = ladder.heavyweight_provider(settings["heavyweight"])
        learner, seeds = self.tier_learner(), len(ladder.TIER_HINTS)
        seeded = 0 if router.task_group_for(text) == "weather_lookup" else None   # a known trivial lookup
        picked = ladder.decide(text, learner, mode, heavyweight, loads, limit, seeds=seeds, tier=seeded)
        by, jev_ms = "local", None
        if (seeded is None and use_jev and picked["unsure"] and self.jev_status()["state"] == "ok"
                and self.store.jev_calls_today() < settings["jev_daily_cap"]):
            asked = self._ask_jev_tier(text, settings)
            if asked:
                picked = ladder.decide(text, learner, mode, heavyweight, loads, limit, seeds=seeds, jev=asked[0])
                by, jev_ms = "jev", asked[1]
        moved = picked["moved_from"]
        return {"pick": f"{picked['provider']}:{picked['model']}", "label": str(picked["tier"]), "effort": picked["effort"],
                "by": by, "confidence": picked["confidence"], "probs": picked["probs"], "difficulty": picked["expected"],
                "unsure": picked["unsure"], "moved_from": f"{moved['provider']}:{moved['model']}" if moved else None,
                "tier": picked["tier"], "tier_name": picked["tier_name"], "ladder": True, "jev_ms": jev_ms}

    def _ask_jev_tier(self, text, settings):
        key, base_url = self._jev_key()
        if not key:
            return None
        try:
            self.store.count_jev_call()
            questions = ladder.jev_questions(self.store.examples)
            answers, ms = self._ask(key, text[:router.TEXT_KEEP], questions, model=settings["jev_model"], base_url=base_url)
        except Exception as error:  # noqa: BLE001 - Jev failing must fall back to the local pick
            if self._key:
                self._key.update(state="down", problem=str(error)[:120], checked_at=self._clock())
            return None
        parsed = ladder.read_jev(answers)
        return (parsed, ms) if parsed else None

    def _ask_jev(self, text, settings, learner, levels, loads, seeds):
        key, base_url = self._jev_key()
        if not key:
            return None
        try:
            self.store.count_jev_call()
            questions = router.jev_questions(settings["candidates"], self.store.examples)
            answers, ms = self._ask(key, text[:router.TEXT_KEEP], questions, model=settings["jev_model"], base_url=base_url)
        except Exception as error:  # noqa: BLE001 - Jev failing must fall back to the local pick
            if self._key:
                self._key.update(state="down", problem=str(error)[:120], checked_at=self._clock())
            return None
        parsed = router.read_jev(answers, settings["candidates"])
        if not parsed:
            return None
        decision = router.decide(text, settings, learner, levels, loads, jev=parsed, seeds=seeds)
        decision["jev_ms"] = ms
        return decision

    def status(self):
        settings = self.settings()
        learner = self.tier_learner() if settings["ladder"] else self.learners(settings)[0]
        return {"settings": settings, "ladder": ladder_table() if settings["ladder"] else None, "jev": self.jev_status(), "loads": self.loads(settings),
                "stats": self.store.stats(), "recent": self.store.recent(),
                "jev_calls_today": self.store.jev_calls_today(), "samples": round(learner.samples)}
