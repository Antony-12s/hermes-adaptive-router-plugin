# Decisions

- The Desktop interface is a read-only page in the unified HARP package, not a second routing owner. It reuses the existing authenticated Dashboard API.
- Desktop enablement is opt-in and separate from the Python plugin gate. Missing `HARP_CONFIG` leaves routing inactive; the page explains the missing usage collection without inventing defaults.
- The local daily token budgets remain observational. Provider quota is shown for the active profile, not attributed to individual pool accounts.
