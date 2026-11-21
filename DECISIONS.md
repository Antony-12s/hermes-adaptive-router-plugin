# Decisions

- The Desktop interface lives in the unified HARP package, not as a second routing owner. It uses HARP's own authenticated Dashboard API and never calls the account-pool plugin.
- Desktop enablement is opt-in and separate from the Python plugin gate. Missing `HARP_CONFIG` leaves routing inactive; the page explains the missing usage collection without inventing defaults.
- The local daily token budgets remain observational. Provider quota is shown for the active profile; per-account usage comes only from HARP's Live usage read of each pool credential.
- The Desktop view follows Accounts' compact header, summary cards, status-marked account rows, and quota meters.
- HARP owns four account actions on the Hermes credential pool: Live usage (read-only per-credential usage), Balance now (bench at 90% or more, never the whole pool below 100%, release HARP benches, order by usage; no change without readings), Wake (Anthropic only, one 1-token request to accounts whose session window is idle, 10-minute cooldown), and Add account (relays Hermes' own `hermes auth add`; tokens never reach HARP or the renderer). Balance and Wake require confirmation in the page.
- HARP balance marks its benches with `[HARP balance]` and releases only its own benches. Run only one balance owner at a time; the account-pool balance timer was inactive when this was added.
