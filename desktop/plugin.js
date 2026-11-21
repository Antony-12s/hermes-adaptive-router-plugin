// HARP Desktop page. Read-only; shares the plugin's authenticated backend.
import { host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, useQuery } from '@hermes/plugin-sdk'
import { createElement as h, useState } from 'react'

const ID = 'harp'
const ROUTE = '/harp'
const providers = { anthropic: 'Anthropic', jev: 'Jev · TypeSafe', 'openai-codex': 'OpenAI Codex' }
const number = new Intl.NumberFormat()
let rest

const CSS = `
.harp-desktop { box-sizing:border-box; height:100%; overflow:auto; padding:24px; color:var(--ui-text-primary); }
.harp-desktop * { box-sizing:border-box; }
.harp-desktop .harp-wrap { max-width:1000px; display:grid; gap:18px; }
.harp-desktop .harp-head, .harp-desktop .harp-row { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; }
.harp-desktop h1 { font-size:22px; font-weight:650; margin:0; }
.harp-desktop h2 { font-size:15px; font-weight:600; margin:0 0 12px; }
.harp-desktop p { margin:5px 0 0; }
.harp-desktop .harp-muted { color:var(--ui-text-tertiary); font-size:12px; }
.harp-desktop .harp-panels { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr)); gap:14px; }
.harp-desktop .harp-panel { min-width:0; padding:16px; border:1px solid var(--ui-stroke-secondary); border-radius:10px; }
.harp-desktop button, .harp-desktop select { color:inherit; background:transparent; border:1px solid var(--ui-stroke-secondary); border-radius:6px; padding:6px 10px; font:inherit; cursor:pointer; }
.harp-desktop button:disabled { opacity:.55; cursor:default; }
.harp-desktop button[aria-pressed=true] { border-color:var(--ui-accent); color:var(--ui-accent); }
.harp-desktop .harp-table { width:100%; overflow-x:auto; }
.harp-desktop table { width:100%; border-collapse:collapse; text-align:left; font-size:12px; }
.harp-desktop th, .harp-desktop td { padding:9px 8px; border-bottom:1px solid var(--ui-stroke-secondary); text-align:left; vertical-align:top; }
.harp-desktop .harp-flow { padding-left:22px; display:grid; gap:14px; }
.harp-desktop .harp-flow li::marker { color:var(--ui-accent); }
.harp-desktop progress { width:100%; accent-color:var(--ui-accent); }
`

function Table({ label, headings, rows }) {
  return h('div', { className: 'harp-table' }, h('table', { 'aria-label': label },
    h('thead', null, h('tr', null, ...headings.map(text => h('th', { key: text, scope: 'col' }, text)))),
    h('tbody', null, ...rows.map(({ key, cells }) => h('tr', { key }, ...cells.map((cell, index) =>
      h(index ? 'td' : 'th', { key: headings[index], scope: index ? undefined : 'row' }, cell)))))))
}

function Panel({ title, children }) {
  return h('section', { className: 'harp-panel' }, h('h2', null, title), children)
}

function QueryState({ query, label }) {
  if (query.isPending) return h('p', { role: 'status' }, `Loading ${label}…`)
  if (query.isError) return h('p', { role: 'alert' }, `Could not load ${label}. Check the HARP backend or reopen Desktop.`)
  return null
}

function Diagram() {
  const steps = [
    ['Hermes prepares the request', 'Selects provider, model and credentials.'],
    ['HARP checks routing rules', 'Eligible short text requests can use a faster model on the same provider.'],
    ['Hermes sends the request', 'Hermes handles retries and configured fallback routes.'],
    ['HARP records tagged usage', 'Successful main requests contribute reported token counts; unknown accounts stay Unassigned.']
  ]
  return h(Panel, { title: 'Request flow' }, h('ol', { className: 'harp-flow' },
    ...steps.map(([title, detail]) => h('li', { key: title }, h('strong', null, title), h('p', { className: 'harp-muted' }, detail)))))
}

function Accounts({ provider, query }) {
  const data = query.data
  return h('div', { className: 'harp-panels' },
    h(Panel, { title: 'Account Pool' }, h(QueryState, { query, label: 'accounts' }),
      data && (data.accounts.length
        ? h(Table, { label: 'Pool accounts', headings: ['Account', 'Status'], rows: data.accounts.map((row, index) => ({ key: index, cells: [row.label, row.status] })) })
        : h('p', { className: 'harp-muted' }, `No ${providers[provider]} accounts configured.`))),
    h(Panel, { title: 'Provider quota' }, h('p', { className: 'harp-muted' }, 'Hermes usage for the active profile; not per-account.'),
      data && (data.windows.length
        ? h(Table, { label: 'Provider quota', headings: ['Window', 'Used', 'Resets'], rows: data.windows.map((row, index) => ({
            key: index, cells: [row.label || '—', Number.isFinite(row.used_percent) ? `${number.format(row.used_percent)}%` : '—', row.reset_at ? new Date(row.reset_at).toLocaleString() : '—']
          })) })
        : h('p', { className: 'harp-muted' }, 'No provider quota reading available.'))))
}

function Budget({ account }) {
  if (account.quota_tokens == null) return 'No budget set'
  const limit = account.quota_tokens
  return h('div', null, h('span', null, `${number.format(account.tokens)} / ${number.format(limit)}`),
    h('progress', { value: Math.min(account.tokens, limit), max: limit, 'aria-label': `Daily token budget for ${account.account}` }),
    account.tokens > limit && h('span', null, 'Over budget'))
}

function Usage({ query, tag, setTag }) {
  const data = query.data
  const tags = data ? [...new Set(data.usage.map(row => row.tag))].sort() : []
  const selectedTag = tags.includes(tag) ? tag : ''
  const usage = data ? data.usage.filter(row => !selectedTag || row.tag === selectedTag) : []
  return h('div', { className: 'harp-wrap' },
    h(QueryState, { query, label: 'usage' }),
    data && !data.configured && h(Panel, { title: 'Usage collection is off' },
      h('p', { className: 'harp-muted' }, 'HARP_CONFIG and daily_token_quotas are required for local daily budgets and tagged usage. Accounts above remain available.')),
    data && data.configured && h('div', { className: 'harp-wrap' },
      h('p', { className: 'harp-muted' }, `Local daily budgets · ${data.day} UTC · Monitoring only, not provider limits`),
      h(Panel, { title: 'HARP daily budgets' }, data.accounts.length
        ? h(Table, { label: 'Daily budgets', headings: ['Account', 'Used tokens', 'Daily budget', 'Requests', 'Unmetered'], rows: data.accounts.map(row => ({
            key: row.account, cells: [row.account, number.format(row.tokens), h(Budget, { account: row }), number.format(row.requests), number.format(row.unmetered)]
          })) })
        : h('p', { className: 'harp-muted' }, 'No budgets or usage recorded.')),
      h(Panel, { title: 'Usage by tag' },
        h('label', null, 'Tag ', h('select', { value: selectedTag, onChange: event => setTag(event.target.value) },
          h('option', { value: '' }, 'All tags'), ...tags.map(value => h('option', { key: value, value }, value)))),
        usage.length
          ? h(Table, { label: 'Tagged usage', headings: ['Tag', 'Account', 'Provider', 'Model', 'Tokens', 'Requests', 'Unmetered'], rows: usage.map(row => ({
              key: JSON.stringify([row.tag, row.account, row.provider, row.model]),
              cells: [row.tag, row.account, row.provider, row.model, number.format(row.tokens), number.format(row.requests), number.format(row.unmetered)]
            })) })
          : h('p', { className: 'harp-muted' }, 'No usage recorded for this UTC day.'))))
}

function HarpPage() {
  const [provider, setProvider] = useState('anthropic')
  const [view, setView] = useState('usage')
  const [tag, setTag] = useState('')
  const accounts = useQuery({ queryKey: [ID, 'accounts', provider], queryFn: () => rest(`/accounts?provider=${encodeURIComponent(provider)}`), refetchInterval: 60000 })
  const usage = useQuery({ queryKey: [ID, 'usage'], queryFn: () => rest('/usage'), refetchInterval: 60000 })
  return h('main', { className: 'harp-desktop' }, h('div', { className: 'harp-wrap' },
    h('header', { className: 'harp-head' }, h('div', null, h('h1', null, 'HARP'),
      h('p', { className: 'harp-muted' }, 'Accounts, provider quota and local usage')), h('button', {
      type: 'button', disabled: accounts.isFetching || usage.isFetching,
      onClick: () => { accounts.refetch(); usage.refetch() }
    }, 'Refresh')),
    h('nav', { className: 'harp-row', 'aria-label': 'HARP views' },
      h('div', { className: 'harp-row' }, ...[['usage', 'Accounts & usage'], ['diagram', 'Diagram']].map(([value, label]) =>
        h('button', { key: value, type: 'button', 'aria-pressed': view === value, onClick: () => setView(value) }, label)))),
    view === 'diagram' ? h(Diagram) : h('div', { className: 'harp-wrap' },
      h('label', null, 'Provider ', h('select', { value: provider, onChange: event => setProvider(event.target.value) },
        ...Object.entries(providers).map(([value, label]) => h('option', { key: value, value }, label)))),
      h(Accounts, { provider, query: accounts }), h(Usage, { query: usage, tag, setTag }))))
}

export default {
  id: ID,
  name: 'HARP',
  description: 'Read-only accounts, quotas, usage and request flow for HARP.',
  defaultEnabled: false,
  register(ctx) {
    rest = ctx.rest
    const style = document.createElement('style')
    style.dataset.plugin = ID
    style.textContent = CSS
    document.head.appendChild(style)
    ctx.onDispose(() => style.remove())
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => h(HarpPage) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, order: 56, data: { path: ROUTE, label: 'HARP', codicon: 'graph' } },
      { id: 'open', area: PALETTE_AREA, data: { id: 'harp.open', label: 'HARP: accounts and usage', keywords: ['harp', 'router', 'usage'], run: () => host.navigate(ROUTE) } }
    ])
  }
}
