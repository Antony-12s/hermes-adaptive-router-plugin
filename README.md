# Hermes Adaptive Router Plugin (HARP)

HARP routes short, text-only Chat Completions requests from a configured primary
model to a faster model on the same provider. It applies the limit to the entire
text history, including system messages. Requests with tools, multimodal input,
structured output, or a different model or provider follow Hermes' default route.
Sessions that attach tools to every request will continue using the default model.
Set `allow_tools` to `true` to route initial text requests that advertise tools
when both models support the same tool definitions. Requests containing tool
results or previous tool calls still use Hermes' selected model.

## Configure on Windows

Use a Hermes Agent release that supports the
[LLM request middleware API](https://hermes-agent.nousresearch.com/docs/developer-guide/middleware).
From the HARP checkout, prepare your routing config:

```powershell
Copy-Item .\harp.example.json .\harp.json
$env:HARP_CONFIG = (Resolve-Path .\harp.json).Path
hermes plugins doctor .
```

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
confirmation. Local daily budgets are monitoring thresholds, not provider
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
