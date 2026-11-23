# HARP routing dataset

[routing-seeds.jsonl](routing-seeds.jsonl) contains 500 synthetic prompts, split evenly between Thai and
English. There are 20 task groups with 25 prompts each: 20 for training and 5 held out for evaluation.
The first 100 prompts come from the original seed set and keep their ids. The other 400 are built by
[generate_dataset.py](generate_dataset.py) from templates and slot pools with a fixed seed. Evaluation
prompts use templates that never appear in training, so the held-out score measures unfamiliar phrasing.

## The cost ladder

The learner predicts a **difficulty tier** from 0 to 6. [dashboard/ladder.py](../dashboard/ladder.py) turns the
tier into a model and effort. Cheap models think harder before work moves to a pricier model, so an easy prompt
no longer lands on Opus or GPT-6.2 Sol.

| Tier | Anthropic | Codex | Relative cost |
| --- | --- | --- | --- |
| 0 trivial | Haiku 5.5, low | GPT-6 Luna, low | 0.5 |
| 1 easy | Haiku 5.5, high | GPT-6 Luna, high | 1.3 |
| 2 routine | Sonnet 5.5, medium | GPT-6.1 Terra, high | 19 / 13 |
| 3 hard | Opus 5.5, medium | GPT-6.2 Sol, high | 38 / 26 |
| 4 very hard | Opus 5.5, xhigh | GPT-6.2 Sol, xhigh | 134 / 67 |
| 5 extreme | Fable 5.2, xhigh | GPT-6.2 Astra, xhigh | 268 |
| 6 maximum | Fable 5.2, max | GPT-6.2 Astra, max | 400 |

Effort levels are low, medium, high, xhigh, and max. Both providers' official model pages list all five for
these models. Relative cost is the output price per million tokens (Haiku 5.5 $0.50, Sonnet 5.5 $10, Opus 5.5
$20, Fable 5.2 $40, GPT-6 Luna $0.50, GPT-6.1 Terra $5, GPT-6.2 Sol $10, GPT-6.2 Astra $40) times an estimated reasoning-token
multiplier for the effort. The multipliers (low 1, medium 1.9, high 2.6, xhigh 6.7) come from one third-party
Opus 5.5 benchmark, and max (10) is an assumption. They are estimates, not published numbers. Edit
`EFFORT_TOKENS` and `RUNGS` in `ladder.py` to change the ladder; the dataset stores tiers, not model names.

Sources, checked 2026-11-22: [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing),
[Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort),
[OpenAI pricing](https://developers.openai.com/api/docs/pricing),
[OpenAI models](https://developers.openai.com/api/docs/models). Every model in the ladder also appears in this
machine's Hermes model list. The Codex model table could not be read in full, so confirm that your ChatGPT
plan can use GPT-6 Luna, GPT-6.1 Terra and GPT-6.2 Astra before relying on those rungs. OpenAI describes
Terra as a mid-priced model for images, screenshots, and computer use; it sits at tier 2 because of its price,
and its coding quality is not verified here.

## What each row teaches

| Field | Meaning |
| --- | --- |
| `tier` | Difficulty from 0 to 6. The local learner is trained on this. |
| `route_class` | `coding` (either provider, quota decides), `analysis`, `everyday`, or `light` (Anthropic first). |
| `template` | The prompt template, so cross-validation can keep one template in one fold. |
| `scenario` | Account usage per provider, plan weights, and an optional heavyweight preference. A test input, not trained on. |
| `scenario.expected_route` | The provider, model, and effort HARP should choose in that scenario. |

| Task groups | Tier |
| --- | --- |
| weather, chitchat, quick_fact | 0 |
| status_update, summarize, translate_rewrite | 1 |
| tests, sql_data, explain_concept, email_writing | 2 |
| debug_ui, debug_backend, implement_feature, refactor, build_error, code_review, devops_ci | 3 |
| complex_investigation, architecture_design, planning_analysis | 4 |

A coding or analysis prompt with three or more kinds of work (investigate, fix, test, and so on) is one tier
harder than its group. No prompt reaches tier 6, so max effort and the Fable and Astra rungs are never
exercised by this dataset. Add prompts that truly need them before tuning those rungs.

## Provider policy

The provider is chosen by quota and plan weights, never learned from the prompt:

1. **Coding, heavyweight on Auto:** the provider with more weighted quota left runs it. Weighted quota is the
   sum over accounts of plan weight (1×, 5×, 10×, 20×) times the share still unused.
2. **Heavyweight preference (tier 3 and up):** the preferred provider runs it while it has room, even if the
   other provider has more quota left. If it has no room, the other provider runs it. If neither has room,
   the default stays.
3. **Everything else:** Anthropic runs it. If Anthropic has no room, Codex runs it.
4. **No room** means every account in that provider is at or above the limit (85% in this dataset).
5. **Missing quota data** means no comparison. The default stays unless it has no room.

The mode sets the highest tier: `save` stops at tier 2, `balanced` at tier 4, and `best` at tier 6.

Every row carries one of 13 scenario cases (quota ahead for either provider, plan weights that flip the winner,
one or both providers out of room, no or partial quota data, and preferences with healthy or exhausted
providers). Each work type sees every case.

## Extra tier rows

[tier-extra.jsonl](tier-extra.jsonl) adds about 1,440 prompts that teach the tier only. They have no quota
scenario, so the provider policy checks above still use the 500 rows alone. They were written by language
models in six batches, each told to imitate real chat with a coding assistant: status pings and thanks
(`status_ack`, tier 0), short follow-up edits like "สลับที่กัน" (`followup_edit`, tiers 1-2), questions and
writing (tiers 0-2), hard coding work (tier 3), deep investigations and designs (tier 4), and messy input with
typos or text typed on the wrong keyboard layout (`messy`, tiers 0-3). Duplicates of the 500 rows were dropped.
Rows written together share a `template` (ten rows each) and every fifth template is held out, so the extra
held-out score also measures unfamiliar batches. `provenance` is `synthetic-llm`; the tiers were set by the
writing model and spot-checked, not reviewed row by row.

Bare option picks and go-aheads ("1", "ข้อ 2", "ok", "ต่อเลย", "ทำหมดเลย") are left out on purpose. Their cost
is whatever the previous turn offered, so the router gives them the tier of the same session's last decision
(within six hours) instead of guessing from the words; see `router.is_followup`.

The learner keeps one centroid per task group (a prototype) and a tier scores as its best prototype. With one
centroid per tier, short edit commands were averaged with long explanations in tier 2 and a short Thai command
landed nearer the trivial centroid.

## Run training explicitly

From the repository root with Python 3 installed, run:

```sh
python -m training.train --mode balanced
```

The command trains a temporary local learner on the 400 training prompts plus the extra training rows
(`--no-extra` for the 400 alone). It first picks the learner's softmax
temperature by 5-fold cross-validation on the training rows, grouped by template, so the 100 held-out prompts
are scored once and never used for tuning. The loss counts a prompt sent to too weak a model as twice as bad
as one sent to too strong a model. It runs offline, keeps the learner in memory, and leaves Hermes profiles and
learning history untouched. The HARP plugin never loads this dataset automatically.

The report covers:

1. **Tier accuracy:** exact and within one tier, how many prompts went to a model that was too weak or too
   strong, and the learner's confidence.
2. **Provider, model, and effort under each row's quota scenario:** the learner and the quota policy together.
3. **Estimated cost per prompt:** against the same prompts with perfect tiers, and against fixed routes (always
   Opus medium, always Opus xhigh).
4. **Provider policy with perfect tiers:** every row routed as if the tier were known. It should match all 500.

Add `--temperature 400` to skip cross-validation. When the learner is unsure it leans one tier up instead of
jumping to the strongest model.

## See every routing decision

```sh
python -m training.report
```

This trains the learner, runs all 500 prompts through it, and writes `training/routing-report.html`. For each
prompt the report shows the input (text and account usage), the learner's tier and agent role, the provider,
model, effort, and estimated cost it was sent to, and the sub-agents the planner would use. Filter by split,
group, provider, effort, result, or sub-agent count.

The sub-agent planner (`training/simulate.py`) is an offline estimate: it counts the kinds of work in a prompt,
splits demanding coding or analysis jobs into at most four sub-agents, and routes each one by its role and
tier. Everyday and light prompts are never split, and the count is capped at two when only one provider has
room. HARP does not split work itself; Hermes decides that, and HARP routes each delegated goal.

## Seed a profile

```sh
python -m training.seed --dry-run
python -m training.seed
```

A fresh HARP install knows nothing, so every message lands on tier 3 (Opus 5.5 at medium) until Jev or your
corrections teach it. `training.seed` copies the 400 training prompts and the extra training rows into your Hermes profile's router database
as low-weight samples marked `seed`, so the learner starts with a spread of easy and hard work. Held-out prompts
are never copied, repeating the command adds nothing, and the plugin never runs it on its own. The prompts are
synthetic, so your own corrections and verified runs outweigh them. Pass `--home` to use another profile.

## Limits

The dataset is synthetic. Templates make prompts consistent, so scores here are higher than on real traffic.
Mix in reviewed prompts from real use and keep `provenance` accurate before trusting the numbers. The live router uses the
ladder by default (`ladder` in `harp-router.json`; set it to `false` to route among your own candidate models).
