"""Out-of-the-box setup HARP can do for a new install: a cross-provider fallback chain and the HARP Timer.

Hermes falls back to another provider when a model refuses (safety filter), errors or runs out, but
only through `fallback_providers` in config.yaml, which starts empty. HARP fills it with the other
providers the user is already signed in to with OAuth, so nothing new has to be configured.
"""

from pathlib import Path

try:
    from . import ladder
except ImportError:  # loaded by file path (plugin_api's _sibling) or as a top-level test module
    import importlib.util as _util
    import sys as _sys

    _key = "harp_ladder"   # same key as the plugin's _sibling(), so both share one module
    if _key not in _sys.modules:
        _spec = _util.spec_from_file_location(_key, Path(__file__).with_name("ladder.py"))
        _sys.modules[_key] = _util.module_from_spec(_spec)
        _spec.loader.exec_module(_sys.modules[_key])
    ladder = _sys.modules[_key]

FALLBACK_TIER = 3                               # a capable rung: the fallback takes over real work
NOUS_MODEL = "google/gemini-3.1-pro-preview"    # through Nous Portal (OAuth device login)


def candidates():
    """(provider, model) in the order HARP offers them. Claude and Codex use the cost ladder's rung."""
    return [(provider, ladder.RUNGS[provider][FALLBACK_TIER][0]) for provider in (ladder.CODEX, ladder.ANTHROPIC)] \
        + [("nous", NOUS_MODEL)]


def signed_in(entries):
    return bool(entries) and any(getattr(e, "last_status", None) != "dead" for e in entries)


def plan(primary, chain, pool_for):
    """The chain with every signed-in provider that is neither the primary nor already in it appended.
    pool_for(provider) -> its credential pool entries (or None)."""
    chain = [dict(entry) for entry in chain or []]
    taken = {primary} | {entry.get("provider") for entry in chain}
    for provider, model in candidates():
        if provider not in taken and signed_in(pool_for(provider)):
            chain.append({"provider": provider, "model": model})
            taken.add(provider)
    return chain


def ensure_timer(needed, timer, home):
    """Install the HARP Timer when a setting needs it and it is missing (auto-balance is on by default,
    so a fresh install needs it). Never raises: True when installed by this call."""
    try:
        if needed and not timer.installed():
            timer.install(home)
            return True
    except Exception:  # noqa: BLE001 - setup must never break a page load
        return False
    return False
