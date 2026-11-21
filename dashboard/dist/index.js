(function () {
  "use strict";

  const sdk = window.__HERMES_PLUGIN_SDK__;
  const { createElement: h, useEffect, useState } = sdk.React;
  const format = new Intl.NumberFormat();
  const providerNames = {
    anthropic: "Anthropic",
    jev: "Jev · TypeSafe",
    "openai-codex": "OpenAI Codex",
  };

  function AccountSources({ provider, revision }) {
    const [snapshot, setSnapshot] = useState(null);
    useEffect(() => {
      let current = true;
      setSnapshot(null);
      sdk.fetchJSON("/api/plugins/harp/accounts?provider=" + encodeURIComponent(provider))
        .then((data) => {
          if (current) setSnapshot({ provider, data });
        })
        .catch(() => {
          if (current) setSnapshot({ provider, data: null });
        });
      return () => { current = false; };
    }, [provider, revision]);
    if (snapshot?.provider !== provider) return h("p", { role: "status" }, "Loading account and quota data…");

    if (!snapshot.data) return h("p", { role: "alert" }, "Could not load accounts and quota from Hermes.");
    const { accounts, windows } = snapshot.data;
    return h("div", { className: "harp-panels" },
      h("section", { className: "harp-panel" },
        h("h2", null, "Account Pool"),
        accounts.length ? table("Pool accounts", ["Account", "Status"], accounts.map((row, index) => ({
          key: index,
          cells: [row.label, row.status],
        }))) : h("p", null, "No accounts configured for this provider.")
      ),
      h("section", { className: "harp-panel" },
        h("h2", null, "Provider quota"),
        h("p", { className: "harp-muted" }, "Usage reported by Hermes for the active profile."),
        windows.length ? table("Provider quota", ["Window", "Used", "Resets"], windows.map((row, index) => ({
          key: index,
          cells: [row.label ?? "—", Number.isFinite(row.used_percent) ? format.format(row.used_percent) + "%" : "—",
            row.reset_at ? new Date(row.reset_at).toLocaleString() : "—"],
        }))) : h("p", null, "No quota reading available for this provider.")
      )
    );
  }

  function Diagram() {
    const steps = [
      ["Hermes prepares the request", "Selects the provider, model and credentials."],
      ["HARP checks routing rules", "Eligible short text requests can use a faster model on the same provider."],
      ["Hermes sends the request", "Hermes handles retries and configured fallback routes."],
      ["HARP records tagged usage", "Successful main requests contribute reported token counts. Unknown accounts stay Unassigned."],
    ];
    return h("section", { className: "harp-panel" },
      h("h2", null, "Request flow"),
      h("ol", { className: "harp-flow" }, steps.map(([title, detail]) =>
        h("li", { key: title }, h("strong", null, title), h("p", null, detail)))),
      h("p", { className: "harp-muted" }, "The dashboard reads account and quota data from Hermes alongside HARP usage.")
    );
  }

  function table(label, headers, rows) {
    return h("div", { className: "harp-table-scroll" },
      h("table", { "aria-label": label },
        h("thead", null, h("tr", null, headers.map((header) =>
          h("th", { key: header, scope: "col" }, header)
        ))),
        h("tbody", null, rows.map(({ key, cells }) =>
          h("tr", { key }, cells.map((cell, index) =>
            h(index === 0 ? "th" : "td", { key: headers[index], scope: index === 0 ? "row" : undefined }, cell)
          ))
        ))
      )
    );
  }

  function quota(account) {
    if (account.quota_tokens == null) return "No quota set";
    const limit = account.quota_tokens;
    return h("div", { className: "harp-quota" },
      h("span", null, format.format(account.tokens) + " / " + format.format(limit)),
      limit > 0 && h("progress", {
        value: Math.min(account.tokens, limit),
        max: limit,
        "aria-label": "Daily token quota for " + account.account,
      }),
      account.tokens > limit && h("span", { className: "harp-over-quota" }, "Over quota")
    );
  }

  function HARPUsage() {
    const [data, setData] = useState(null);
    const [error, setError] = useState(false);
    const [loading, setLoading] = useState(true);
    const [revision, setRevision] = useState(0);
    const [tag, setTag] = useState("");
    const [provider, setProvider] = useState("anthropic");
    const [view, setView] = useState("usage");

    useEffect(() => {
      let current = true;
      setLoading(true);
      setError(false);
      sdk.fetchJSON("/api/plugins/harp/usage")
        .then((snapshot) => {
          if (current) setData(snapshot);
        })
        .catch(() => {
          if (current) setError(true);
        })
        .finally(() => {
          if (current) setLoading(false);
        });
      return () => { current = false; };
    }, [revision]);

    const tags = data ? [...new Set(data.usage.map((row) => row.tag))].sort() : [];
    const selectedTag = tags.includes(tag) ? tag : "";
    const usage = data ? data.usage.filter((row) => !selectedTag || row.tag === selectedTag) : [];

    return h("main", { className: "harp-usage" },
      h("header", { className: "harp-toolbar" },
        h("div", null,
          h("h1", null, "HARP Usage"),
          data?.configured && h("p", { className: "harp-muted" }, "Daily usage · " + data.day + " UTC")
        ),
        h("button", { type: "button", disabled: loading, onClick: () => setRevision(revision + 1) }, "Refresh")
      ),
      h("nav", { className: "harp-toolbar", "aria-label": "HARP views" },
        h("button", { type: "button", "aria-pressed": view === "usage", onClick: () => setView("usage") }, "Accounts & usage"),
        h("button", { type: "button", "aria-pressed": view === "diagram", onClick: () => setView("diagram") }, "Diagram")
      ),
      view === "diagram" ? h(Diagram) : h("div", { className: "harp-panels" },
      h("label", { className: "harp-filter" }, "Provider",
        h("select", { value: provider, onChange: (event) => setProvider(event.target.value) },
          Object.entries(providerNames).map(([value, label]) => h("option", { key: value, value }, label))
        )
      ),
      h(AccountSources, { provider, revision }),
      loading && h("p", { role: "status" }, "Loading usage…"),
      error && h("p", { role: "alert" }, "Could not load HARP usage. Refresh to try again."),
      data && !data.configured && h("p", null, "Configure usage collection to view account quotas and tags."),
      data?.configured && h("div", { className: "harp-panels", "aria-busy": loading },
        h("section", { className: "harp-panel" },
          h("h2", null, "HARP daily budgets"),
          h("p", { className: "harp-muted" }, "Daily quotas are local monitoring thresholds."),
          data.accounts.length ? table("Account quotas", ["Account", "Used tokens", "Daily quota", "Requests", "Without token counts"],
            data.accounts.map((row) => ({
              key: row.account,
              cells: [row.account, format.format(row.tokens), quota(row), format.format(row.requests), format.format(row.unmetered)],
            }))
          ) : h("p", null, "No accounts have usage or a configured quota.")
        ),
        h("section", { className: "harp-panel" },
          h("div", { className: "harp-toolbar" },
            h("h2", null, "Usage by tag"),
            h("label", { className: "harp-filter" }, "Tag",
              h("select", { value: selectedTag, onChange: (event) => setTag(event.target.value) },
                h("option", { value: "" }, "All tags"),
                tags.map((value) => h("option", { key: value, value }, value))
              )
            )
          ),
          usage.length ? table("Tagged usage", ["Tag", "Account", "Provider", "Model", "Tokens", "Requests", "Without token counts"],
            usage.map((row) => ({
              key: JSON.stringify([row.tag, row.account, row.provider, row.model]),
              cells: [row.tag, row.account, row.provider, row.model, format.format(row.tokens), format.format(row.requests), format.format(row.unmetered)],
            }))
          ) : h("p", null, "No usage recorded for this UTC day.")
        )
      ))
    );
  }

  window.__HERMES_PLUGINS__.register("harp", HARPUsage);
})();
