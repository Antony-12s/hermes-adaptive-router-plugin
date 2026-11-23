"""HARP cost ladder: map a difficulty tier to the cheapest (model, effort) that should handle it.

Cheap models think harder before work moves to a pricier model. Each tier has one rung per provider, and the
rungs rise in estimated cost per task. Prices and effort names come from the providers' official model pages
(checked 2026-11-22). The effort-to-token multipliers are estimates, not published numbers: higher effort
makes a model write more reasoning tokens, and a third-party Opus 5.5 benchmark measured about 1x, 1.9x, 2.6x
and 6.7x at low, medium, high and xhigh. Max is an assumption. Adjust them in EFFORT_TOKENS.

Pure logic, no Hermes imports. Not yet wired into the live router.
"""

import math

EFFORTS = ("low", "medium", "high", "xhigh", "max")
ANTHROPIC, CODEX = "anthropic", "openai-codex"
TIERS = ("trivial", "easy", "routine", "hard", "very hard", "extreme", "maximum")

# Output price in USD per million tokens. Output dominates cost because reasoning tokens bill as output.
OUTPUT_PRICE = {
    "claude-haiku-5-5": 0.5, "claude-sonnet-5-5": 10, "claude-opus-5-5": 20, "claude-fable-5-2": 40,
    "gpt-6-luna": 0.5, "gpt-6.1-terra": 5, "gpt-6.2-sol": 10, "gpt-6.2-astra": 40,
}
EFFORT_TOKENS = {"low": 1.0, "medium": 1.9, "high": 2.6, "xhigh": 6.7, "max": 10.0}

# One rung per tier, cheapest first. Both providers cover all seven tiers.
RUNGS = {
    ANTHROPIC: (
        ("claude-haiku-5-5", "low"), ("claude-haiku-5-5", "high"), ("claude-sonnet-5-5", "medium"),
        ("claude-opus-5-5", "medium"), ("claude-opus-5-5", "xhigh"), ("claude-fable-5-2", "xhigh"),
        ("claude-fable-5-2", "max"),
    ),
    CODEX: (
        ("gpt-6-luna", "low"), ("gpt-6-luna", "high"), ("gpt-6.1-terra", "high"),
        ("gpt-6.2-sol", "high"), ("gpt-6.2-sol", "xhigh"), ("gpt-6.2-astra", "xhigh"),
        ("gpt-6.2-astra", "max"),
    ),
}
# The highest tier each mode may reach.
MODE_TOP_TIER = {"save": 2, "balanced": 4, "best": 6}
MODE_SURE = {"save": 0.55, "balanced": 0.70, "best": 0.85}
DEMANDING_TIER = 3        # a heavyweight preference applies from this tier up
# Softmax temperature of the tier learner, chosen by cross-validation on the training prompts (training/train.py).
# At the router's default of 60 the learner was sure about only 40 of 100 held-out prompts and over-routed 37.
LEARNER_TEMPERATURE = 400.0
UNSURE_TIER = 3           # where an unsure learner with no information lands: Opus at medium
COMPARE_TIER = 2          # from this tier up, the provider with more quota left runs the job
LABELS = tuple(str(t) for t in range(len(TIERS)))
# What each tier means. They seed a fresh learner and become Jev's answer options.
TIER_HINTS = (
    "Trivial: a greeting, small talk, a weather lookup or a one-line factual answer",
    "Easy: a short summary, translation, rewrite or status update from text already given",
    "Routine: an explanation, an email draft, a simple test or a database query",
    "Hard: debugging, building or refactoring a feature, code review, build or CI errors",
    "Very hard: a deep investigation, a system design or a detailed plan with several parts",
    "Extreme: a large design or investigation that spans many systems, with many parts",
    "Maximum: the hardest open-ended reasoning, only when cheaper tiers have failed",
)



def cost(model, effort):
    """Relative cost of one task: output price times the reasoning-token multiplier."""
    return OUTPUT_PRICE[model] * EFFORT_TOKENS[effort]


def rung(provider, tier):
    model, effort = RUNGS[provider][tier]
    return {"provider": provider, "model": model, "effort": effort}


def tier_of_model(model):
    """The lowest tier whose rung uses this model on either provider, or None."""
    return next((t for t in range(len(TIERS)) if any(RUNGS[p][t][0] == model for p in RUNGS)), None)


def tier_of_route(model, effort):
    """The tier of an exact (model, effort) rung, else the lowest tier that uses the model."""
    exact = next((t for t in range(len(TIERS)) if any(RUNGS[p][t] == (model, effort) for p in RUNGS)), None)
    return exact if exact is not None else tier_of_model(model)


def tier_of_label(label):
    """A user's correction: a tier number or a "provider:model" id."""
    label = str(label).strip()
    if label in LABELS:
        return int(label)
    tier = tier_of_model(label.split(":", 1)[-1])
    if tier is None:
        raise ValueError(f"{label!r} is not a tier or a ladder model")
    return tier


def cost_of(route):
    return cost(route["model"], route["effort"])


# --------------------------------------------------------------------------- provider
def is_hot(load, limit):
    """True when every account of the provider is at or above the usage limit."""
    if not load:
        return False
    if load.get("pool_has_room"):
        return False
    return bool(load.get("pool_near_limit") or load.get("used", 0) >= limit)


def heavyweight_provider(heavyweight):
    """The setting is a candidate id such as "anthropic:claude-opus-5-5". Only its provider matters here."""
    provider = (heavyweight or "").split(":", 1)[0]
    return provider if provider in RUNGS else ""


def pick_provider(tier, heavyweight, loads, limit):
    """Choose the provider for one job.

    heavyweight: "", ANTHROPIC or CODEX. A preference applies to demanding tiers while the preferred
    provider has room. Otherwise, from the routine tier up, the provider with more weighted quota left runs
    the job, because cost no longer matters much below that. Easy work stays on Anthropic. A provider with no
    room is skipped. loads: {provider: load} as built from fresh quota readings; a missing provider has
    unknown quota and is never compared.
    """
    base, other = ANTHROPIC, CODEX

    def hot(provider):
        return is_hot(loads.get(provider), limit)

    if heavyweight and tier >= DEMANDING_TIER:
        rival = ANTHROPIC if heavyweight == CODEX else CODEX
        if not hot(heavyweight):
            return heavyweight
        return rival if not hot(rival) else base
    if tier >= COMPARE_TIER:
        a, c = loads.get(ANTHROPIC), loads.get(CODEX)
        if a and c and not hot(ANTHROPIC) and not hot(CODEX):
            return CODEX if c["left"] > a["left"] else ANTHROPIC
    if hot(base) and not hot(other):
        return other
    return base


# --------------------------------------------------------------------------- tier
def tier_from_probs(probs, confidence, mode="balanced"):
    """Return (tier, unsure, expected) from probabilities over the tiers and the learner's confidence."""
    expected = sum(int(label) * p for label, p in probs.items())
    unsure = confidence < MODE_SURE[mode]
    if unsure:
        # Lean one tier up from what the learner expects, but trust that estimate only as far as it is confident.
        # A learner that knows nothing lands on UNSURE_TIER instead of a tier picked from noise.
        weight = min(1.0, confidence / MODE_SURE[mode])
        tier = math.ceil(weight * expected + (1 - weight) * UNSURE_TIER - 1e-9)
    else:
        tier = round(expected)
    return min(tier, MODE_TOP_TIER[mode], len(TIERS) - 1), unsure, expected


def predict_tier(learner, text, mode="balanced", seeds=0):
    """Return (tier, confidence, unsure, expected). Unsure picks lean one rung up, not to the top.

    learner is a router.Learner trained with the labels "0".."6". The mode sets the highest tier allowed.
    """
    probs, confidence = learner.predict(text, [str(t) for t in range(len(TIERS))], seeds)
    tier, unsure, expected = tier_from_probs(probs, confidence, mode)
    return tier, confidence, unsure, expected


def jev_questions(examples=lambda label: []):
    """Jev answers one choice question: which tier does this message need?"""
    criteria = {}
    for label, hint in zip(LABELS, TIER_HINTS):
        shown = examples(label)
        criteria[label] = {"what": hint, "examples": shown} if shown else hint
    return {"tier": {"type": "choice", "instructions": "How demanding is it to answer this user message well?",
                     "criteria": criteria}}


def read_jev(answers):
    """Jev's answer as {"probs", "confidence"} over the tiers, or None when it is unusable."""
    answer = answers.get("tier") or {}
    probs = {k: float(v) for k, v in (answer.get("probabilities") or {}).items() if k in LABELS}
    if not probs or answer.get("choice") not in LABELS:
        return None
    return {"probs": probs, "confidence": float(answer.get("confidence") or 0)}


def decide(text, learner, mode, heavyweight, loads, limit, seeds=0, tier=None, jev=None):
    """Full decision: tier from Jev, the learner or the caller, provider from quota, then the rung.

    heavyweight is a provider ("anthropic", "openai-codex") or ""."""
    if tier is not None:
        tier, confidence, unsure, expected = min(tier, MODE_TOP_TIER[mode]), 1.0, False, float(tier)
        probs = {label: float(int(label) == tier) for label in LABELS}
    else:
        if jev:
            probs, confidence = jev["probs"], jev["confidence"]
        else:
            probs, confidence = learner.predict(text, list(LABELS), seeds)
        tier, unsure, expected = tier_from_probs(probs, confidence, mode)
    provider = pick_provider(tier, heavyweight, loads, limit)
    base = ANTHROPIC
    return {**rung(provider, tier), "tier": tier, "tier_name": TIERS[tier], "confidence": round(confidence, 3),
            "unsure": unsure, "expected": round(expected, 2), "probs": {k: round(v, 3) for k, v in probs.items()},
            "moved_from": None if provider == base else {"provider": base, **rung(base, tier)}}
