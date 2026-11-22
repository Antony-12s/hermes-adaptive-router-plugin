"""Explicit offline training and evaluation for HARP's synthetic seed dataset."""

import argparse
import json
from pathlib import Path

from dashboard import router


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=router.MODES, default="balanced")
    args = parser.parse_args()
    dataset = Path(__file__).with_name("routing-seeds.jsonl")
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]

    candidates = [*router.DEFAULTS["candidates"],
                  {"provider": "anthropic", "model": "claude-haiku-5-5", "cap": "low"}]
    settings = {**router.DEFAULTS, "candidates": candidates, "mode": args.mode}
    learner, difficulty = router.Learner(), router.Learner()
    descriptions = 0
    for candidate in candidates:
        if candidate.get("description"):
            learner.learn(candidate["description"], router.candidate_id(candidate))
            descriptions += 1

    training_count = 0
    for row in rows:
        if row["split"] == "train":
            route = row["preferred_route"]
            learner.learn(row["prompt"], router.candidate_id(route))
            difficulty.learn(row["prompt"], str(router.EFFORTS.index(route["effort"])))
            training_count += 1

    evaluated = matched_model = matched_route = 0
    for row in rows:
        if row["split"] == "evaluation":
            decision = router.decide(row["prompt"], settings, learner, difficulty, {}, seeds=descriptions)
            expected = row["preferred_route"]
            model_matches = decision["pick"] == router.candidate_id(expected)
            effort = min(router.EFFORTS.index(expected["effort"]), router.MODES[args.mode]["cap"])
            matched_model += model_matches
            matched_route += model_matches and decision["effort"] == router.EFFORTS[effort]
            evaluated += 1

    print(f"Trained on {training_count} prompts in memory. Mode: {args.mode}.")
    print(f"Held-out model matches: {matched_model}/{evaluated}")
    print(f"Held-out provider, model, and effort matches: {matched_route}/{evaluated}")


if __name__ == "__main__":
    main()
