"""HARP router engine: settings, Jev key state, learner cache and logging for one Hermes home.

Hermes-specific pieces (credential pool) are injected so the engine is testable without Hermes.
"""

import json
import threading
import time
from pathlib import Path

try:
    from . import router, typesafe
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

    router, typesafe = _load("router"), _load("typesafe")

KEY_RECHECK = 300          # seconds between Jev key checks
LOAD_MAX_AGE = 3600        # usage readings older than this do not count as load


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
        return router.save_settings(self.settings_path, {**self.settings(), **changes})

    def record_load(self, provider, peaks):
        """Called after a usage read: {entry id: highest window percent}."""
        try:
            data = json.loads(self.load_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[provider] = {"at": self._clock(), "peaks": peaks}
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
            load = router.provider_load(known, settings["plans"].get(provider, {}))
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

    # ---------------------------------------------------------------- deciding
    def learners(self, settings):
        signature = (self.store.stats(days=36500)["samples"], json.dumps(settings["candidates"], sort_keys=True))
        with self._lock:
            if self._learner[0] != signature:
                learner, levels = self.store.learner(settings["candidates"])
                self._learner = (signature, learner, levels)
            return self._learner[1], self._learner[2]

    def decide(self, text, session="", platform="", current="", log=True, use_jev=True):
        settings = self.settings()
        if not settings["enabled"] or not settings["candidates"] or not (text or "").strip():
            return None
        learner, levels = self.learners(settings)
        seeds = sum(1 for c in settings["candidates"] if c.get("description"))
        loads = self.loads(settings)
        local = router.decide(text, settings, learner, levels, loads, seeds=seeds)
        decision = local
        if use_jev and local["unsure"] and self.jev_status()["state"] == "ok" \
                and self.store.jev_calls_today() < settings["jev_daily_cap"]:
            decision = self._ask_jev(text, settings, learner, levels, loads, seeds) or local
        if log:
            decision["id"] = self.store.log(decision, text, session, platform, current, applied=settings["apply"])
        return decision

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
        learner, _ = self.learners(settings)
        return {"settings": settings, "jev": self.jev_status(), "loads": self.loads(settings),
                "stats": self.store.stats(), "recent": self.store.recent(),
                "jev_calls_today": self.store.jev_calls_today(), "samples": round(learner.samples)}
