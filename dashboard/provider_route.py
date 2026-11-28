"""Run each turn of a session on the `harp` provider on the rung HARP picks.

The user picks HARP in the model picker (`harp-save`, `harp-balanced`, `harp-best`; the model is the mode).
HARP's pre_llm_call hook decides the rung, then binds the real provider onto the agent for that one turn
with Hermes' own per-turn route swap (the one the voice-chat model and the fallback chain use).
`restore_primary_runtime` puts `harp` back when the next turn starts. If the bind fails, the turn stays on
the `harp` provider's facade client (cheap rung, no live typing), and the chat goes on.
"""

PROVIDER = "harp"
MODES = {"harp-save": "save", "harp-balanced": "balanced", "harp-best": "best"}
MODELS = tuple(MODES)
DEFAULT_MODE = "balanced"


def mode_for(model):
    return MODES.get(str(model or "").strip().lower(), DEFAULT_MODE)


def is_harp(agent):
    return str(getattr(agent, "provider", "") or "").strip().lower() == PROVIDER


def bind_turn(agent, decision, binder):
    """Put `agent` on the decision's pick for this turn. binder(agent, provider, model) -> bool.
    Returns (bound, reason); on failure the agent and its effort are left as they were."""
    provider, _, model = str(decision.get("pick") or "").partition(":")
    if not provider or not model:
        return False, f"bad pick {decision.get('pick')}"
    try:
        if not binder(agent, provider, model):
            return False, "provider not configured"
    except Exception as exc:  # noqa: BLE001 - a failed bind keeps the turn on the facade
        return False, f"{type(exc).__name__}: {exc}"[:200]
    effort = decision.get("effort")
    if effort:
        agent.reasoning_config = {"enabled": effort != "off", "effort": effort}
    return True, ""


def hermes_binder(agent, provider, model):
    """Hermes' per-turn route swap, as `agent.voice_turn_route` uses it (private Hermes API, checked
    against Hermes v0.23.3). Bind-or-nothing: a failed swap reinstalls the captured runtime."""
    from agent.route_binding import bind_route_entry
    from agent.voice_turn_route import _capture, _reinstall

    state = _capture(agent)
    try:
        if bind_route_entry(agent, {"provider": provider, "model": model}, provider, model,
                            commit_pool_choice=False) is None:
            _reinstall(agent, state)
            return False
    except Exception:
        _reinstall(agent, state)
        raise
    choice = getattr(agent, "_route_pool_choice", None)
    if choice is not None:
        choice.commit()          # this rung takes the turn: its pool key is now in use
    from agent.chat_completion_helpers import rewrite_prompt_model_identity

    rewrite_prompt_model_identity(agent, agent.model, agent.provider)
    return True
