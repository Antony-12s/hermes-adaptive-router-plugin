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
`plugin.yaml` and `__init__.py` into `~/.hermes/plugins/harp/`. For a custom
home or profile, use its plugin directory. Then run `hermes plugins enable harp`.
Start or restart the gateway with the same `HARP_CONFIG` environment variable.

Hermes owns retry and failover. If you want a backup on transient failures,
configure a distinct backup route in
[`fallback_providers`](https://hermes-agent.nousresearch.com/docs/user-guide/features/fallback-providers).
HARP scopes its override to the configured primary route, so a distinct
fallback route passes through. The alternate model receives the full input via
the configured provider.

## Local checks

```powershell
python -B -m unittest discover -s tests -v
```
