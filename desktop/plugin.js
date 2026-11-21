// HARP Desktop page: accounts, live usage, balance, wake and sign-in through HARP's own backend.
import { Button, Codicon, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, host, Input, PALETTE_AREA, ROUTES_AREA, SegmentedControl, SIDEBAR_NAV_AREA, useQuery } from '@hermes/plugin-sdk'
import { createElement as h, useEffect, useState } from 'react'

const ID = 'harp'
const ROUTE = '/harp'
const ACTION_PROVIDERS = ['anthropic', 'openai-codex']
const number = new Intl.NumberFormat()
let rest
let openExternal
const post = (path, body) => rest(path, { method: 'POST', body, timeoutMs: 180000 })
const notify = (kind, message) => host.notify?.({ kind, message })
const failure = error => error?.message || String(error)

// Match the density, surfaces, and controls of the Accounts desktop page.
const CSS = `
.harp-page{display:flex;flex-direction:column;height:100%;overflow:hidden;background:var(--ui-surface-background);color:var(--ui-text-secondary);font-size:.75rem}
.harp-page *{box-sizing:border-box}
.harp-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 16px 6px}
.harp-title{font-size:.875rem;font-weight:600;color:var(--ui-text-primary);margin:0}
.harp-pill{border-radius:999px;background:var(--ui-bg-quaternary);padding:1px 6px;font-size:.625rem;color:var(--ui-text-tertiary);font-variant-numeric:tabular-nums}
.harp-grow{flex:1}
.harp-sub{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:0 16px 8px;font-size:.6875rem;color:var(--ui-text-tertiary)}
.harp-body{flex:1;overflow:auto;padding:2px 16px 28px}
.harp-cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:2px 0 6px}
@media(max-width:850px){.harp-cards{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:500px){.harp-cards{grid-template-columns:1fr}}
.harp-card{min-width:0;border:1px solid var(--ui-stroke-secondary);border-radius:8px;padding:8px 10px;background:var(--ui-bg-card)}
.harp-card-k{font-size:.6875rem;color:var(--ui-text-tertiary)}
.harp-card-v{font-size:1.05rem;font-weight:600;margin-top:2px;color:var(--ui-text-primary);font-variant-numeric:tabular-nums}
.harp-card-s{font-size:.6875rem;color:var(--ui-text-secondary);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.harp-section{display:flex;align-items:center;gap:6px;margin:16px 0 4px;font-size:.6875rem;font-weight:600;color:var(--ui-text-tertiary);text-transform:uppercase;letter-spacing:.04em}
.harp-table-scroll{width:100%;overflow-x:auto}
.harp-table{width:100%;border-collapse:collapse;font-size:.75rem}
.harp-table th{text-align:left;font-weight:500;color:var(--ui-text-tertiary);font-size:.6875rem;padding:4px 8px;border-bottom:1px solid var(--ui-stroke-secondary);white-space:nowrap}
.harp-table td,.harp-table tbody th{padding:6px 8px;border-bottom:1px solid var(--ui-stroke-tertiary);vertical-align:middle}
.harp-table tbody th{text-align:left;font-weight:500;color:var(--ui-text-primary)}
.harp-table tbody tr:hover td,.harp-table tbody tr:hover th{background:var(--ui-row-hover-background)}
.harp-muted{color:var(--ui-text-tertiary);font-size:.6875rem}
.harp-banner{display:flex;gap:8px;align-items:center;border:1px solid var(--ui-stroke-secondary);background:var(--ui-bg-card);border-radius:8px;padding:8px 10px;margin:10px 0;font-size:.75rem}
.harp-bar{display:inline-block;width:96px;height:6px;border-radius:3px;background:var(--ui-bg-quaternary);overflow:hidden;flex:none}
.harp-bar>i{display:block;height:100%;background:var(--ui-accent)}
.harp-bar.warn>i{background:var(--ui-orange)}
.harp-bar.bad>i{background:var(--ui-red)}
.harp-quota{display:flex;align-items:center;gap:8px;white-space:nowrap}
.harp-account{display:flex;align-items:center;gap:6px}
.harp-dot{width:6px;height:6px;border-radius:50%;background:var(--ui-accent);flex:none}
.harp-dot.off{background:var(--ui-text-tertiary)}
.harp-status{font-size:.6875rem;color:var(--ui-text-tertiary)}
.harp-select{background:var(--ui-bg-input);color:var(--ui-text-primary);border:1px solid var(--ui-stroke-secondary);border-radius:6px;padding:3px 6px;font-size:.75rem}
.harp-select option{background:var(--ui-bg-card);color:var(--ui-text-primary)}
.harp-empty{padding:28px 8px;text-align:center;color:var(--ui-text-tertiary);font-size:.75rem}
.harp-flow{list-style:none;margin:6px 0;padding:0;max-width:720px}
.harp-flow li{display:flex;gap:10px;align-items:flex-start;padding:12px 0;border-bottom:1px solid var(--ui-stroke-tertiary)}
.harp-flow b{display:flex;align-items:center;justify-content:center;min-width:22px;height:22px;border-radius:6px;background:var(--ui-bg-quaternary);font-size:.6875rem;color:var(--ui-text-primary)}
.harp-flow strong{font-size:.75rem;color:var(--ui-text-primary)}
.harp-flow p{margin:2px 0 0;font-size:.6875rem;color:var(--ui-text-tertiary)}
.harp-dialog p{margin:0 0 8px;font-size:.75rem;color:var(--ui-text-secondary);line-height:1.45}
.harp-dialog code{font-size:1rem;font-weight:600;color:var(--ui-text-primary);letter-spacing:.08em}
.harp-picks{display:flex;flex-direction:column;gap:6px;margin:6px 0}
.harp-picks label{display:flex;align-items:center;gap:8px;font-size:.75rem}
.harp-error{color:var(--ui-red);font-size:.75rem;margin-top:6px}
.harp-link{word-break:break-all;font-size:.6875rem;color:var(--ui-text-tertiary)}
`

function Table({ label, headings, rows }) {
  return h('div', { className: 'harp-table-scroll' }, h('table', { className: 'harp-table', 'aria-label': label },
    h('thead', null, h('tr', null, ...headings.map(text => h('th', { key: text, scope: 'col' }, text)))),
    h('tbody', null, ...rows.map(({ key, cells }) => h('tr', { key }, ...cells.map((cell, index) =>
      h(index ? 'td' : 'th', { key: headings[index], scope: index ? undefined : 'row' }, cell)))))))
}

function Section({ title, children }) {
  return h('section', null, h('h2', { className: 'harp-section' }, title), children)
}

function QueryState({ query, label }) {
  if (query.isPending) return h('div', { className: 'harp-empty', role: 'status' }, `Loading ${label}…`)
  if (query.isError) return h('div', { className: 'harp-banner', role: 'alert' }, `Could not load ${label}. Check the HARP backend or reopen Desktop.`)
  return null
}

function Meter({ percent, label }) {
  if (!Number.isFinite(percent)) return h('span', { className: 'harp-muted' }, 'No reading')
  const safe = Math.max(0, Math.min(100, percent))
  return h('div', { className: 'harp-quota', 'aria-label': `${label}: ${number.format(percent)}% used` },
    h('span', { className: `harp-bar${safe >= 90 ? ' bad' : safe >= 75 ? ' warn' : ''}`, 'aria-hidden': true },
      h('i', { style: { width: `${safe}%` } })),
    h('span', null, `${number.format(percent)}%`))
}

function SummaryCard({ title, value, detail, percent }) {
  return h('div', { className: 'harp-card' },
    h('div', { className: 'harp-card-k' }, title),
    h('div', { className: 'harp-card-v' }, value),
    Number.isFinite(percent) && h('span', { className: `harp-bar${percent >= 90 ? ' bad' : percent >= 75 ? ' warn' : ''}`, 'aria-hidden': true },
      h('i', { style: { width: `${Math.max(0, Math.min(100, percent))}%` } })),
    h('div', { className: 'harp-card-s' }, detail))
}

function Diagram() {
  const steps = [
    ['Hermes prepares the request', 'Selects provider, model and credentials.'],
    ['HARP checks routing rules', 'Eligible short text requests can use a faster model on the same provider.'],
    ['Hermes sends the request', 'Hermes handles retries and configured fallback routes.'],
    ['HARP records tagged usage', 'Successful main requests contribute reported token counts; unknown accounts stay Unassigned.']
  ]
  return h(Section, { title: 'REQUEST FLOW' }, h('ol', { className: 'harp-flow' },
    ...steps.map(([title, detail], index) => h('li', { key: title }, h('b', null, index + 1),
      h('div', null, h('strong', null, title), h('p', null, detail))))))
}

function Accounts({ query }) {
  const data = query.data
  return h('div', null,
    h(QueryState, { query, label: 'accounts and quota' }),
    data && h('div', null,
      h('div', { className: 'harp-cards' },
        h(SummaryCard, { title: 'Pool accounts', value: number.format(data.accounts.length), detail: 'recorded by Hermes' }),
        h(SummaryCard, { title: 'Status OK', value: number.format(data.accounts.filter(row => row.status === 'ok').length), detail: 'as recorded by the pool' }),
        ...[0, 1].map(index => {
          const row = data.windows[index]
          return h(SummaryCard, {
            key: index, title: row?.label || 'Quota window',
            value: Number.isFinite(row?.used_percent) ? `${number.format(row.used_percent)}%` : '—',
            detail: row?.reset_at ? `Resets ${new Date(row.reset_at).toLocaleString()}` : 'No reading',
            percent: row?.used_percent
          })
        })),
      h(Section, { title: 'POOL ACCOUNTS' }, data.accounts.length
        ? h(Table, { label: 'Pool accounts', headings: ['#', 'Account', 'Live usage', 'Recorded status'], rows: data.accounts.map((row, index) => ({
            key: row.id || index, cells: [index + 1,
              h('span', { className: 'harp-account' }, h('span', { className: `harp-dot${row.status === 'ok' ? '' : ' off'}`, 'aria-hidden': true }), row.label),
              row.usage ? h(Meter, { percent: row.usage.peak, label: `${row.label} live usage` }) : h('span', { className: 'harp-muted' }, 'Not read yet'),
              h('span', { className: 'harp-status' }, row.status)]
          })) })
        : h('div', { className: 'harp-empty' }, 'No accounts configured for this provider.')),
      h(Section, { title: 'PROVIDER QUOTA' },
        h('div', { className: 'harp-muted' }, 'Hermes usage for the active profile, not for individual pool accounts.'),
        data.windows.length
          ? h(Table, { label: 'Provider quota', headings: ['Window', 'Used', 'Resets'], rows: data.windows.map((row, index) => ({
              key: index, cells: [row.label || '—', h(Meter, { percent: row.used_percent, label: row.label || 'Quota window' }), row.reset_at ? new Date(row.reset_at).toLocaleString() : '—']
            })) })
          : h('div', { className: 'harp-empty' }, 'No provider quota reading available.'))))
}

function Budget({ account }) {
  if (account.quota_tokens == null) return 'No budget set'
  const limit = account.quota_tokens
  const percent = account.tokens / limit * 100
  return h('div', { className: 'harp-quota', 'aria-label': `Daily token budget for ${account.account}: ${number.format(account.tokens)} of ${number.format(limit)}` },
    h('span', { className: `harp-bar${percent >= 100 ? ' bad' : percent >= 75 ? ' warn' : ''}`, 'aria-hidden': true },
      h('i', { style: { width: `${Math.max(0, Math.min(100, percent))}%` } })),
    h('span', null, `${number.format(account.tokens)} / ${number.format(limit)}`),
    account.tokens > limit && h('span', null, 'Over budget'))
}

function Usage({ query, tag, setTag }) {
  const data = query.data
  const tags = data ? [...new Set(data.usage.map(row => row.tag))].sort() : []
  const selectedTag = tags.includes(tag) ? tag : ''
  const usage = data ? data.usage.filter(row => !selectedTag || row.tag === selectedTag) : []
  return h('div', null,
    h(QueryState, { query, label: 'usage' }),
    data && !data.configured && h('div', { className: 'harp-banner' },
      h(Codicon, { name: 'info', size: '0.8rem' }),
      h('span', null, 'Local usage collection is off. HARP_CONFIG and daily_token_quotas are needed for budgets and tags.')),
    data && data.configured && h('div', null,
      h(Section, { title: 'DAILY BUDGETS' },
        h('span', { className: 'harp-muted' }, `Local monitoring · ${data.day} UTC · Not a provider limit`),
        data.accounts.length
          ? h(Table, { label: 'Daily budgets', headings: ['Account', 'Used tokens', 'Daily budget', 'Requests', 'Unmetered'], rows: data.accounts.map(row => ({
              key: row.account, cells: [row.account, number.format(row.tokens), h(Budget, { account: row }), number.format(row.requests), number.format(row.unmetered)]
            })) })
          : h('div', { className: 'harp-empty' }, 'No budgets or usage recorded.')),
      h(Section, { title: 'USAGE BY TAG' },
        h('label', { className: 'harp-muted' }, 'Tag ', h('select', { className: 'harp-select', value: selectedTag, onChange: event => setTag(event.target.value) },
          h('option', { value: '' }, 'All tags'), ...tags.map(value => h('option', { key: value, value }, value)))),
        usage.length
          ? h(Table, { label: 'Tagged usage', headings: ['Tag', 'Account', 'Provider', 'Model', 'Tokens', 'Requests', 'Unmetered'], rows: usage.map(row => ({
              key: JSON.stringify([row.tag, row.account, row.provider, row.model]),
              cells: [row.tag, row.account, row.provider, row.model, number.format(row.tokens), number.format(row.requests), number.format(row.unmetered)]
            })) })
          : h('div', { className: 'harp-empty' }, 'No usage recorded for this UTC day.'))))
}

function ActionDialog({ title, onClose, footer, children }) {
  return h(Dialog, { open: true, onOpenChange: open => !open && onClose() },
    h(DialogContent, { className: 'harp-dialog', style: { maxWidth: 520 } },
      h(DialogHeader, null, h(DialogTitle, null, title)),
      children,
      h(DialogFooter, null, h(Button, { size: 'sm', variant: 'ghost', onClick: onClose }, 'Cancel'), footer)))
}

function BalanceDialog({ provider, busy, onClose, onConfirm }) {
  return h(ActionDialog, { title: 'Balance now?', onClose, footer:
    h(Button, { size: 'sm', 'data-action': 'confirm-balance', disabled: !!busy, onClick: onConfirm }, busy ? 'Balancing…' : 'Balance now') },
    h('p', null, `HARP reads the live usage of every ${provider} account, benches accounts at 90% or more until their window resets, releases accounts HARP benched that are now below 90%, and puts the least-used account first.`),
    h('p', null, 'If no account can be read, nothing changes. If every account is at 90% or more, only accounts at 100% are benched. Running sessions keep their account.'))
}

function WakeDialog({ accounts, busy, onClose, onConfirm }) {
  const [picked, setPicked] = useState([])
  const usable = accounts.filter(row => row.id && row.status !== 'dead')
  return h(ActionDialog, { title: 'Wake accounts', onClose, footer:
    h(Button, { size: 'sm', 'data-action': 'confirm-wake', disabled: !!busy || !picked.length, onClick: () => onConfirm(picked) },
      busy ? 'Waking…' : `Wake ${picked.length} account${picked.length === 1 ? '' : 's'}`) },
    h('p', null, 'Wake sends one tiny request (claude-haiku-4-5, 1 output token) to each chosen account whose 5-hour window is not running, so the window starts now. HARP skips accounts that are running, unread, or woken in the last 10 minutes.'),
    h('div', { className: 'harp-picks' }, ...usable.map(row => h('label', { key: row.id },
      h('input', { type: 'checkbox', checked: picked.includes(row.id), 'aria-label': `Wake ${row.label}`,
        onChange: event => setPicked(event.target.checked ? [...picked, row.id] : picked.filter(id => id !== row.id)) }),
      row.label))),
    !usable.length && h('p', null, 'No usable accounts.'))
}

function AddDialog({ provider, onClose, onDone }) {
  const [label, setLabel] = useState('')
  const [flow, setFlow] = useState(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const finish = result => {
    if (result.status === 'done') { notify('success', `Added ${result.account}.`); onDone() }
    else if (result.status === 'error') { setError(result.message); if (result.final) setFlow(null) }
  }
  useEffect(() => {
    if (flow?.kind !== 'device') return undefined
    const timer = setInterval(() => post('/signin/step', { flow: flow.flow }).then(finish, e => setError(failure(e))), 3000)
    return () => clearInterval(timer)
  }, [flow?.flow])
  const act = async (path, body, then) => {
    setBusy(true); setError('')
    try { then(await post(path, body)) } catch (e) { setError(failure(e)) } finally { setBusy(false) }
  }
  const cancel = () => { if (flow) post('/signin/cancel', { flow: flow.flow }).catch(() => {}); onClose() }
  const footer = !flow
    ? h(Button, { size: 'sm', 'data-action': 'start-signin', disabled: busy || !label.trim(),
        onClick: () => act('/signin/start', { provider, label: label.trim() }, setFlow) }, busy ? 'Starting…' : 'Start sign-in')
    : flow.kind === 'code'
      ? h(Button, { size: 'sm', 'data-action': 'finish-signin', disabled: busy || !code.trim(),
          onClick: () => act('/signin/step', { flow: flow.flow, code }, finish) }, busy ? 'Checking…' : 'Finish')
      : null
  return h(ActionDialog, { title: `Add ${provider} account`, onClose: cancel, footer },
    !flow && h('div', null,
      h('p', null, 'Hermes signs the account in and saves it in its pool. Use the account email as the name.'),
      h(Input, { value: label, placeholder: 'name@example.com', 'aria-label': 'Account name', onChange: event => setLabel(event.target.value) })),
    flow?.kind === 'code' && h('div', null,
      h('p', null, '1. Open the link in a browser signed in as this account and authorize.'),
      h(Button, { size: 'sm', variant: 'outline', onClick: () => openExternal ? openExternal(flow.url) : window.open(flow.url) }, 'Open sign-in link'),
      h('div', { className: 'harp-link' }, flow.url),
      h('p', null, '2. Paste the whole code shown after authorizing.'),
      h(Input, { value: code, placeholder: 'code#state', 'aria-label': 'Authorization code', onChange: event => setCode(event.target.value) })),
    flow?.kind === 'device' && h('div', null,
      h('p', null, 'Open the page, enter this code, and authorize. This window finishes by itself.'),
      h('code', null, flow.user_code),
      h('div', null, h(Button, { size: 'sm', variant: 'outline', onClick: () => openExternal ? openExternal(flow.url) : window.open(flow.url) }, 'Open sign-in page'))),
    error && h('div', { className: 'harp-error', role: 'alert' }, error))
}

function HarpPage() {
  const [provider, setProvider] = useState('anthropic')
  const [view, setView] = useState('usage')
  const [tag, setTag] = useState('')
  const [dialog, setDialog] = useState(null)
  const [busy, setBusy] = useState('')
  const accounts = useQuery({ queryKey: [ID, 'accounts', provider], queryFn: () => rest(`/accounts?provider=${encodeURIComponent(provider)}`), refetchInterval: 60000 })
  const usage = useQuery({ queryKey: [ID, 'usage'], queryFn: () => rest('/usage'), refetchInterval: 60000 })
  const canAct = ACTION_PROVIDERS.includes(provider)
  const run = async (name, path, body, report) => {
    setBusy(name)
    try { report(await post(path, body)); setDialog(null); accounts.refetch() }
    catch (error) { notify('error', `${name} failed: ${failure(error)}`) }
    finally { setBusy('') }
  }
  const tool = (label, icon, props) => h(Button, { size: 'sm', variant: 'ghost', disabled: !canAct || !!busy, ...props },
    h(Codicon, { name: icon, size: '0.8rem' }), label)
  return h('main', { className: 'harp-page' },
    h('header', { className: 'harp-head' },
      h('h1', { className: 'harp-title' }, 'HARP'),
      accounts.data && h('span', { className: 'harp-pill' }, accounts.data.accounts.length),
      h(SegmentedControl, { options: [
        { id: 'anthropic', label: 'anthropic' }, { id: 'jev', label: 'jev' }, { id: 'openai-codex', label: 'openai-codex' }
      ], value: provider, onChange: setProvider }),
      h('span', { className: 'harp-grow' }),
      h(Button, { size: 'sm', variant: 'ghost', 'aria-label': 'Refresh', disabled: accounts.isFetching || usage.isFetching,
        onClick: () => { accounts.refetch(); usage.refetch() } }, h(Codicon, { name: 'eye', size: '0.8rem' })),
      tool('Live usage', 'refresh', { onClick: () => run('Live usage', '/pool/live', { provider }, () => notify('success', 'Live usage updated.')) }),
      tool('Wake', 'zap', { disabled: provider !== 'anthropic' || !!busy, onClick: () => setDialog('wake') }),
      tool('Balance now', 'arrow-swap', { variant: 'outline', onClick: () => setDialog('balance') }),
      tool('Add account', 'add', { variant: 'default', onClick: () => setDialog('add') })),
    h('div', { className: 'harp-sub' },
      h(SegmentedControl, { options: [{ id: 'usage', label: 'Accounts & usage' }, { id: 'diagram', label: 'Diagram' }], value: view, onChange: setView }),
      h('span', null, accounts.data?.live_read_at ? `Live usage read ${new Date(accounts.data.live_read_at * 1000).toLocaleTimeString()}` : 'Live usage not read yet'),
      h('span', null, '· budgets are local monitoring')),
    h('div', { className: 'harp-body' }, view === 'diagram' ? h(Diagram) : h('div', null,
      h(Accounts, { query: accounts }), h(Usage, { query: usage, tag, setTag }))),
    dialog === 'balance' && h(BalanceDialog, { provider, busy, onClose: () => setDialog(null),
      onConfirm: () => run('Balance', '/pool/balance', { provider }, result => notify(result.changed ? 'success' : 'warning',
        result.changed ? `Balanced: ${result.order.join(' > ')}${result.benched.length ? ` · benched ${result.benched.join(', ')}` : ''}` : result.message)) }),
    dialog === 'wake' && h(WakeDialog, { accounts: accounts.data?.accounts || [], busy, onClose: () => setDialog(null),
      onConfirm: ids => run('Wake', '/pool/wake', { provider, ids }, result => notify('info',
        result.results.map(row => `${row.account}: ${row.result}`).join('\n') || 'Nothing to wake.')) }),
    dialog === 'add' && h(AddDialog, { provider, onClose: () => setDialog(null), onDone: () => { setDialog(null); accounts.refetch() } }))
}

export default {
  id: ID,
  name: 'HARP',
  description: 'Accounts, live usage, balance, wake, sign-in and request flow for HARP.',
  defaultEnabled: false,
  register(ctx) {
    rest = ctx.rest
    openExternal = ctx.os?.openExternal
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
