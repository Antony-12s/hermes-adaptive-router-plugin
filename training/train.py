"""Explicit offline training and evaluation for HARP's synthetic routing dataset.

The local learner predicts a difficulty tier (0 trivial to 6 maximum). The cost ladder turns the tier into a
model and effort, and the quota policy picks the provider. The learner's softmax temperature is chosen by
cross-validation on the training rows, grouped by prompt template, so the held-out rows are scored once.
"""

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from dashboard import ladder, router
from training.generate_dataset import expected_route

DATASET = Path(__file__).with_name("routing-seeds.jsonl")
EXTRA = Path(__file__).with_name("tier-extra.jsonl")    # tier-only rows (no quota scenario), see README
LABELS = [str(t) for t in range(len(ladder.TIERS))]
TEMPERATURES = (10, 20, 30, 60, 100, 150, 250, 400, 800)
FOLDS = 5
UNDER_PENALTY, OVER_PENALTY = 2.0, 1.0      # a weak model on hard work is worse than a strong one on easy work
BASELINES = {                                # fixed routes to compare cost against
    "always Opus medium": ("claude-opus-5-5", "medium"),    # cheap, but too weak for the hardest tiers
    "always Opus xhigh": ("claude-opus-5-5", "xhigh"),      # strong enough for every tier in the dataset
}


def load_rows(path=DATASET):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def load_extra(path=EXTRA):
    return load_rows(path) if Path(path).exists() else []


PROBES = (("เสร็จยัง", 0), ("ได้ยินไหม", 0), ("ขอบคุณมาก", 0), ("thanks!", 0), ("ถึงไหนแล้ว", 0),
          ("สรุปให้หน่อย", 1), ("สลับที่กัน", 2), ("ลดลงเหลือ 4 พอ", 2),
          ("แก้บั๊กปุ่มบันทึกส่งคำขอสองครั้ง", 3), ("ออกแบบระบบ sync ข้ามหลายเครื่องพร้อมแผน migration", 4))


def build_loads(scenario):
    """Account usage -> the load dict HARP builds from fresh quota readings."""
    loads = {}
    for provider, accounts in scenario["accounts"].items():
        peaks = {f"{provider}-{i}": a["peak"] for i, a in enumerate(accounts)}
        plans = {f"{provider}-{i}": a["plan"] for i, a in enumerate(accounts)}
        load = router.provider_load(peaks, plans)
        load["pool_near_limit"] = min(peaks.values()) >= scenario["limit"]
        load["pool_has_room"] = min(peaks.values()) < scenario["limit"]
        loads[provider] = load
    return loads


def target(row, mode):
    """The tier and route the dataset expects once the mode's ceiling applies."""
    tier = min(row["tier"], ladder.MODE_TOP_TIER[mode])
    scenario = row["scenario"]
    route = expected_route(tier, scenario["heavyweight"], scenario["accounts"])
    return tier, route


def train_learner(rows):
    learner = router.Learner()
    for row in rows:
        learner.learn(row["prompt"], str(row["tier"]), group=row["task_group"])
    return learner


def scores(learner, text):
    """Cosine score per tier. These do not depend on temperature, so cross-validation computes them once."""
    return learner.label_scores(text, LABELS)


def probs_at(score, temperature):
    top = max(score.values())
    exp = {label: math.exp(temperature * (value - top)) for label, value in score.items()}
    total = sum(exp.values())
    return {label: value / total for label, value in exp.items()}


def tier_loss(predicted, truth):
    return UNDER_PENALTY * (truth - predicted) if predicted < truth else OVER_PENALTY * (predicted - truth)


def pick_temperature(train_rows, mode):
    """Grouped cross-validation: every prompt template stays in a single fold."""
    templates = sorted({r["template"] if r["template"] != "seed" else f"seed:{r['task_group']}:{r['language']}"
                        for r in train_rows})
    fold_of = {t: i % FOLDS for i, t in enumerate(templates)}
    key = lambda r: fold_of[r["template"] if r["template"] != "seed" else f"seed:{r['task_group']}:{r['language']}"]
    cached = []
    for fold in range(FOLDS):
        learner = train_learner([r for r in train_rows if key(r) != fold])
        for row in (r for r in train_rows if key(r) == fold):
            cached.append((scores(learner, row["prompt"]), learner.samples, min(row["tier"], ladder.MODE_TOP_TIER[mode])))
    results = {}
    for temperature in TEMPERATURES:
        loss = 0.0
        for score, samples, truth in cached:
            probs = probs_at(score, temperature)
            confidence = router.confidence(probs) * min(1.0, samples / router.MIN_SAMPLES)
            tier, _, _ = ladder.tier_from_probs(probs, confidence, mode)
            loss += tier_loss(tier, truth)
        results[temperature] = loss / len(cached)
    return min(results, key=results.get), results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=ladder.MODE_TOP_TIER, default="balanced")
    parser.add_argument("--dataset", default=str(DATASET))
    parser.add_argument("--temperature", type=float, default=None, help="skip cross-validation and use this value")
    parser.add_argument("--show", type=int, default=5, help="how many mismatches to print")
    parser.add_argument("--no-extra", action="store_true", help="train on routing-seeds.jsonl only")
    args = parser.parse_args()
    rows = load_rows(args.dataset)
    extra = [] if args.no_extra else load_extra()
    train_rows = [r for r in rows + extra if r["split"] == "train"]
    held_out = [r for r in rows if r["split"] == "evaluation"]

    if args.temperature:
        temperature, cv = args.temperature, None
    else:
        temperature, cv = pick_temperature(train_rows, args.mode)
    router.Learner.TEMPERATURE = temperature
    learner = train_learner(train_rows)

    print(f"Trained on {len(train_rows)} prompts in memory. Mode: {args.mode}.")
    if cv:
        print("Temperature chosen by grouped cross-validation on the training rows (loss, lower is better):")
        print("  " + "  ".join(f"{t}: {loss:.2f}" + ("*" if t == temperature else "") for t, loss in cv.items()))

    exact = near = under = over = sure = route_hits = 0
    cost_ladder = cost_oracle = 0.0
    by_tier = defaultdict(lambda: [0, 0])
    misses = []
    for row in held_out:
        scenario = row["scenario"]
        truth, want = target(row, args.mode)
        decision = ladder.decide(row["prompt"], learner, args.mode, scenario["heavyweight"], build_loads(scenario),
                                 scenario["limit"])
        tier = decision["tier"]
        exact += tier == truth
        near += abs(tier - truth) <= 1
        under += tier < truth
        over += tier > truth
        sure += not decision["unsure"]
        hit = {k: decision[k] for k in ("provider", "model", "effort")} == want
        route_hits += hit
        by_tier[truth][0] += tier == truth
        by_tier[truth][1] += 1
        cost_ladder += ladder.cost(decision["model"], decision["effort"])
        cost_oracle += ladder.cost_of(want)
        if not hit:
            misses.append((row["id"], scenario["case"], row["prompt"][:50], f"tier {truth}->{tier}", want["model"],
                           want["effort"], "got", decision["model"], decision["effort"]))
    n = len(held_out)
    print(f"Held-out tier exactly right: {exact}/{n}, within one tier: {near}/{n}")
    print(f"  too weak (under-routed): {under}/{n}, too strong (over-routed): {over}/{n}, learner sure: {sure}/{n}")
    for tier in sorted(by_tier):
        hit, total = by_tier[tier]
        print(f"  tier {tier} {ladder.TIERS[tier]}: {hit}/{total}")
    print(f"Held-out provider, model and effort right under quota scenarios: {route_hits}/{n}")
    print(f"Estimated cost per prompt (relative units): ladder {cost_ladder / n:.1f}, perfect tiers {cost_oracle / n:.1f}")
    for name, fixed in BASELINES.items():
        fixed_cost = ladder.cost(*fixed)
        print(f"  {name}: {fixed_cost:.1f}, so the ladder costs {100 * (cost_ladder / n / fixed_cost - 1):+.0f}%")
    for miss in misses[:args.show]:
        print("  miss", *miss)

    extra_held = [r for r in extra if r["split"] == "evaluation"]
    if extra_held:
        hits, near_hits, per = 0, 0, defaultdict(lambda: [0, 0])
        for row in extra_held:
            tier = ladder.predict_tier(learner, row["prompt"], args.mode)[0]
            truth = min(row["tier"], ladder.MODE_TOP_TIER[args.mode])
            hits += tier == truth
            near_hits += abs(tier - truth) <= 1
            per[truth][0] += tier == truth
            per[truth][1] += 1
        print(f"Extra held-out (tier only): exactly right {hits}/{len(extra_held)}, within one tier {near_hits}/{len(extra_held)}")
        print("  " + "  ".join(f"tier {t}: {h}/{n}" for t, (h, n) in sorted(per.items())))
    print("Probes: " + "  ".join(f"{text} {want}->{ladder.predict_tier(learner, text, args.mode)[0]}" for text, want in PROBES))

    # Provider policy alone: every row, perfect tier.
    policy_hits, cases, policy_misses = 0, defaultdict(lambda: [0, 0]), []
    for row in rows:
        scenario = row["scenario"]
        truth, want = target(row, args.mode)
        got = ladder.decide(row["prompt"], learner, args.mode, scenario["heavyweight"], build_loads(scenario),
                            scenario["limit"], tier=truth)
        hit = {k: got[k] for k in ("provider", "model", "effort")} == want
        policy_hits += hit
        cases[scenario["case"]][0] += hit
        cases[scenario["case"]][1] += 1
        if not hit:
            policy_misses.append((row["id"], scenario["case"], want, got["provider"], got["model"], got["effort"]))
    print(f"Provider policy with perfect tiers: {policy_hits}/{len(rows)}")
    for case, (hit, total) in sorted(cases.items()):
        print(f"  {case}: {hit}/{total}")
    for miss in policy_misses[:args.show]:
        print("  miss", *miss)


if __name__ == "__main__":
    main()
