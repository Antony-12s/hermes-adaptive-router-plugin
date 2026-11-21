// HARP Desktop page: accounts, live usage, balance, wake and sign-in through HARP's own backend.
import { Button, Codicon, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, host, Input, PALETTE_AREA, ROUTES_AREA, SegmentedControl, SIDEBAR_NAV_AREA, Switch, useQuery } from '@hermes/plugin-sdk'
import * as sdk from '@hermes/plugin-sdk'

import { createElement as h, Fragment, useEffect, useState } from 'react'

const ID = 'harp'
const ROUTE = '/harp'
const ACTION_PROVIDERS = ['anthropic', 'openai-codex', 'jev']
const KEY_PROVIDERS = ['jev']   // accounts are API keys: no sign-in flow, no usage windows
const TYPESAFE_CONSOLE = 'https://console.typesafe.ai/home'
const number = new Intl.NumberFormat()
const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 })
const usd = new Intl.NumberFormat('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
let rest
let openExternal
const post = (path, body) => rest(path, { method: 'POST', body, timeoutMs: 180000 })
const notify = (kind, message) => host.notify?.({ kind, message })
const failure = error => error?.message || String(error)
const LIVE_MAX_AGE = 300
const liveRequested = {}

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
.harp-window{display:flex;flex-direction:column;gap:2px}
.harp-reset{font-size:.6875rem;color:var(--ui-text-tertiary);white-space:nowrap;font-variant-numeric:tabular-nums}
.harp-account{display:flex;align-items:center;gap:6px}
.harp-dot{width:6px;height:6px;border-radius:50%;background:var(--ui-accent);flex:none}
.harp-dot.off{background:var(--ui-text-tertiary)}
.harp-status{font-size:.6875rem;color:var(--ui-text-tertiary)}
.harp-select{background:var(--ui-bg-input);color:var(--ui-text-primary);border:1px solid var(--ui-stroke-secondary);border-radius:6px;padding:3px 6px;font-size:.75rem}
.harp-select option{background:var(--ui-bg-card);color:var(--ui-text-primary)}
.harp-select{background:transparent;color-scheme:dark light}
.harp-select-trigger{width:auto;min-width:96px}
.harp-select-wide{width:100%;min-width:0}
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
.harp-num{display:block;text-align:right;font-variant-numeric:tabular-nums;color:var(--ui-text-primary)}
.harp-row-actions{display:flex;justify-content:flex-end;gap:2px;color:var(--ui-text-tertiary)}
.harp-row-actions button{opacity:.7}
.harp-row-actions button:hover{opacity:1}
.harp-dialog label.harp-field{display:block;font-size:.75rem;margin:6px 0 4px;color:var(--ui-text-secondary)}
.harp-tools{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:4px 0 8px;font-size:.6875rem;color:var(--ui-text-tertiary)}
.harp-share{display:inline-block;width:72px;height:5px;border-radius:3px;background:var(--ui-bg-quaternary);overflow:hidden;vertical-align:middle;margin-right:6px}
.harp-share>i{display:block;height:100%;background:var(--ui-accent)}
.harp-heat-wrap{overflow-x:auto;max-width:100%}
.harp-heat{display:grid;grid-template-columns:72px repeat(24,minmax(0,1fr));gap:2px;font-size:.625rem;color:var(--ui-text-tertiary);margin-top:4px;width:100%;max-width:760px;box-sizing:border-box}
.harp-heat-cell{height:14px;border-radius:2px;background:var(--ui-bg-quaternary)}
.harp-check{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--ui-stroke-tertiary);font-size:.75rem}
.harp-check i{width:7px;height:7px;border-radius:50%;flex:none;background:var(--ui-accent)}
.harp-check i.warn{background:var(--ui-orange)}
.harp-check i.fail{background:var(--ui-red)}
.harp-setting{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--ui-stroke-tertiary)}
.harp-setting b{display:block;font-size:.75rem;font-weight:500;color:var(--ui-text-primary)}
.harp-setting span{font-size:.6875rem;color:var(--ui-text-tertiary)}
.harp-setting input{width:64px}
.harp-key{display:flex;align-items:center;gap:10px;padding:9px 12px;border:1px solid var(--ui-stroke-tertiary);border-radius:8px;margin:6px 0 4px}
.harp-key.ok{border-color:color-mix(in srgb,var(--ui-green, #3fb950) 60%,transparent)}
.harp-key.warn{border-color:color-mix(in srgb,var(--ui-orange) 60%,transparent)}
.harp-key b{display:block;font-size:.8125rem;font-weight:600;color:var(--ui-text-primary)}
.harp-key span{font-size:.6875rem;color:var(--ui-text-tertiary)}
.harp-tree{position:relative;margin:8px 0 4px}
.harp-tree svg{position:absolute;inset:0;overflow:visible;pointer-events:none}
.harp-tree path{fill:none;stroke:var(--ui-stroke-secondary);stroke-width:1.5}
.harp-tree path.on{stroke:var(--ui-accent);stroke-width:2.4}
.harp-node{position:absolute;height:34px;display:flex;align-items:center;gap:6px;padding:0 10px;border:1px solid var(--ui-stroke-tertiary);border-radius:8px;background:var(--ui-bg-secondary);font-size:.75rem;white-space:nowrap;overflow:hidden}
.harp-node.on{border-color:var(--ui-accent);box-shadow:0 0 0 1px var(--ui-accent)}
.harp-node.dim{opacity:.55}
.harp-node.round{border-radius:17px;font-weight:600}
.harp-node small{margin-left:auto;color:var(--ui-text-tertiary);font-variant-numeric:tabular-nums}
.harp-cand{display:grid;grid-template-columns:22px 130px minmax(140px,1fr) minmax(160px,2fr) 92px auto;gap:6px;align-items:center;padding:4px 0;border-bottom:1px solid var(--ui-stroke-tertiary);font-size:.75rem}
.harp-cand input,.harp-cand select,.harp-load select,.harp-tools select{font-size:.75rem}
.harp-load{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px;margin-top:4px}
.harp-load>div{border:1px solid var(--ui-stroke-tertiary);border-radius:8px;padding:8px 10px}
.harp-load>div.hot{border-color:var(--ui-red)}
.harp-load .acc{display:grid;grid-template-columns:minmax(0,1fr) 72px 110px;gap:8px;align-items:center;font-size:.6875rem;margin-top:5px}
.harp-load .acc>span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.harp-pick{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:.75rem;margin-top:6px}
.harp-pick i{font-style:normal;padding:2px 8px;border-radius:10px;border:1px solid var(--ui-stroke-tertiary)}
.harp-pick i.hit{border-color:var(--ui-accent);background:color-mix(in srgb,var(--ui-accent) 15%,transparent)}
.harp-pick s{opacity:.6}
`

// Dropdowns follow the Hermes theme through the SDK Select; a native <select> is the fallback.
// options: strings or { value, label }. An empty value is allowed (shown as the placeholder).
const SDK = typeof sdk === 'undefined' ? {} : sdk
const NONE = '__none__'
function Pick({ value, options, onChange, label, wide, placeholder }) {
  const opts = options.map(o => (typeof o === 'string' ? { value: o, label: o } : o))
  if (SDK.Select && SDK.SelectTrigger && SDK.SelectContent && SDK.SelectItem && SDK.SelectValue) {
    return h(SDK.Select, { value: value === '' ? NONE : String(value), onValueChange: v => onChange(v === NONE ? '' : v) },
      h(SDK.SelectTrigger, { size: 'sm', className: `harp-select-trigger${wide ? ' harp-select-wide' : ''}`, 'aria-label': label },
        h(SDK.SelectValue, { placeholder })),
      h(SDK.SelectContent, null, opts.map(o => h(SDK.SelectItem, { key: String(o.value), value: o.value === '' ? NONE : String(o.value) }, o.label))))
  }
  return h('select', { className: `harp-select${wide ? ' harp-select-wide' : ''}`, value, 'aria-label': label, onChange: e => onChange(e.target.value) },
    placeholder && value === '' ? h('option', { value: '' }, placeholder) : null,
    ...opts.map(o => h('option', { key: String(o.value), value: o.value }, o.label)))
}

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

function SummaryCard({ title, value, detail, percent, lines }) {
  return h('div', { className: 'harp-card' },
    h('div', { className: 'harp-card-k' }, title),
    h('div', { className: 'harp-card-v' }, value),
    Number.isFinite(percent) && h('span', { className: `harp-bar${percent >= 90 ? ' bad' : percent >= 75 ? ' warn' : ''}`, 'aria-hidden': true },
      h('i', { style: { width: `${Math.max(0, Math.min(100, percent))}%` } })),
    detail && h('div', { className: 'harp-card-s' }, detail),
    ...(lines || []).filter(Boolean).map((line, i) => h('div', { className: 'harp-card-s', key: i }, line)))
}

const ROUTER_LABEL = { anthropic: 'anthropic', 'openai-codex': 'Codex' }
const KEY_TEXT = {
  ok: ['ok', 'Jev API found', 'Jev decides what the local learner is not sure about, and teaches it.'],
  none: ['warn', 'No Jev API key: the local learner decides (free)', 'It learns from your corrections. Add a Type-safe key to make it sharper and learn faster.'],
  bad: ['warn', 'The Jev API key was rejected: the local learner took over', 'TypeSafe answered 401/403. Nothing breaks; replace the key in Type-safe.'],
  down: ['warn', 'Jev is not answering or out of credit: the local learner took over for now', 'HARP checks Jev again every 5 minutes and switches back by itself.']
}
const candId = c => `${c.provider}:${c.model}`
const shortId = id => (id || '').split(':').slice(1).join(':')

function RouterTree({ settings, decision, by }) {
  const cands = settings.candidates
  const ROW = 42, W = { text: 80, judge: 150, route: 170, cand: 230 }, X = { text: 0, judge: 104, route: 282, cand: 482 }
  const height = Math.max(1, cands.length) * ROW
  const mid = height / 2 - 17
  const pick = decision?.pick
  const path = (x1, y1, x2, y2, on) => { const mx = (x1 + x2) / 2; return h('path', { key: `${x2}-${y2}`, className: on ? 'on' : '', d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}` }) }
  const node = (key, x, y, w, cls, ...children) => h('div', { key, className: `harp-node ${cls}`, style: { left: `${x}px`, top: `${y}px`, width: `${w}px` } }, ...children)
  return h('div', { className: 'harp-tree', style: { height: `${height}px`, minWidth: `${X.cand + W.cand}px` } },
    h('svg', { width: X.cand + W.cand, height },
      path(X.text + W.text, mid + 17, X.judge, mid + 17, !!decision), path(X.judge + W.judge, mid + 17, X.route, mid + 17, !!decision),
      ...cands.map((c, i) => path(X.route + W.route, mid + 17, X.cand, i * ROW + 21, candId(c) === pick))),
    node('t', X.text, mid, W.text, 'round on', 'Text'),
    node('j', X.judge, mid, W.judge, `round ${decision ? 'on' : ''}`, by === 'jev' ? 'Jev decides' : 'Local learner'),
    node('r', X.route, mid, W.route, decision ? 'on' : '', 'ROUTING TO PROVIDER'),
    ...cands.map((c, i) => node(candId(c), X.cand, i * ROW + 4, W.cand, pick ? (candId(c) === pick ? 'on' : 'dim') : '',
      h('span', null, `${ROUTER_LABEL[c.provider] || c.provider} · ${c.model}`),
      decision?.probs?.[candId(c)] != null && h('small', null, `${Math.round(decision.probs[candId(c)] * 100)}%`))))
}

function RouterView({ onAddKey }) {
  const query = useQuery({ queryKey: [ID, 'router'], queryFn: () => rest('/router'), refetchInterval: 30000 })
  const [draft, setDraft] = useState(null)
  const [busy, setBusy] = useState('')
  const [sample, setSample] = useState('')
  const [tried, setTried] = useState(null)
  const [fixing, setFixing] = useState(null)
  const data = query.data
  if (!data) return h(QueryState, { query, label: 'router' })
  const s = draft || data.settings
  const edit = changes => setDraft({ ...s, ...changes })
  const setCand = (i, changes) => edit({ candidates: s.candidates.map((c, j) => (j === i ? { ...c, ...changes } : c)) })
  const move = (i, d) => { const list = [...s.candidates]; [list[i], list[i + d]] = [list[i + d], list[i]]; edit({ candidates: list }) }
  const act = async (name, fn) => { setBusy(name); try { await fn() } catch (e) { notify('error', `${name}: ${failure(e)}`) } finally { setBusy('') } }
  const save = () => act('Save', async () => { await post('/router/settings', s); setDraft(null); notify('success', 'Router saved.'); query.refetch() })
  const tryIt = useJev => act(useJev ? 'Ask Jev' : 'Try', async () => setTried((await post('/router/try', { text: sample, use_jev: useJev })).decision))
  const vote = (row, label) => act('Feedback', async () => { await post('/router/feedback', { id: row.id, label }); setFixing(null); notify('success', 'Learned.'); query.refetch() })
  const [cls, title, detail] = KEY_TEXT[data.jev.state] || KEY_TEXT.down
  const shown = tried || (data.recent[0] ? { pick: data.recent[0].pick, probs: null } : null)
  const st = data.stats
  return h('div', null,
    h('div', { className: 'harp-tools' },
      h(Switch, { checked: s.enabled, 'aria-label': 'Router on', onCheckedChange: value => edit({ enabled: value }) }), 'Router',
      h(SegmentedControl, { options: [{ id: 'shadow', label: 'Shadow: record only' }, { id: 'apply', label: 'Apply on the same provider' }],
        value: s.apply ? 'apply' : 'shadow', onChange: value => edit({ apply: value === 'apply' }) }),
      h('span', { className: 'harp-grow' }),
      draft && h('span', null, 'Unsaved changes'),
      h(Button, { size: 'sm', variant: 'default', 'data-action': 'save-router', disabled: !draft || !!busy, onClick: save }, busy === 'Save' ? 'Saving…' : 'Save')),
    h('div', { className: `harp-key ${cls}` }, h(Codicon, { name: data.jev.state === 'ok' ? 'pass' : 'info', size: '1rem' }),
      h('div', { className: 'harp-grow' }, h('b', null, data.jev.state === 'ok' ? `${title} · ${data.jev.label} · ${data.jev.ms ?? '–'} ms` : title), h('span', null, detail)),
      data.jev.state === 'none'
        ? h(Button, { size: 'sm', variant: 'outline', onClick: onAddKey }, 'Add Jev API key')
        : h(Button, { size: 'sm', variant: 'ghost', disabled: !!busy, onClick: () => act('Check', async () => { await post('/router/check-key', {}); query.refetch() }) }, 'Check now')),
    h(Section, { title: 'HOW A MESSAGE IS ROUTED' }, h('div', { style: { overflowX: 'auto' } },
      h(RouterTree, { settings: s, decision: shown, by: tried?.by || data.recent[0]?.by || (data.jev.state === 'ok' ? 'jev' : 'local') }))),
    h(Section, { title: 'MODELS IT CAN PICK' },
      h('div', { className: 'harp-tools' }, 'Strongest first: when unsure it uses the top one. The description tells Jev and the learner what each is for.'),
      ...s.candidates.map((c, i) => h('div', { className: 'harp-cand', key: candId(c) + i },
        h('span', { className: 'harp-muted' }, i + 1),
        h(Pick, { value: c.provider, label: `Provider ${i + 1}`, wide: true, options: Object.keys(ROUTER_LABEL).map(p => ({ value: p, label: ROUTER_LABEL[p] })),
          onChange: v => setCand(i, { provider: v, model: (data.models[v] || [''])[0] }) }),
        h(Pick, { value: c.model, label: `Model ${i + 1}`, wide: true, options: [...new Set([c.model, ...(data.models[c.provider] || [])])], onChange: v => setCand(i, { model: v }) }),
        h(Input, { value: c.description || '', placeholder: 'What it is good at', 'aria-label': `Good at ${i + 1}`, onChange: e => setCand(i, { description: e.target.value }) }),
        h(Pick, { value: c.cap || 'high', label: `Effort ceiling ${i + 1}`, wide: true, options: ['low', 'medium', 'high'].map(v => ({ value: v, label: `max ${v}` })), onChange: v => setCand(i, { cap: v }) }),
        h('span', { className: 'harp-row-actions' },
          h(Button, { size: 'sm', variant: 'ghost', 'aria-label': `Move up ${i + 1}`, disabled: !i, onClick: () => move(i, -1) }, h(Codicon, { name: 'arrow-up', size: '0.8rem' })),
          h(Button, { size: 'sm', variant: 'ghost', 'aria-label': `Remove ${i + 1}`, onClick: () => edit({ candidates: s.candidates.filter((_, j) => j !== i) }) }, h(Codicon, { name: 'trash', size: '0.8rem' }))))),
      h('div', { className: 'harp-tools' }, h(Button, { size: 'sm', variant: 'outline', onClick: () => edit({ candidates: [...s.candidates, { provider: 'anthropic', model: (data.models.anthropic || [''])[0], description: '', cap: 'medium' }] }) },
        h(Codicon, { name: 'add', size: '0.8rem' }), 'Add model'))),
    h(Section, { title: 'FOCUS' }, h(SegmentedControl, { options: [{ id: 'save', label: 'Save' }, { id: 'balanced', label: 'Balanced' }, { id: 'best', label: 'Best' }],
      value: s.mode, onChange: value => edit({ mode: value }) }),
      h('span', { className: 'harp-muted' }, { save: ' Small models first, light thinking.', balanced: ' Pick by the task, medium thinking at most.', best: ' Strongest when unsure, full thinking.' }[s.mode])),
    h(Section, { title: 'PROVIDER LOAD' },
      h('div', { className: 'harp-tools' }, 'Set each account\'s plan once. Work moves away from a provider above ',
        h(Pick, { value: String(s.limit), label: 'Load limit', options: [70, 80, 85, 90, 95].map(v => ({ value: String(v), label: `${v}%` })), onChange: v => edit({ limit: Number(v) }) })),
      h('div', { className: 'harp-load' }, ...Object.keys(ROUTER_LABEL).map(provider => {
        const load = data.loads[provider]
        const hot = load && load.used >= s.limit
        return h('div', { key: provider, className: hot ? 'hot' : '' },
          h('div', { className: 'harp-account' }, h('b', null, ROUTER_LABEL[provider]), h('span', { className: 'harp-grow' }),
            h('span', { className: 'harp-muted' }, !load ? 'no usage read yet' : hot ? `${load.used}% used: near its limit` : `${load.used}% used`)),
          ...(data.accounts[provider] || []).map(a => h('div', { className: 'acc', key: a.id }, h('span', null, a.label),
            h(Pick, { value: (s.plans[provider] || {})[a.id] || 'x1', label: `Plan: ${a.label}`, wide: true, options: ['x1', 'x5', 'x20'],
              onChange: v => edit({ plans: { ...s.plans, [provider]: { ...(s.plans[provider] || {}), [a.id]: v } } }) }),
            h(Meter, { percent: a.peak, label: `${a.label} usage` }))))
      }))),
    h(Section, { title: 'TRY A MESSAGE' },
      h('div', { className: 'harp-tools' },
        h(Input, { value: sample, placeholder: 'Type anything…', 'aria-label': 'Try a message', onChange: e => setSample(e.target.value) }),
        h(Button, { size: 'sm', variant: 'outline', disabled: !sample.trim() || !!busy, onClick: () => tryIt(false) }, 'Try'),
        h(Button, { size: 'sm', variant: 'ghost', disabled: !sample.trim() || !!busy || data.jev.state !== 'ok', title: 'Spends one Jev call', onClick: () => tryIt(true) }, 'Ask Jev')),
      tried && h('div', { className: 'harp-pick' },
        h('span', null, tried.by === 'jev' ? 'Jev' : 'Local learner'),
        tried.moved_from && h('s', null, shortId(tried.moved_from)),
        h('i', { className: 'hit' }, `${ROUTER_LABEL[tried.pick.split(':')[0]]} · ${shortId(tried.pick)} · ${tried.effort}`),
        h('span', { className: 'harp-muted' }, tried.unsure ? `not sure (${Math.round(tried.confidence * 100)}%): strongest model` : `sure ${Math.round(tried.confidence * 100)}%`),
        tried.moved_from && h('span', { className: 'harp-muted' }, 'moved: the first pick\'s provider is near its limit'))),
    h(Section, { title: 'WHAT IT LEARNED' },
      h('div', { className: 'harp-cards' },
        h(SummaryCard, { title: 'Learned from', value: `${data.samples}`, detail: `messages · ${st.corrections_total} of them your corrections` }),
        h(SummaryCard, { title: 'Decided locally, 7 days', value: st.decisions ? `${Math.round(st.local / st.decisions * 100)}%` : '–', detail: `${st.local} of ${st.decisions} · free` }),
        h(SummaryCard, { title: 'Jev calls today', value: `${data.jev_calls_today}`, detail: `daily cap ${s.jev_daily_cap}` }),
        h(SummaryCard, { title: 'Right when rated', value: st.rated ? `${Math.round(st.right / st.rated * 100)}%` : '–', detail: `${st.rated} rated` })),
      st.corrections.length ? h('div', { className: 'harp-tools' }, 'Corrections: ', st.corrections.map(c => `${shortId(c.from)} → ${shortId(c.to)} ×${c.times}`).join(' · ')) : null,
      data.recent.length
        ? h(Table, { label: 'Recent picks', headings: ['Message', 'Picked', 'By', 'Was on', ''], rows: data.recent.map(row => ({ key: row.id, cells: [
            h('span', { className: 'harp-status' }, row.text),
            `${shortId(row.pick)} · ${row.effort}`, row.by === 'jev' ? 'Jev' : 'local', row.current || '–',
            row.feedback ? h('span', { className: 'harp-muted' }, row.feedback === 'ok' ? 'right' : `should be ${shortId(row.feedback)}`)
              : fixing === row.id
                ? h(Pick, { value: '', placeholder: 'Should be…', label: `Correct pick: ${row.text}`, onChange: v => v && vote(row, v),
                    options: s.candidates.filter(c => candId(c) !== row.pick).map(c => ({ value: candId(c), label: shortId(candId(c)) })) })
                : h('span', { className: 'harp-row-actions' },
                    h(Button, { size: 'sm', variant: 'ghost', 'aria-label': `Right pick: ${row.text}`, onClick: () => vote(row, '') }, h(Codicon, { name: 'thumbsup', size: '0.8rem' })),
                    h(Button, { size: 'sm', variant: 'ghost', 'aria-label': `Wrong pick: ${row.text}`, onClick: () => setFixing(row.id) }, 'Wrong'))] })) })
        : h('div', { className: 'harp-empty' }, 'No messages routed yet. Shadow mode records a pick for each new message.')))
}

function RowActions({ row, canAct, onAction }) {
  const icon = (name, label, type) => h(Button, { size: 'sm', variant: 'ghost', title: label, 'aria-label': `${label}: ${row.label}`,
    disabled: !canAct || !row.id, onClick: () => onAction(type, row) }, h(Codicon, { name, size: '0.85rem' }))
  return h('span', { className: 'harp-row-actions' },
    row.keyed ? icon('key', 'Replace key', 'relogin') : icon('key', 'Sign in again', 'relogin'),
    row.parked ? icon('debug-start', 'Unpark', 'unpark') : icon('debug-pause', 'Park', 'park'),
    icon('calendar', row.schedule ? `Schedule: ${row.schedule}` : 'Schedule', 'schedule'),
    icon('trash', 'Remove', 'remove'))
}

function AccountState({ row }) {
  const parts = []
  if (row.parked) parts.push(row.parked.until ? `parked until ${stamp(row.parked.until * 1000, { dateStyle: 'medium', timeStyle: 'short' })}` : 'parked')
  else parts.push(row.status)
  if (row.schedule) parts.push(`schedule ${row.schedule}`)
  return h('span', { className: 'harp-status' }, parts.join(' · '))
}

const SESSION = /session|5\s*h|hour/i
const WEEK = /week/i
// Hermes records only failures (exhausted, dead); no recorded error means the account is usable.
const usable = row => !row.parked && !['exhausted', 'dead'].includes(row.status)

function pickWindow(row, pattern, fallback) {
  const windows = row.usage?.windows || []
  return windows.find(w => pattern.test(w.label || '')) || windows[fallback] || null
}

function resetText(iso) {
  if (!iso) return '—'
  return stamp(iso, { weekday: 'short', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function KeyCheck({ row }) {
  const check = row.usage?.check
  if (!check) return h('span', { className: 'harp-muted' }, 'Not checked yet')
  const works = check.ok
  return h('span', { className: 'harp-account' }, h('span', { className: `harp-dot${works ? '' : ' off'}`, 'aria-hidden': true }),
    works ? `Key works · ${check.ms} ms` : `Key ${check.problem}`)
}

// Every clock time on the page is 24-hour.
const stamp = (value, options) => new Date(value).toLocaleString(undefined, { ...options, hourCycle: 'h23' })

// Time left until a usage window resets, counting down ("resets in 3h 50m").
function countdown(iso, now = Date.now()) {
  const left = Math.floor((Date.parse(iso) - now) / 60000)
  if (!Number.isFinite(left)) return ''
  if (left <= 0) return 'resetting…'
  const d = Math.floor(left / 1440), hr = Math.floor((left % 1440) / 60), m = left % 60
  return `resets in ${d ? `${d}d ${hr}h` : hr ? `${hr}h ${String(m).padStart(2, '0')}m` : `${m}m`}`
}

function WindowCell({ row, window, name, now }) {
  if (!row.usage) return h('span', { className: 'harp-muted' }, 'Not read yet')
  if (!window) return h('span', { className: 'harp-muted' }, '—')
  return h('div', { className: 'harp-window' },
    h(Meter, { percent: window.used_percent, label: `${row.label} ${name}` }),
    h('span', { className: 'harp-reset', title: window.reset_at ? resetText(window.reset_at) : undefined },
      window.reset_at ? countdown(window.reset_at, now) : 'not running'))
}

function Accounts({ query, canAct = false, onAction = () => {}, keyed = false }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 30000); return () => clearInterval(timer) }, [])
  const data = query.data
  const rows = (data?.accounts || []).map(row => ({ ...row, keyed }))
  const readable = rows.filter(row => row.usage)
  const top = (pattern, fallback) => readable.reduce((best, row) => {
    const value = pickWindow(row, pattern, fallback)?.used_percent
    return Number.isFinite(value) && (!best || value > best.value) ? { value, label: row.label } : best
  }, null)
  const topSession = top(SESSION, 0)
  const topWeek = top(WEEK, 1)
  const inRotation = rows.filter(row => usable(row)).length
  const tokens = rows.reduce((sum, row) => sum + (row.tokens_24h || 0), 0)
  const requests24h = rows.reduce((sum, row) => sum + (row.requests_24h || 0), 0)
  const price24h = rows.some(row => row.price_24h != null) ? rows.reduce((sum, row) => sum + (row.price_24h || 0), 0) : null
  return h('div', null,
    h(QueryState, { query, label: 'accounts and quota' }),
    data && h('div', null,
      keyed && h('div', { className: 'harp-banner' }, h(Codicon, { name: 'info', size: '0.8rem' }),
        h('span', null, 'TypeSafe has no usage or balance API, so Live usage checks each key (free model list call). Balance and spend are in the ',
          h('a', { href: TYPESAFE_CONSOLE, onClick: event => { event.preventDefault(); openExternal ? openExternal(TYPESAFE_CONSOLE) : window.open(TYPESAFE_CONSOLE) } }, 'TypeSafe console'), '.')),
      keyed ? h('div', { className: 'harp-cards' },
        h(SummaryCard, { title: 'In rotation', value: `${inRotation} / ${rows.length}`, detail: inRotation === rows.length ? 'all usable' : `${rows.length - inRotation} parked or blocked` }),
        h(SummaryCard, { title: 'Keys working', value: rows.filter(r => r.usage?.check).length ? `${rows.filter(r => r.usage?.check?.ok).length} / ${rows.filter(r => r.usage?.check).length}` : '–', detail: 'from the last key check' }),
        h(SummaryCard, { title: 'Models', value: (rows.find(r => r.usage?.check?.models?.length)?.usage.check.models || []).join(', ') || '–', detail: 'offered to these keys' }),
        h(SummaryCard, {
          title: 'Tokens, last 24 h', value: tokens ? compact.format(tokens) : '–',
          detail: tokens ? null : 'counting starts with the next request',
          lines: tokens ? [price24h != null && `$${usd.format(price24h)} at API prices`, requests24h ? `${number.format(requests24h)} requests` : null] : []
        }))
      : h('div', { className: 'harp-cards' },
        h(SummaryCard, { title: 'In rotation', value: `${inRotation} / ${rows.length}`, detail: inRotation === rows.length ? 'all usable' : `${rows.length - inRotation} parked or blocked` }),
        h(SummaryCard, { title: 'Highest session', value: topSession ? `${number.format(topSession.value)}%` : '–', detail: topSession ? topSession.label : 'read live usage', percent: topSession?.value }),
        h(SummaryCard, { title: 'Highest week', value: topWeek ? `${number.format(topWeek.value)}%` : '–', detail: topWeek ? topWeek.label : 'read live usage', percent: topWeek?.value }),
        h(SummaryCard, {
          title: 'Tokens, last 24 h', value: tokens ? compact.format(tokens) : '–',
          detail: tokens ? null : 'counting starts with the next request',
          lines: tokens ? [price24h != null && `$${usd.format(price24h)} at API prices`, requests24h ? `${number.format(requests24h)} requests` : null] : []
        })),
      h(Section, { title: 'POOL ACCOUNTS' }, data.accounts.length
        ? keyed ? h(Table, { label: 'Pool accounts', headings: ['#', 'Key', 'Check', 'Tokens, 24 h', 'Used by', 'State', ''], rows: rows.map((row, index) => ({ key: row.id || index, cells: [index + 1,
              h('span', { className: 'harp-account' }, h('span', { className: `harp-dot${usable(row) ? '' : ' off'}`, 'aria-hidden': true }), row.label),
              h(KeyCheck, { row }),
              h('span', { className: 'harp-num' }, row.tokens_24h ? compact.format(row.tokens_24h) : '–'),
              h('span', { className: 'harp-status' }, row.used_by?.length ? row.used_by.join(', ') : 'idle'),
              h(AccountState, { row }),
              h(RowActions, { row, canAct, onAction })] })) })
        : h(Table, { label: 'Pool accounts', headings: ['#', 'Account', 'Session (5 h)', 'Week', 'Tokens, 24 h', 'Used by', 'State', ''], rows: rows.map((row, index) => {
            const session = pickWindow(row, SESSION, 0)
            const week = pickWindow(row, WEEK, 1)
            return { key: row.id || index, cells: [index + 1,
              h('span', { className: 'harp-account' }, h('span', { className: `harp-dot${usable(row) ? '' : ' off'}`, 'aria-hidden': true }), row.label),
              h(WindowCell, { row, window: session, name: 'session', now }),
              h(WindowCell, { row, window: week, name: 'week', now }),
              h('span', { className: 'harp-num' }, row.tokens_24h ? compact.format(row.tokens_24h) : '–'),
              h('span', { className: 'harp-status' }, row.used_by?.length ? row.used_by.join(', ') : 'idle'),
              h(AccountState, { row }),
              h(RowActions, { row, canAct, onAction })] }
          }) })
        : h('div', { className: 'harp-empty' }, keyed ? 'No Type-safe keys yet. Use Add account to paste one.' : 'No accounts configured for this provider.'))))
}

// ---------------------------------------------------------------- Summary: every provider at a glance
function LeftMeter({ used, label }) {
  if (!Number.isFinite(used)) return h('span', { className: 'harp-muted' }, 'No reading')
  const left = Math.max(0, Math.min(100, Math.round(100 - used)))
  return h('div', { className: 'harp-quota', 'aria-label': `${label}: ${left}% left` },
    h('span', { className: `harp-bar${left <= 10 ? ' bad' : left <= 25 ? ' warn' : ''}`, 'aria-hidden': true }, h('i', { style: { width: `${left}%` } })),
    h('span', null, `${left}% left`))
}

function LeftCell({ row, window, name, now }) {
  if (!row.usage) return h('span', { className: 'harp-muted' }, 'Not read yet')
  if (!window) return h('span', { className: 'harp-muted' }, '—')
  return h('div', { className: 'harp-window' },
    h(LeftMeter, { used: window.used_percent, label: `${row.label} ${name}` }),
    h('span', { className: 'harp-reset', title: window.reset_at ? resetText(window.reset_at) : undefined },
      window.reset_at ? countdown(window.reset_at, now) : 'not running'))
}

const SUMMARY_PROVIDERS = [['anthropic', 'ANTHROPIC'], ['openai-codex', 'CODEX'], ['jev', 'TYPE-SAFE']]

function useProviderAccounts(provider) {
  const query = useQuery({ queryKey: [ID, 'accounts', provider], queryFn: () => rest(`/accounts?provider=${encodeURIComponent(provider)}`), refetchInterval: 60000 })
  const liveAt = query.data?.live_read_at
  useEffect(() => {   // same read-only refresh as the provider tabs, at most every 5 minutes
    if (!query.data || (liveAt && Date.now() / 1000 - liveAt < LIVE_MAX_AGE)) return
    if (Date.now() - (liveRequested[provider] || 0) < LIVE_MAX_AGE * 1000) return
    liveRequested[provider] = Date.now()
    post('/pool/live', { provider }).then(() => query.refetch(), () => {})
  }, [provider, !!query.data, liveAt])
  return query
}

function ProviderSummary({ provider, title, query, now, onOpen }) {
  const rows = query.data?.accounts || []
  const keyed = KEY_PROVIDERS.includes(provider)
  const label = h(Fragment, null, title, h('span', { className: 'harp-grow' }),
    h(Button, { size: 'sm', variant: 'ghost', onClick: () => onOpen(provider) }, h(Codicon, { name: 'link-external', size: '0.8rem' }), `Open ${title.toLowerCase()}`))
  if (!query.data) return h(Section, { title: label }, h(QueryState, { query, label: title.toLowerCase() }))
  if (!rows.length) return h(Section, { title: label }, h('div', { className: 'harp-empty' }, 'No accounts.'))
  const money = row => (row.price_24h != null ? `$${usd.format(row.price_24h)}` : '–')
  const reqs = row => (row.requests_24h ? number.format(row.requests_24h) : '–')
  const toks = row => (row.tokens_24h ? compact.format(row.tokens_24h) : '–')
  const name = row => h('span', { className: 'harp-account' }, h('span', { className: `harp-dot${usable(row) ? '' : ' off'}`, 'aria-hidden': true }), row.label)
  return h(Section, { title: label },
    keyed
      ? h(Table, { label: title, headings: ['Key', 'Check', 'Tokens, 24 h', 'Cost', 'Requests'], rows: rows.map((row, i) => ({ key: row.id || i,
          cells: [name(row), h(KeyCheck, { row }), toks(row), money(row), reqs(row)] })) })
      : h(Table, { label: title, headings: ['Account', 'Session (5 h)', 'Week', 'Tokens, 24 h', 'Cost', 'Requests'], rows: rows.map((row, i) => ({ key: row.id || i,
          cells: [name(row), h(LeftCell, { row, window: pickWindow(row, SESSION, 0), name: 'session', now }),
            h(LeftCell, { row, window: pickWindow(row, WEEK, 1), name: 'week', now }), toks(row), money(row), reqs(row)] })) }))
}

function ActivityHeat() {
  const [span, setSpan] = useState('week')
  const query = useQuery({ queryKey: [ID, 'heat', span], queryFn: () => rest(`/activity/heat?span=${span}`), refetchInterval: 300000 })
  const days = query.data?.days || []
  const top = Math.max(0, ...days.flatMap(day => day.tokens))
  return h(Section, { title: 'ACTIVITY' },
    h('div', { className: 'harp-tools' }, h('span', { className: 'harp-grow' }),
      h(SegmentedControl, { options: [{ id: 'day', label: '24 h' }, { id: 'week', label: 'Week' }, { id: 'month', label: 'Month' }], value: span, onChange: setSpan })),
    h(QueryState, { query, label: 'activity' }),
    days.length ? h('div', { className: 'harp-heat-wrap' }, h('div', { className: 'harp-heat' },
      h('span'), ...Array.from({ length: 24 }, (_, hr) => h('span', { key: `h${hr}` }, hr % 3 ? '' : String(hr).padStart(2, '0'))),
      ...days.flatMap(day => [h('span', { key: `l${day.date}`, title: day.date }, day.dow),
        ...day.tokens.map((value, hr) => h('i', { key: `${day.date}-${hr}`, className: 'harp-heat-cell',
          title: `${day.dow} ${day.date} ${String(hr).padStart(2, '0')}:00 · ${number.format(value)} tokens`,
          style: value ? { background: `color-mix(in srgb, var(--ui-accent) ${Math.round(20 + 80 * value / (top || 1))}%, transparent)` } : undefined }))]))) : null)
}

function SummaryView({ onOpen }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 30000); return () => clearInterval(timer) }, [])
  const queries = SUMMARY_PROVIDERS.map(([provider]) => useProviderAccounts(provider))
  const routerQuery = useQuery({ queryKey: [ID, 'router'], queryFn: () => rest('/router'), refetchInterval: 60000 })
  const all = queries.flatMap((q, i) => (q.data?.accounts || []).map(row => ({ ...row, provider: SUMMARY_PROVIDERS[i][0] })))
  const tokens = all.reduce((sum, row) => sum + (row.tokens_24h || 0), 0)
  const requests = all.reduce((sum, row) => sum + (row.requests_24h || 0), 0)
  const price = all.some(row => row.price_24h != null) ? all.reduce((sum, row) => sum + (row.price_24h || 0), 0) : null
  const quota = all.filter(row => !KEY_PROVIDERS.includes(row.provider))
  const usableCount = quota.filter(row => usable(row)).length
  let lowest = null
  for (const row of quota) for (const [pattern, fallback, kind] of [[SESSION, 0, '5 h'], [WEEK, 1, 'week']]) {
    const w = pickWindow(row, pattern, fallback)
    if (w && Number.isFinite(w.used_percent) && (!lowest || 100 - w.used_percent < lowest.left))
      lowest = { left: Math.round(100 - w.used_percent), label: row.label, kind, reset: w.reset_at }
  }
  const jev = routerQuery.data?.jev
  const [, jevTitle] = KEY_TEXT[jev?.state] || ['', 'Jev key not checked yet']
  return h('div', null,
    h('div', { className: 'harp-cards' },
      h(SummaryCard, { title: 'Tokens, last 24 h', value: tokens ? compact.format(tokens) : '–',
        detail: tokens ? null : 'counting starts with the next request',
        lines: tokens ? [price != null && `$${usd.format(price)} at API prices`, requests ? `${number.format(requests)} requests` : null] : [] }),
      h(SummaryCard, { title: 'Accounts usable', value: `${usableCount} / ${quota.length}`, detail: 'anthropic and Codex' }),
      h(SummaryCard, { title: 'Least left', value: lowest ? `${lowest.left}%` : '–', percent: lowest ? 100 - lowest.left : undefined,
        detail: lowest ? `${lowest.label} · ${lowest.kind}${lowest.reset ? ` · ${countdown(lowest.reset, now)}` : ''}` : 'read live usage' }),
      h(SummaryCard, { title: 'Jev', value: jev?.state === 'ok' ? 'ready' : jev?.state || '–', detail: jev?.state === 'ok' ? `${jevTitle} · ${jev.label}` : jevTitle })),
    h(ActivityHeat),
    ...SUMMARY_PROVIDERS.map(([provider, title], i) => h(ProviderSummary, { key: provider, provider, title, query: queries[i], now, onOpen })))
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
        h('label', { className: 'harp-muted' }, 'Tag ', h(Pick, { value: selectedTag, label: 'Tag', options: [{ value: '', label: 'All tags' }, ...tags], onChange: setTag })),
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

function KeyDialog({ provider, replace, onClose, onDone }) {
  const [label, setLabel] = useState('')
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true); setError('')
    try {
      const result = await post('/pool/add-key', replace ? { provider, replace: replace.id, key: key.trim() } : { provider, label: label.trim(), key: key.trim() })
      notify('success', `${result.replaced ? 'Replaced the key of' : 'Added'} ${result.account}.`)
      onDone()
    } catch (e) { setError(failure(e)) } finally { setBusy(false) }
  }
  const footer = h(Button, { size: 'sm', 'data-action': 'save-key', disabled: busy || !key.trim() || (!replace && !label.trim()), onClick: save },
    busy ? 'Checking key…' : replace ? 'Replace key' : 'Add key')
  return h(ActionDialog, { title: replace ? `Replace key: ${replace.label}` : 'Add Type-safe API key', onClose, footer },
    h('p', null, replace
      ? 'Paste the new key. It keeps this entry\'s name, position and schedule; the old key is removed after TypeSafe accepts the new one.'
      : 'Paste a TypeSafe API key from the console. HARP checks it with TypeSafe (free model list call) and saves it in Hermes\' pool. Nothing is saved if TypeSafe rejects it.'),
    !replace && h('label', { className: 'harp-field' }, 'Name'),
    !replace && h(Input, { value: label, placeholder: 'team, personal…', 'aria-label': 'Account name', onChange: event => setLabel(event.target.value) }),
    h('label', { className: 'harp-field' }, 'API key'),
    h(Input, { type: 'password', autoComplete: 'off', spellCheck: false, value: key, placeholder: 'ts-…', 'aria-label': 'API key', onChange: event => setKey(event.target.value) }),
    h('div', null, h(Button, { size: 'sm', variant: 'link', onClick: () => openExternal ? openExternal(TYPESAFE_CONSOLE) : window.open(TYPESAFE_CONSOLE) }, 'Get a key in the TypeSafe console')),
    error && h('div', { className: 'harp-error', role: 'alert' }, error))
}

function AddDialog({ provider, replace, onClose, onDone }) {
  if (KEY_PROVIDERS.includes(provider)) return h(KeyDialog, { provider, replace, onClose, onDone })
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
    ? h(Button, { size: 'sm', 'data-action': 'start-signin', disabled: busy || (!replace && !label.trim()),
        onClick: () => act('/signin/start', replace ? { provider, replace: replace.id } : { provider, label: label.trim() }, setFlow) },
        busy ? 'Starting…' : 'Start sign-in')
    : flow.kind === 'code'
      ? h(Button, { size: 'sm', 'data-action': 'finish-signin', disabled: busy || !code.trim(),
          onClick: () => act('/signin/step', { flow: flow.flow, code }, finish) }, busy ? 'Checking…' : 'Finish')
      : null
  return h(ActionDialog, { title: replace ? `Sign in again: ${replace.label}` : `Add ${provider} account`, onClose: cancel, footer },
    !flow && replace && h('p', null, 'Hermes signs this account in again. The new login keeps its name, position and schedule; the old login is deleted only after the sign-in succeeds. Sign in with the same account.'),
    !flow && !replace && h('div', null,
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

const PERIODS = [{ id: '24', label: '24 hours' }, { id: '72', label: '3 days' }, { id: '168', label: '7 days' }]
const when = ts => stamp(ts * 1000, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
const EVENT_ICON = { balance: 'arrow-swap', alert: 'bell', park: 'debug-pause', unpark: 'debug-start', schedule: 'calendar',
  remove: 'trash', add: 'add', relogin: 'key', wake: 'zap' }

function useView(kind, provider, hours, interval) {
  return useQuery({ queryKey: [ID, kind, provider, hours], refetchInterval: interval,
    queryFn: () => rest(`/${kind}?provider=${encodeURIComponent(provider)}${hours ? `&hours=${hours}` : ''}`) })
}

function ActivityView({ provider }) {
  const [hours, setHours] = useState('24')
  const query = useView('activity', provider, hours, 60000)
  const events = query.data?.events || []
  return h('div', null,
    h('div', { className: 'harp-tools' }, h(SegmentedControl, { options: PERIODS, value: hours, onChange: setHours }),
      'What HARP changed: balance, park, schedules, sign-ins, wake and alerts. Kept 30 days.'),
    h(QueryState, { query, label: 'activity' }),
    query.data && (events.length
      ? h(Table, { label: 'Activity', headings: ['When', 'By', 'What happened'], rows: events.map((e, i) => ({ key: `${e.ts}-${i}`, cells: [
          when(e.ts), h('span', { className: 'harp-status' }, e.by),
          h('span', { className: 'harp-account' }, h(Codicon, { name: EVENT_ICON[e.kind] || 'circle-small', size: '0.8rem' }), e.text)] })) })
      : h('div', { className: 'harp-empty' }, 'Nothing recorded in this period.')))
}

function GroupTable({ title, label, rows, extra }) {
  if (!rows?.length) return null
  return h(Section, { title }, h(Table, { label: title, headings: [label, 'Tokens', 'Share', 'Requests', ...(extra ? extra[0] : [])],
    rows: rows.map(row => ({ key: row.name, cells: [row.name === '?' ? h('span', { className: 'harp-muted' }, 'not recorded') : row.name,
      h('span', { className: 'harp-num' }, compact.format(row.tokens)),
      h('span', null, h('span', { className: 'harp-share' }, h('i', { style: { width: `${Math.min(100, row.share)}%` } })), `${Math.round(row.share)}%`),
      h('span', { className: 'harp-num' }, number.format(row.requests)), ...(extra ? extra[1](row) : [])] })) }))
}

function HeatMap({ chart }) {
  const top = Math.max(0, ...chart.flatMap(day => day.tokens))
  return h(Section, { title: 'TOKENS BY HOUR, LAST 7 DAYS' }, h('div', { className: 'harp-heat-wrap' }, h('div', { className: 'harp-heat' },
    h('span'), ...Array.from({ length: 24 }, (_, hr) => h('span', { key: `h${hr}` }, hr % 3 ? '' : String(hr).padStart(2, '0'))),
    ...chart.flatMap(day => [h('span', { key: `l${day.date}`, title: day.date }, day.dow),
      ...day.tokens.map((value, hr) => h('i', { key: `${day.date}-${hr}`, className: 'harp-heat-cell',
        title: `${day.dow} ${day.date} ${String(hr).padStart(2, '0')}:00 · ${number.format(value)} tokens`,
        style: value ? { background: `color-mix(in srgb, var(--ui-accent) ${Math.round(20 + 80 * value / (top || 1))}%, transparent)` } : undefined }))]))))
}

function ReportView({ provider }) {
  const [hours, setHours] = useState('24')
  const query = useView('report', provider, hours, 300000)
  const data = query.data
  const busiest = data && data.chart.flatMap(day => day.tokens.map((value, hr) => ({ value, hr }))).reduce((a, b) => (b.value > a.value ? b : a), { value: 0, hr: 0 })
  return h('div', null,
    h('div', { className: 'harp-tools' }, h(SegmentedControl, { options: PERIODS, value: hours, onChange: setHours }),
      'Counted by HARP on this computer. No provider call.'),
    h(QueryState, { query, label: 'report' }),
    data && h('div', null,
      h('div', { className: 'harp-cards' },
        h(SummaryCard, { title: 'Tokens', value: data.total.tokens ? compact.format(data.total.tokens) : '–', detail: `${number.format(data.total.requests)} requests` }),
        h(SummaryCard, { title: 'Accounts used', value: String(data.accounts.length), detail: data.accounts[0] ? `most: ${data.accounts[0].name}` : 'none yet' }),
        h(SummaryCard, { title: 'Busiest hour, 7 days', value: busiest.value ? `${String(busiest.hr).padStart(2, '0')}:00` : '–', detail: busiest.value ? `${compact.format(busiest.value)} tokens` : 'no load yet' }),
        h(SummaryCard, { title: 'Usage history', value: String(data.snapshots), detail: data.snapshots ? 'snapshots from the HARP Timer' : 'turn on alerts to record it' })),
      h(GroupTable, { title: 'BY ACCOUNT', label: 'Account', rows: data.accounts, extra: [['Peak', 'Time ≥ 90%'], row => [
        h('span', { className: 'harp-num' }, row.peak == null ? '–' : `${Math.round(row.peak)}%`),
        h('span', { className: 'harp-num' }, row.peak == null ? '–' : `${row.hot_min} min`)]] }),
      h(GroupTable, { title: 'BY MODEL', label: 'Model', rows: data.models }),
      h(GroupTable, { title: 'BY SOURCE', label: 'Source', rows: data.sources }),
      data.sessions.length ? h(Section, { title: 'BY SESSION' }, h(Table, { label: 'By session', headings: ['Session', 'Source', 'Account', 'Model', 'Tokens', 'Requests'],
        rows: data.sessions.map(s => ({ key: s.session, cells: [s.session, s.platform, s.accounts.join(' → '), s.models.join(', '),
          h('span', { className: 'harp-num' }, compact.format(s.tokens)), h('span', { className: 'harp-num' }, number.format(s.requests))] })) })) : null,
      h(HeatMap, { chart: data.chart }),
      !data.total.tokens && h('div', { className: 'harp-empty' }, 'No tokens counted in this period. Sessions started before HARP was enabled count after they restart.')))
}

function HealthView({ provider }) {
  const query = useView('health', provider, '', 60000)
  const data = query.data
  return h('div', null,
    h(QueryState, { query, label: 'health' }),
    data && h('div', null,
      h('div', { className: 'harp-cards' },
        h(SummaryCard, { title: 'HARP Timer', value: data.timer.installed ? 'active' : 'off', detail: data.timer.last_tick ? `last run ${when(data.timer.last_tick)}` : data.timer.needed ? 'needed but not installed' : 'not needed' }),
        h(SummaryCard, { title: 'Last usage read', value: data.live_read_at ? when(data.live_read_at) : 'none', detail: 'Accounts & usage reads it' }),
        h(SummaryCard, { title: 'Active alerts', value: String(data.alerts), detail: data.alerts ? 'see Alerts' : 'none' }),
        h(SummaryCard, { title: 'Problems', value: String(data.checks.filter(c => c.level !== 'ok').length), detail: 'from local state, no provider call' })),
      h('div', { className: 'harp-tools' }, h(Button, { size: 'sm', variant: 'outline', disabled: query.isFetching, onClick: () => query.refetch() },
        h(Codicon, { name: 'pulse', size: '0.8rem' }), query.isFetching ? 'Checking…' : 'Check again')),
      h(Section, { title: 'CHECKS' }, ...data.checks.map((c, i) => h('div', { className: 'harp-check', key: i },
        h('i', { className: c.level === 'ok' ? '' : c.level, 'aria-label': c.level }), h('span', null, c.text))))))
}

function AlertsView({ provider }) {
  const query = useView('alerts', provider, '', 60000)
  const [busy, setBusy] = useState(false)
  const [threshold, setThreshold] = useState('')
  const data = query.data
  const save = async changes => {
    setBusy(true)
    try {
      const result = await post('/alerts', changes)
      if (result.warning) notify('warning', result.warning)
      query.refetch()
    } catch (error) { notify('error', `Alerts: ${failure(error)}`) } finally { setBusy(false) }
  }
  const test = async () => {
    setBusy(true)
    try { const result = await post('/alerts/test', {}); notify(result.sent.length ? 'success' : 'warning', result.sent.length ? `Test sent: ${result.sent.join(', ')}` : 'No channel delivered the test.') }
    catch (error) { notify('error', failure(error)) } finally { setBusy(false) }
  }
  if (!data) return h(QueryState, { query, label: 'alerts' })
  const s = data.settings
  const level = threshold || String(s.threshold)
  const levelOk = /^\d+$/.test(level) && Number(level) >= 50 && Number(level) <= 100
  return h('div', null,
    h('div', { className: 'harp-tools' }, `The HARP Timer reads every account each 5 minutes while a channel is on, and alerts once when a 5-hour window or weekly limit reaches ${s.threshold}%, once when every account is there, and once when an account needs to sign in again.`),
    h('div', { className: 'harp-setting' }, h('div', { className: 'harp-grow' }, h('b', null, 'Desktop notifications'), h('span', null, 'Windows notifications on this computer.')),
      h(Switch, { checked: s.desktop, disabled: busy, 'aria-label': 'Desktop notifications', onCheckedChange: value => save({ desktop: value }) })),
    h('div', { className: 'harp-setting' }, h('div', { className: 'harp-grow' }, h('b', null, 'Telegram'), h('span', null, 'To the Telegram bot\'s home channel through hermes send (send /sethome to the bot first).')),
      h(Switch, { checked: s.telegram, disabled: busy, 'aria-label': 'Telegram alerts', onCheckedChange: value => save({ telegram: value }) })),
    h('div', { className: 'harp-setting' }, h('div', { className: 'harp-grow' }, h('b', null, 'Alert at'), h('span', null, 'Usage percent, 50 to 100.')),
      h(Input, { value: level, disabled: busy, 'aria-label': 'Alert threshold', onChange: event => setThreshold(event.target.value),
        onBlur: () => { if (levelOk && Number(level) !== s.threshold) save({ threshold: Number(level) }) } }), '%'),
    h('div', { className: 'harp-tools' }, h(Button, { size: 'sm', variant: 'outline', disabled: busy || !(s.desktop || s.telegram), onClick: test },
      h(Codicon, { name: 'bell', size: '0.8rem' }), 'Send a test alert'),
      data.timer_installed ? 'HARP Timer active.' : (s.desktop || s.telegram) ? 'HARP Timer is not installed: save a setting again.' : 'HARP Timer off.'),
    h(Section, { title: 'ACTIVE NOW' }, data.active.length
      ? data.active.map(a => h('div', { className: 'harp-check', key: a.key }, h('i', { className: 'warn' }), h('span', { className: 'harp-grow' }, a.text), h('span', { className: 'harp-status' }, `since ${when(a.since)}`)))
      : h('div', { className: 'harp-empty' }, 'None.')))
}

function localInput(epoch) {
  const date = new Date(epoch)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}

function ParkDialog({ row, busy, onClose, onConfirm }) {
  const [until, setUntil] = useState('')
  const when = until ? new Date(until).getTime() : null
  const invalid = when !== null && !(when > Date.now())
  return h(ActionDialog, { title: `Park ${row.label}`, onClose, footer:
    h(Button, { size: 'sm', 'data-action': 'confirm-park', disabled: !!busy || invalid,
      onClick: () => onConfirm(when ? Math.floor(when / 1000) : null) }, busy ? 'Parking…' : 'Park') },
    h('p', null, 'A parked account stays in the pool with its login but is out of rotation: new sessions do not start on it. Running sessions keep their account.'),
    h('label', { className: 'harp-field' }, 'Return automatically at (empty: stay parked until you unpark it)'),
    h(Input, { type: 'datetime-local', value: until, min: localInput(Date.now()), 'aria-label': 'Park until', onChange: event => setUntil(event.target.value) }),
    invalid && h('div', { className: 'harp-error', role: 'alert' }, 'Choose a time in the future.'))
}

function ScheduleDialog({ row, busy, onClose, onSave }) {
  const [every, setEvery] = useState(row.schedule || '')
  return h(ActionDialog, { title: `Schedule for ${row.label}`, onClose, footer: h('span', { className: 'harp-row-actions' },
    row.schedule && h(Button, { size: 'sm', variant: 'outline', disabled: !!busy, onClick: () => onSave('') }, 'Remove schedule'),
    h(Button, { size: 'sm', 'data-action': 'save-schedule', disabled: !!busy || !every.trim(), onClick: () => onSave(every.trim()) },
      busy ? 'Saving…' : 'Save')) },
    h('p', null, 'Park this account every week during these hours. HARP checks every 5 minutes with a Windows Task Scheduler task ("HARP Timer"), and returns the account to rotation when the window ends.'),
    h('label', { className: 'harp-field' }, 'Days and hours, such as "mon-fri 09:00-17:00" or "sat,sun 22:00-06:00"'),
    h(Input, { value: every, placeholder: 'mon-fri 09:00-17:00', 'aria-label': 'Schedule', onChange: event => setEvery(event.target.value) }))
}

function RemoveDialog({ row, busy, onClose, onConfirm }) {
  return h(ActionDialog, { title: `Remove ${row.label}?`, onClose, footer:
    h(Button, { size: 'sm', variant: 'destructive', 'data-action': 'confirm-remove', disabled: !!busy, onClick: onConfirm },
      busy ? 'Removing…' : 'Remove') },
    h('p', null, 'Its saved login is deleted from Hermes\' pool. To use it again, sign in again with Add account. To pause it instead, park it.'))
}

function HarpPage() {
  const [top, setTop] = useState('summary')
  const [view, setView] = useState('usage')
  const [tag, setTag] = useState('')
  const [dialog, setDialog] = useState(null)
  const [busy, setBusy] = useState('')
  const [target, setTarget] = useState(null)
  const [lastProvider, setLastProvider] = useState('anthropic')
  const diagram = top === 'diagram' || top === 'summary'   // overview pages: no provider tools or views
  const provider = diagram ? lastProvider : top
  const pickTop = value => { setTop(value); if (value !== 'diagram' && value !== 'summary') setLastProvider(value) }
  const accounts = useQuery({ queryKey: [ID, 'accounts', provider], queryFn: () => rest(`/accounts?provider=${encodeURIComponent(provider)}`), refetchInterval: 60000 })
  const usage = useQuery({ queryKey: [ID, 'usage'], queryFn: () => rest('/usage'), refetchInterval: 60000 })
  const canAct = ACTION_PROVIDERS.includes(provider)
  const keyed = KEY_PROVIDERS.includes(provider)
  const liveAt = accounts.data?.live_read_at
  useEffect(() => {
    // Fill each row's session and week bars without a click: a read-only usage read, at most every 5 minutes.
    if (!canAct || !accounts.data || (liveAt && Date.now() / 1000 - liveAt < LIVE_MAX_AGE)) return
    if (Date.now() - (liveRequested[provider] || 0) < LIVE_MAX_AGE * 1000) return
    liveRequested[provider] = Date.now()
    post('/pool/live', { provider }).then(() => accounts.refetch(), () => {})
  }, [provider, canAct, !!accounts.data, liveAt])
  const run = async (name, path, body, report) => {
    setBusy(name)
    try { report(await post(path, body)); setDialog(null); setTarget(null); accounts.refetch() }
    catch (error) { notify('error', `${name} failed: ${failure(error)}`) }
    finally { setBusy('') }
  }
  const onAction = (type, row) => {
    if (type === 'unpark') return run('Unpark', '/pool/unpark', { provider, id: row.id }, () => notify('success', `${row.label} is back in rotation.`))
    setTarget({ type, row })
  }
  const closeTarget = () => setTarget(null)
  const tool = (label, icon, props) => h(Button, { size: 'sm', variant: 'ghost', disabled: !canAct || !!busy, ...props },
    h(Codicon, { name: icon, size: '0.8rem' }), label)
  return h('main', { className: 'harp-page' },
    h('header', { className: 'harp-head' },
      h('h1', { className: 'harp-title' }, 'HARP'),
      !diagram && accounts.data && h('span', { className: 'harp-pill' }, accounts.data.accounts.length),
      h(SegmentedControl, { options: [
        { id: 'summary', label: 'Summary' }, { id: 'diagram', label: 'Diagram' }, { id: 'jev', label: 'Type-safe' }, { id: 'anthropic', label: 'anthropic' }, { id: 'openai-codex', label: 'Codex' }
      ], value: top, onChange: pickTop }),
      h('span', { className: 'harp-grow' }),
      !diagram && h(Button, { size: 'sm', variant: 'ghost', 'aria-label': 'Refresh', disabled: accounts.isFetching || usage.isFetching,
        onClick: () => { accounts.refetch(); usage.refetch() } }, h(Codicon, { name: 'eye', size: '0.8rem' })),
      !diagram && tool('Live usage', 'refresh', { onClick: () => run('Live usage', '/pool/live', { provider }, () => notify('success', 'Live usage updated.')) }),
      !diagram && tool('Wake', 'zap', { disabled: provider !== 'anthropic' || !!busy, onClick: () => setDialog('wake') }),
      !diagram && tool('Balance now', 'arrow-swap', { variant: 'outline', disabled: keyed || !canAct || !!busy, title: keyed ? 'Type-safe keys have no usage percent to balance by' : undefined, onClick: () => setDialog('balance') }),
      !diagram && tool('Add account', 'add', { variant: 'default', onClick: () => setDialog('add') })),
    !diagram && h('div', { className: 'harp-sub' },
      h(SegmentedControl, { options: [{ id: 'usage', label: 'Accounts & usage' }, { id: 'activity', label: 'Activity' },
        { id: 'report', label: 'Report' }, { id: 'health', label: 'Health' }, { id: 'alerts', label: 'Alerts' }], value: view, onChange: setView }),
      view === 'usage' && h('span', null, accounts.data?.live_read_at ? `Live usage read ${stamp(accounts.data.live_read_at * 1000, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}` : 'Live usage not read yet')),
    h('div', { className: 'harp-body' }, top === 'summary' ? h(SummaryView, { onOpen: pickTop }) : diagram ? h(RouterView, { onAddKey: () => pickTop('jev') })
      : view === 'activity' ? h(ActivityView, { provider })
        : view === 'report' ? h(ReportView, { provider })
          : view === 'health' ? h(HealthView, { provider })
            : view === 'alerts' ? h(AlertsView, { provider })
              : h('div', null, h(Accounts, { query: accounts, canAct: canAct && !busy, onAction, keyed }), h(Usage, { query: usage, tag, setTag }))),
    target?.type === 'relogin' && h(AddDialog, { provider, replace: target.row, onClose: closeTarget, onDone: () => { closeTarget(); accounts.refetch() } }),
    target?.type === 'park' && h(ParkDialog, { row: target.row, busy, onClose: closeTarget,
      onConfirm: until => run('Park', '/pool/park', { provider, id: target.row.id, until }, () => notify('success', `Parked ${target.row.label}.`)) }),
    target?.type === 'schedule' && h(ScheduleDialog, { row: target.row, busy, onClose: closeTarget,
      onSave: every => run('Schedule', '/pool/schedule', { provider, id: target.row.id, every }, result =>
        notify(result.warning ? 'warning' : 'success', result.warning || (every ? `Schedule saved: ${every}` : 'Schedule removed.'))) }),
    target?.type === 'remove' && h(RemoveDialog, { row: target.row, busy, onClose: closeTarget,
      onConfirm: () => run('Remove', '/pool/remove', { provider, id: target.row.id }, result => notify('success', `Removed ${result.removed}.`)) }),
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
