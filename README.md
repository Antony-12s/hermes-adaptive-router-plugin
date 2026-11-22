# Hermes Adaptive Router Plugin (HARP)

HARP routes short, text-only Chat Completions requests from a configured primary
model to a faster model on the same provider. It applies the limit to the entire
text history, including system messages. Requests with tools, multimodal input,
structured output, or a different model or provider follow Hermes' default route.
Sessions that attach tools to every request will continue using the default model.
Set `allow_tools` to `true` to route initial text requests that advertise tools
when both models support the same tool definitions. Requests containing tool
results or previous tool calls still use Hermes' selected model.

## Configure on Windows, Linux, or macOS

Use a Hermes Agent release that supports the
[LLM request middleware API](https://hermes-agent.nousresearch.com/docs/developer-guide/middleware).
From the HARP checkout, prepare your routing config:

```powershell
Copy-Item .\harp.example.json .\harp.json
$env:HARP_CONFIG = (Resolve-Path .\harp.json).Path
hermes plugins doctor .
```

On Linux or macOS:

```sh
cp harp.example.json harp.json
export HARP_CONFIG="$(pwd)/harp.json"
hermes plugins doctor .
```

HARP uses one plugin package for all three platforms. The optional HARP Timer
uses Windows Task Scheduler, a Linux systemd user timer, or a macOS LaunchAgent
in the signed-in desktop session. Linux and macOS need `hermes` on PATH when
registering the timer. Linux desktop alerts also need `notify-send` and a
notification service. Telegram alerts use Hermes on all three platforms.

Before running the doctor, replace the placeholder model IDs in `harp.json`
with actual models available through the same provider. Set `primary_model`
to the model ID Hermes puts in the upstream request. Choose a fast model
compatible with the same provider request options and output limits. HARP
registers its middleware only when the Hermes process has `HARP_CONFIG` set.

Install the checkout as a user plugin. For a default Hermes home, copy
`plugin.yaml`, `__init__.py`, and the `dashboard/` directory into
`~/.hermes/plugins/harp/`. For a custom
home or profile, use its plugin directory. Then run `hermes plugins enable harp`.
Start or restart the gateway with the same `HARP_CONFIG` environment variable.

Hermes owns retry and failover. If you want a backup on transient failures,
configure a distinct backup route in
[`fallback_providers`](https://hermes-agent.nousresearch.com/docs/user-guide/features/fallback-providers).
HARP scopes its override to the configured primary route, so a distinct
fallback route passes through. The alternate model receives the full input via
the configured provider.

## Account Pool and tagged usage

The **Accounts & usage** view uses Hermes' built-in credential pool and
account usage APIs. It does not require the Account Pool or Quota plugins.
The provider selector offers Anthropic, Jev by TypeSafe, and OpenAI Codex.
The account list shows labels and recorded status. Provider quota is for
the active profile's login, with no inferred mapping to individual rows.
Jev lists credentials saved under `jev` or `custom:jev`; Jev quota
fetching is unavailable. **Diagram** shows routing and usage collection.

In `harp.json`, set `daily_token_quotas` to a mapping such as
`{"work": 100000, "personal": 50000}`. The limits are local daily token
budgets and reset in the dashboard at midnight UTC. They do not enforce
provider limits or alter routing. An empty mapping disables recording.

Start the Hermes gateway and `hermes dashboard` with the same absolute
`HARP_CONFIG` path and open **HARP Usage** in the dashboard. To group
usage by workload, set `HARP_USAGE_TAG` in the gateway process environment
(for example, `development`). Set `HARP_ACCOUNT` only for a process pinned
to that one account on the configured provider. Leave it unset for a
rotating credential pool. Unlabeled requests, unknown labels, and other
providers are grouped under **Unassigned**.

Totals come from successful main model requests observed by the gateway.
Responses without token counts show as unmetered. Auxiliary calls and
failed attempts are outside these totals. The SQLite file beside your
config holds aggregate counts and labels, with no credentials or chat text.

## Desktop UI

The optional `desktop/plugin.js` adds a **HARP** sidebar page to Hermes Desktop.
It reads the same authenticated `/accounts` and `/usage` endpoints as the web
Dashboard and includes the request-flow diagram. Each account row shows its own
session and week quota, 24 h tokens and active sessions. Its toolbar has HARP-owned
account actions for Anthropic and OpenAI Codex pools: **Live usage** (reads each
credential's usage), **Wake** (Anthropic only; one 1-token request to idle
accounts), **Balance now** (benches accounts at 90% or more and orders the pool by
usage), and **Add account** (relays `hermes auth add`). Wake and Balance ask for
confirmation. Below the provider row, Activity lists what HARP changed, Report breaks
down tokens by account, model, source and session, Health checks the pool and
the HARP Timer, and Alerts sends desktop or Telegram notifications at a usage
threshold. Local daily budgets are monitoring thresholds, not provider
limits. The Agent and Desktop
halves are enabled separately. After installing the package, rescan plugins in
Desktop's Capabilities → Plugins and enable the Desktop half. The backend API
may require a full Desktop quit and reopen to mount after a fresh install.
The page still shows account metadata when `HARP_CONFIG` is unset, but routing
and tagged usage collection remain inactive.

## Local checks

```powershell
python -B -m unittest discover -s tests -v
```

## Router

The Diagram tab routes each new message to a provider, model and effort.

### Hermes Delegation

Add work types in **HARP → Delegation**, choose their provider, agent and
effort, and select **Save delegation**. On Hermes versions that support
delegation providers, set `delegation.backend: harp` in the Hermes profile
and start a new session. Hermes still creates and runs each subagent.
Unmatched tasks use the Hermes default route. **Local** is the default
classifier. Selecting **Jev** sends the delegated goal and work descriptions
to TypeSafe, uses the same daily Jev allowance as the Router, and falls back
to Local if Jev is unavailable. Diagram shows the latest route HARP returned
to Hermes, which may differ from what ultimately runs if Hermes falls back.

In **Provider weighting**, HARP shows the plan reported by the account usage
API when available. Anthropic Max 5× or 20× is weighted automatically when
the API identifies that exact tier. Unknown plans and Codex use a default
weight of ×1. Change that weight in Diagram if needed. An explicit saved
work route takes precedence over provider weighting.

Open or close the **Quota** pane with the Hermes sidebar controls. **User
Tracker** groups accounts in provider accordions, with separate 5-hour and
weekly reset clocks. **Live routing** shows the latest six model calls the
Hermes hooks observed, including auxiliary calls. HARP keeps up to 100
events locally and records a short message excerpt only when it matches the
request. **Settings** switches the quota display between **Progress bar**
and **Ring**.

In Hermes Desktop, HARP shows a quota notice under the focused chat composer
and in the right-hand **HARP quota** pane. A provider needs fresh readings
showing that its available accounts are near the limit before HARP suggests
another provider with observed headroom. Account plans still weight the
provider totals. In Apply mode, Auto-switch is initially on and waits for
the active turn to finish. Shadow mode starts with Auto-switch off.
**Switch now** applies the offered model and effort to that chat for its
next message. **Ignore** dismisses the current provider warning until the
pool is seen below its limit again. Hermes' own model confirmation still
applies.
If a working Type-safe (Jev) API key is in the pool, Jev decides what the
local learner is not sure about and teaches it; without a key the local
learner decides on its own for free. Set each account's plan (x1, x5, x20)
so work moves away from a provider near its limit. It starts in Shadow mode,
which only records picks; Apply changes model and effort on the session's own
provider. Mark picks Right or Wrong to teach it.

## To do

- Track Jev tokens for calls made by HARP, including Diagram routing and Hermes Delegation. Record provider-reported token counts when available, associate them with the key used, and show a rolling 24-hour total in Type-safe. Show missing usage as unavailable and avoid double-counting calls already tracked by Hermes.

## License

HARP is licensed under the [MIT License](LICENSE).
