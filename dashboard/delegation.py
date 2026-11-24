"""Settings and route selection for Hermes' optional HARP delegation backend."""

import json
import os
import re
import tempfile
from pathlib import Path

try:
    from . import api_keys
except ImportError:  # loaded by file path (plugin_api's _sibling) or as a top-level test module
    import importlib.util as _util
    import sys as _sys

    if "harp_api_keys" not in _sys.modules:
        _spec = _util.spec_from_file_location("harp_api_keys", Path(__file__).with_name("api_keys.py"))
        _sys.modules["harp_api_keys"] = _util.module_from_spec(_spec)
        _spec.loader.exec_module(_sys.modules["harp_api_keys"])
    api_keys = _sys.modules["harp_api_keys"]

SUBSCRIPTIONS = ("anthropic", "openai-codex")
PROVIDERS = SUBSCRIPTIONS + api_keys.IDS
EFFORT_TIER = {"low": 1, "medium": 2, "high": 3}
EFFORTS = ("low", "medium", "high")
DEFAULTS = {"classifier": "local", "jobs": []}


def read(home, name, default):
    try:
        return json.loads((Path(home) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write(home, name, value):
    home = Path(home)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=home, delete=False) as file:
        path = Path(file.name)
        try:
            json.dump(value, file, ensure_ascii=False)
        except BaseException:
            file.close()
            path.unlink(missing_ok=True)
            raise
    try:
        os.replace(path, home / name)
    finally:
        path.unlink(missing_ok=True)


def validate(body):
    classifier = body.get("classifier")
    jobs = body.get("jobs")
    if classifier not in ("local", "jev") or not isinstance(jobs, list) or len(jobs) > 50:
        raise ValueError("Choose a classifier and configure at most 50 jobs.")
    result, names = [], set()
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("Invalid job.")
        name, description, provider, model, effort = (
            job.get(key, "") for key in ("name", "description", "provider", "model", "effort")
        )
        if not all(isinstance(value, str) for value in (name, description, provider, model, effort)):
            raise ValueError("Job fields must be text.")
        name, description, model = name.strip(), description.strip(), model.strip()
        if (not name or len(name) > 70 or len(description) > 300 or name.casefold() in names
                or provider not in PROVIDERS or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,149}", model)
                or effort not in EFFORTS):
            raise ValueError("Each job needs a unique name and a valid provider, model and effort.")
        names.add(name.casefold())
        result.append(dict(name=name, description=description, provider=provider, model=model, effort=effort))
    return {"classifier": classifier, "jobs": result}


def settings(home):
    try:
        return validate(read(home, "harp-delegation.json", DEFAULTS))
    except (ValueError, AttributeError):
        return DEFAULTS


def local_match(goal, jobs):
    goal = goal.casefold()
    exact = next((job for job in jobs if job["name"].casefold() in goal), None)
    if exact:
        return exact
    words = set(re.findall(r"\w{3,}", goal))
    scored = [(len(words.intersection(re.findall(r"\w{3,}", job["description"].casefold()))), job)
              for job in jobs]
    ranked = sorted(scored, key=lambda item: item[0], reverse=True)
    if ranked and ranked[0][0] and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
        return ranked[0][1]
    return None


def select(goal, config, engine):
    jobs = config["jobs"]
    if not goal.strip() or not jobs:
        return None, "hermes"
    if config["classifier"] == "jev":
        try:
            options = {f"job_{index}": job for index, job in enumerate(jobs)}
            label = engine.classify_delegation(goal, options)
            if label in options:
                return options[label], "jev"
            if label == "none":
                return None, "jev"
        except (ValueError, TypeError, OSError):
            pass
    match = local_match(goal, jobs)
    return match, "local" if match else "hermes"


def ladder_route(goal, engine):
    """{provider, model, reasoning_effort} from the router's cost ladder, or None to keep Hermes' default."""
    if not goal.strip():
        return None
    try:
        if not engine.settings().get("ladder"):
            return None
        decision = engine.decide(goal, log=False, use_jev=False)
    except Exception:  # noqa: BLE001 - routing a subagent must never refuse the task
        return None
    if not decision or ":" not in decision.get("pick", ""):
        return None
    provider, model = decision["pick"].split(":", 1)
    return {"provider": provider, "model": model, "reasoning_effort": decision["effort"]}


def api_fallback(route, source, engine, fallback=None):
    """A saved route on a subscription pool that is near its limit moves to an API key, when one is allowed."""
    if route["provider"] not in SUBSCRIPTIONS:
        return route, source
    try:
        load = engine.loads(engine.settings()).get(route["provider"]) or {}
    except Exception:  # noqa: BLE001 - unknown load: keep the saved route
        return route, source
    if not load.get("pool_near_limit"):
        return route, source
    pick = (fallback or api_keys.fallback)(engine.home, EFFORT_TIER.get(route["reasoning_effort"], 2))
    if not pick:
        return route, source
    return {"provider": pick["provider"], "model": pick["model"], "reasoning_effort": route["reasoning_effort"]}, f"{source}+api"


def backend(engine_factory):
    from agent.delegation_provider import DelegationProvider, ROUTE_KEYS

    class HarpDelegation(DelegationProvider):
        name = "harp"
        display_name = "HARP task routes"
        summary = "Classify delegated tasks and select a saved route."

        def is_available(self):
            return True

        def task_parameters(self, config):
            names = [job["name"] for job in settings(engine_factory().home)["jobs"]]
            return {"harp_work": {"type": "string", "enum": names,
                                  "description": "Use an explicitly requested HARP work type."}} if names else {}

        def description(self, config):
            return "Set harp_work only when the user named a configured work type."

        def route_task(self, task, config):
            default = {key: value for key, value in config.items() if key in ROUTE_KEYS}
            engine = engine_factory()
            rules = settings(engine.home)
            explicit = task.get("harp_work")
            job = next((item for item in rules["jobs"] if item["name"] == explicit), None)
            if job:
                source = "manual"
            else:
                job, source = select(str(task.get("goal") or ""), rules, engine)
            if job:
                route = {"provider": job["provider"], "model": job["model"], "reasoning_effort": job["effort"]}
                name = job["name"]
            else:   # no saved work type: the same cost ladder as the main chat (heavyweight, quota, tier)
                route = ladder_route(str(task.get("goal") or ""), engine)
                if not route:
                    return default
                source, name = "ladder", "Cost ladder"
            route, source = api_fallback(route, source, engine)
            try:
                write(engine.home, "harp-delegation-latest.json", {**route, "job": name, "source": source})
            except OSError:
                pass
            return route

    return HarpDelegation()
