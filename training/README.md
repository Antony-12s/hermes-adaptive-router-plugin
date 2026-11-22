# HARP routing seed dataset

[routing-seeds.jsonl](routing-seeds.jsonl) contains 100 synthetic prompts. It
includes 60 coding prompts and 40 general prompts, split evenly between Thai
and English. Each of the ten task groups has eight training prompts and two
held-out evaluation prompts.

Each row has a prompt, task group, language, and preferred provider, model, and
effort. Coding examples also specify a Codex fallback for cases where the
Anthropic route is unavailable or has insufficient quota. General examples use
Haiku at Low effort. Routine coding uses Opus at Medium effort, and complex
investigations may use High if the active mode allows it.

For evaluation, compare the recommended provider, model, and effort with the
expected route under sufficient quota and supported models. Weather examples
assume Hermes can look up current weather for the relevant location. Status
updates assume the requested information is already present. Keep the evaluation
split out of training so it tests unfamiliar phrasings within each group.

## Run training explicitly

From the repository root with Python 3 installed, run:

```sh
python -m training.train --mode balanced
```

The command trains a temporary local learner on the 80 training prompts and
reports model and full-route matches on the 20 held-out prompts. It runs
offline, keeps the trained learner in memory for that run, and leaves Hermes
profiles and learning history untouched. The HARP plugin never loads this
dataset automatically.
