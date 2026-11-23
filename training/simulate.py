"""Offline simulation: run every dataset prompt through the local learner and a sub-agent planner.

For each prompt the learner predicts a difficulty tier and an agent role. The cost ladder turns the tier into
a model and effort, and the quota policy picks the provider from the row's scenario. A rule-based planner
estimates how many sub-agents the job needs and routes each one the same way.

The sub-agent planner is an offline estimate for evaluation. HARP does not split work itself; Hermes decides
that, and HARP only routes each delegated goal.
"""

from dashboard import ladder, router
from training import train
from training.generate_dataset import GROUPS
from training.work_kinds import kinds_of_work

# Agent role for each task group. The learner predicts the group, and the role and work type follow from it.
GROUP_ROLE = {
    "debug_ui": "UI debugger", "debug_backend": "Backend debugger", "implement_feature": "Feature developer",
    "tests": "Tester", "refactor": "Refactorer", "complex_investigation": "Investigator",
    "weather": "Lookup assistant", "status_update": "Reporter", "summarize": "Summarizer",
    "translate_rewrite": "Editor", "build_error": "Build fixer", "code_review": "Reviewer",
    "sql_data": "Data engineer", "devops_ci": "DevOps engineer", "architecture_design": "Architect",
    "planning_analysis": "Planner", "explain_concept": "Tutor", "email_writing": "Writer",
    "chitchat": "Companion", "quick_fact": "Fact lookup",
}
GROUP_WORK = {group: cls for group, cls, *_ in GROUPS}

SUB_ROLE = {"investigate": ("Investigator", "coding"), "fix": ("Fixer", "coding"), "build": ("Developer", "coding"),
            "test": ("Tester", "coding"), "review": ("Reviewer", "coding"), "improve": ("Refactorer", "coding"),
            "explain": ("Writer", "everyday"), "analyze": ("Analyst", "analysis")}
LOWER_TIER = {"Tester", "Reviewer", "Writer", "Verifier"}   # checking and writing need less than doing
MAX_SUB_AGENTS = 4
DEMANDING = 4                      # a predicted tier at or above this marks a demanding job
BASELINES = train.BASELINES


def plan_sub_agents(prompt, work, tier, loads, limit):
    """Return (sub-agent specs, reason). Each spec is (role, work type, tier)."""
    if work in ("everyday", "light"):
        return [], "A single answer. No split."
    open_providers = [p for p in (ladder.ANTHROPIC, ladder.CODEX) if not ladder.is_hot(loads.get(p), limit)]
    if not open_providers and loads:
        return [], "Both providers are out of room. The main agent waits."
    kinds = kinds_of_work(prompt)
    demanding = tier >= DEMANDING
    if len(kinds) <= 1 and not demanding:
        return [], f"Small job ({max(len(kinds), 1)} part). The main agent does it alone."
    count = min(MAX_SUB_AGENTS, max(2, len(kinds) + demanding))
    reason = f"{len(kinds)} kinds of work found" + (", demanding tier" if demanding else "")
    if len(open_providers) == 1 and loads and count > 2:
        count, reason = 2, reason + "; only one provider has room, so capped at 2"
    specs = [SUB_ROLE[kind] for kind in kinds[:count]]
    while len(specs) < count:
        specs.append(("Verifier", work))
    return [(role, sub_work, max(0, tier - 1) if role in LOWER_TIER else tier) for role, sub_work in specs], reason


def run(rows, mode="balanced", temperature=None):
    """Train on the training rows, then route every row. Returns (results, summary, info)."""
    train_rows = [r for r in rows if r["split"] == "train"]
    if temperature is None:
        temperature, _ = train.pick_temperature(train_rows, mode)
    router.Learner.TEMPERATURE = temperature
    tiers = train.train_learner(train_rows)
    agents = router.Learner()
    for row in train_rows:
        agents.learn(row["prompt"], row["task_group"])
    groups = sorted(GROUP_ROLE)

    results = []
    for row in rows:
        scenario = row["scenario"]
        loads = train.build_loads(scenario)
        truth_tier, want = train.target(row, mode)
        group_probs, _ = agents.predict(row["prompt"], groups)
        group = max(group_probs, key=group_probs.get)
        work = GROUP_WORK[group]
        decision = ladder.decide(row["prompt"], tiers, mode, scenario["heavyweight"], loads, scenario["limit"])
        route = {k: decision[k] for k in ("provider", "model", "effort")}
        subs, reason = plan_sub_agents(row["prompt"], work, decision["tier"], loads, scenario["limit"])
        planned = []
        for role, sub_work, sub_tier in subs:
            sub = ladder.decide(row["prompt"], tiers, mode, scenario["heavyweight"], loads, scenario["limit"],
                                tier=sub_tier)
            planned.append({"role": role, **{k: sub[k] for k in ("provider", "model", "effort")}})
        results.append({
            "id": row["id"], "split": "held-out" if row["split"] == "evaluation" else "seen in training",
            "language": row["language"], "prompt": row["prompt"], "group": row["task_group"],
            "truth": {"tier": truth_tier, "group": row["task_group"], "route": want},
            "input": {"case": scenario["case"], "heavyweight": scenario["heavyweight"] or "auto",
                      "limit": scenario["limit"], "accounts": scenario["accounts"]},
            "learner": {"tier": decision["tier"], "tier_name": ladder.TIERS[decision["tier"]],
                        "expected": decision["expected"], "confidence": decision["confidence"],
                        "unsure": decision["unsure"], "agent": GROUP_ROLE[group], "work": work},
            "route": route, "cost": round(ladder.cost_of(route), 1), "ideal_cost": round(ladder.cost_of(want), 1),
            "sub_agents": planned, "sub_reason": reason,
            "ok": {"tier": decision["tier"] == truth_tier, "near": abs(decision["tier"] - truth_tier) <= 1,
                   "agent": group == row["task_group"], "route": route == want},
        })
    return results, summarize(results), {"temperature": temperature, "mode": mode, "trained": len(train_rows)}


def summarize(results):
    def tally(items, key):
        return [sum(r["ok"][key] for r in items), len(items)]

    summary = {"total": len(results), "splits": {}}
    for name in ("held-out", "seen in training"):
        items = [r for r in results if r["split"] == name]
        summary["splits"][name] = {
            "n": len(items), "tier": tally(items, "tier"), "near": tally(items, "near"), "agent": tally(items, "agent"),
            "route": tally(items, "route"),
            "under": [sum(r["learner"]["tier"] < r["truth"]["tier"] for r in items), len(items)],
            "over": [sum(r["learner"]["tier"] > r["truth"]["tier"] for r in items), len(items)],
            "sure": [sum(not r["learner"]["unsure"] for r in items), len(items)]}
    count = lambda values: dict(sorted(((k, values.count(k)) for k in set(values)), key=lambda kv: -kv[1]))
    summary["providers"] = count([r["route"]["provider"] for r in results])
    summary["models"] = count([r["route"]["model"] for r in results])
    summary["efforts"] = dict(sorted(count([r["route"]["effort"] for r in results]).items(),
                                     key=lambda kv: ladder.EFFORTS.index(kv[0])))
    summary["tiers"] = {f"{t} {ladder.TIERS[t]}": sum(r["learner"]["tier"] == t for r in results)
                        for t in range(len(ladder.TIERS))}
    summary["sub_agents"] = {str(k): v for k, v in sorted(count([len(r["sub_agents"]) for r in results]).items())}
    summary["sub_calls"] = sum(len(r["sub_agents"]) for r in results)
    held = [r for r in results if r["split"] == "held-out"]
    summary["cost"] = {"ladder": sum(r["cost"] for r in held) / len(held),
                       "perfect": sum(r["ideal_cost"] for r in held) / len(held),
                       "baselines": {name: ladder.cost(*fixed) for name, fixed in BASELINES.items()}}
    return summary
