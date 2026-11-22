import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'

const source = readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
  .replace(/^import .*$/gm, '')
  .replace('export default {', 'globalThis.plugin = {')

function fixture(usage, session = {}) {
  const requests = []
  const slots = ['anthropic', 'usage', '']
  let index = 0
  let quotaEvent
  let busyListener
  const dispose = []
  const results = [
    { data: { accounts: [{ id: 'acc-1', label: 'Personal', status: 'ok', tokens_24h: 32500000, used_by: [], usage: { peak: 68, windows: [
      { label: 'Current session', used_percent: 0, reset_at: null },
      { label: 'Current week', used_percent: 68, reset_at: '2026-11-24T06:00:00+07:00' }] } }],
      windows: [{ label: '5h', used_percent: 25, reset_at: null }], live_read_at: 1 }, refetch() {} },
    { data: usage, refetch() {} }
  ]
  const pass = type => ({ children, ...props }) => ({ type, props, children: children || [] })
  const sandbox = {
    Intl, Date, Set, JSON, Math, Number, String, document: {
      head: { appendChild() {} }, createElement: () => ({ dataset: {}, remove() {} })
    },
    Fragment: ({ children }) => children,
    host: { navigate: path => requests.push(path), notify: message => requests.push({ notify: message }),
      request: async (op, args) => { requests.push({ op, ...args }); return {} },
      state: session.id ? {
        focusedSessionId: { get: () => session.id },
        busyBySession: { get: () => ({ [session.id]: session.busy }), listen: fn => { busyListener = fn } },
      } : undefined },
    sdk: { COMPOSER_AREAS: { underside: 'composer.underside' } },
    setTimeout, clearTimeout, setInterval, clearInterval,
    PALETTE_AREA: 'palette', ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'sidebar',
    Button: ({ children, ...props }) => ({ type: 'button', props, children }),
    Codicon: () => null,
    Dialog: pass('dialog'), DialogContent: pass('div'), DialogHeader: pass('header'), DialogFooter: pass('footer'), DialogTitle: pass('h2'),
    Input: props => ({ type: 'input', props, children: [] }),
    useEffect: () => {},
    SegmentedControl: ({ options, value, onChange }) => ({ type: 'div', props: { role: 'group' },
      children: options.map(option => ({ type: 'button', props: { onClick: () => onChange(option.id), 'aria-pressed': value === option.id }, children: [option.label] })) }),
    useQuery: options => {
      requests.push(options)
      const kind = options.queryKey[1]
      return kind === 'usage' ? results[1] : kind === 'accounts' ? (results.accountsByProvider?.[options.queryKey[2]] || results[0]) : (results[kind] || { data: undefined })
    },
    Switch: props => ({ type: 'switch', props, children: [] }),
    useState: initial => { const slot = index++; return [slots[slot] ?? initial, value => { slots[slot] = value }] },
    h: (type, props, ...children) => ({ type, props: props || {}, children }),
    console
  }
  vm.runInNewContext(source, sandbox, { filename: 'desktop/plugin.js' })
  const registrations = []
  sandbox.plugin.register({ rest: (path, options) => { requests.push(options ? { path, ...options } : path); return Promise.resolve({ changed: false, results: [], message: 'ok' }) }, os: {}, onDispose(fn) { dispose.push(fn) }, onEvent(name, fn) { if (name === 'plugin.harp.quota') quotaEvent = fn }, registerMany: entries => registrations.push(...entries) })
  function expand(node) {
    if (Array.isArray(node)) return node.map(expand)
    if (node == null || typeof node !== 'object') return node
    if (typeof node.type === 'function') return expand(node.type({ ...node.props, children: node.children }))
    return { ...node, children: node.children.map(expand) }
  }
  const render = () => { index = 0; return expand(registrations.find(x => x.id === 'page').render()) }
  const nodes = (tree, predicate) => {
    const matches = []
    function visit(node) {
      if (Array.isArray(node)) { for (const item of node) visit(item); return }
      if (node == null || typeof node !== 'object') return
      if (predicate(node)) matches.push(node)
      for (const child of node.children || []) visit(child)
    }
    visit(tree)
    return matches
  }
  const flatten = list => list.flatMap(c => (Array.isArray(c) ? flatten(c) : [c]))
  const text = tree => nodes(tree, n => typeof n.type === 'string').flatMap(n => flatten(n.children).filter(c => typeof c === 'string')).join(' ')
  return { registrations, requests, render, nodes, text, slots, results,
    quotaEvent, onBusy: () => busyListener?.(), dispose: () => dispose.forEach(fn => fn()),
    renderQuota: id => { index = 0; return expand(registrations.find(x => x.id === id).render()) },
    countdown: (...args) => sandbox.countdown(...args) }
}

test('the focused chat can ignore, toggle and explicitly apply its quota route', async (t) => {
  const session = { id: 'chat', busy: true }
  const app = fixture({}, session)
  t.after(app.dispose)
  const quota = { provider: 'anthropic', used: 93, auto: true, pick: { provider: 'openai-codex', model: 'gpt-sol', effort: 'medium' } }
  app.quotaEvent({ session_id: 'other', quota })
  assert.equal(app.renderQuota('quota-notice'), null)
  app.quotaEvent({ session_id: 'chat', quota })
  assert.match(app.text(app.renderQuota('quota-pane')), /93% used/)
  let strip = app.renderQuota('quota-notice')
  assert.equal(button(app, strip, 'Switch now').props.disabled, true)
  button(app, strip, 'Auto-switch on').props.onClick()
  assert.ok(button(app, app.renderQuota('quota-notice'), 'Auto-switch off'))
  button(app, app.renderQuota('quota-notice'), 'Ignore').props.onClick()
  app.quotaEvent({ session_id: 'chat', quota })
  assert.equal(app.renderQuota('quota-notice'), null)
  app.quotaEvent({ session_id: 'chat', quota: null })
  app.quotaEvent({ session_id: 'chat', quota })
  session.busy = false
  app.onBusy()
  await button(app, app.renderQuota('quota-notice'), 'Switch now').props.onClick()
  assert.deepEqual(app.requests.filter(x => x.op === 'config.set').map(x => [x.session_id, x.key, x.value]), [
    ['chat', 'model', 'gpt-sol --provider openai-codex --session'], ['chat', 'reasoning', 'medium']
  ])
})

test('Delegation saves a manual work route and Diagram shows the Hermes result', async () => {
  const app = fixture({ configured: false, usage: [] })
  const job = { name: 'Review PR', description: 'Review a proposed change', provider: 'openai-codex',
    model: 'gpt-6-sol', effort: 'medium' }
  app.results.delegation = { data: { settings: { classifier: 'local', jobs: [job] },
    models: { anthropic: ['opus'], 'openai-codex': ['gpt-6-sol'] },
    latest: { job: job.name, provider: job.provider, model: job.model, reasoning_effort: 'medium', source: 'manual' } }, refetch() {} }
  app.results.router = { data: routerData(), refetch() {} }
  button(app, app.render(), 'Delegation').props.onClick()
  const tree = app.render()
  const effort = app.nodes(tree, node => node.type === 'select' && node.props['aria-label'] === 'Work effort 1')[0]
  effort.props.onChange({ target: { value: 'high' } })
  await button(app, app.render(), 'Save delegation').props.onClick()
  const request = app.requests.find(item => item.path === '/delegation' && item.method === 'POST')
  assert.equal(request.body.jobs[0].model, 'gpt-6-sol')
  assert.equal(request.body.jobs[0].effort, 'high')
  button(app, app.render(), 'Diagram').props.onClick()
  assert.match(app.text(app.render()), /LATEST HERMES DELEGATION.*Review PR.*Manual/)
})

test('Quota pane shows two independent limits per account with bar and ring views', () => {
  const app = fixture({})
  app.slots[1] = Date.now()
  app.results.router = { data: routerData(), refetch() {} }
  app.results['live-routing'] = { data: { calls: [
    { id: 2, ts: 1795323120, kind: 'aux:next_prompt', provider: 'anthropic', model: 'claude-haiku', duration: 1.5, tokens: 150 },
    { id: 1, ts: 1795323110, kind: 'main', text: 'why does the app crash', provider: 'openai-codex', model: 'gpt-6-sol', source: 'jev', step: 1, effort: 'medium' },
  ] }, refetch() {} }
  const account = (id, label, windows) => ({ id, label, usage: { windows } })
  const reading = (label, used_percent, minutes) => ({
    label, used_percent, reset_at: minutes ? new Date(Date.now() + minutes * 60000 + 30000).toISOString() : null,
  })
  const anthropic = [
    account('a1', 'Claude Max', [reading('Current week', 78, 1560), reading('Current session', 91, 130)]),
    account('a2', 'Claude Pro', [reading('Current week', 24)]),
  ]
  const codex = [account('c1', 'Codex ID', [reading('5-hour', 40), reading('Weekly', 58)])]
  app.results.accountsByProvider = {
    anthropic: { data: { accounts: anthropic }, refetch() {} },
    'openai-codex': { data: { accounts: codex }, refetch() {} },
  }
  const before = app.renderQuota('quota-pane')
  assert.match(app.text(before), /User Tracker.*Live routing.*Settings/)
  assert.equal(app.nodes(before, node => node.type === 'details' && node.props.className === 'harp-tracker-provider').length, 2)
  const windows = tree => app.nodes(tree, node => node.props?.className === 'harp-tracker-window')
  assert.equal(windows(before).length, 6)
  assert.match(app.text(windows(before)[0]), /5-hour limit.*91%.*resets in 2h 10m/)
  assert.match(app.text(windows(before)[1]), /Weekly limit.*78%.*resets in 1d 2h/)
  assert.match(app.text(windows(before)[2]), /5-hour limit.*No reading/)
  assert.match(app.text(windows(before)[3]), /Weekly limit.*24%/)
  assert.match(app.text(windows(before)[3]), /Reset time unavailable/)
  assert.match(app.text(before), /Live routing.*aux:next_prompt.*claude-haiku.*why does the app crash.*main · Jev · step 1 · effort medium.*gpt-6-sol/)
  for (const detail of ['Codex', 'Anthropic', '1.5s', '150 tok']) {
    assert.ok(app.text(before).includes(detail), detail)
  }
  const style = app.nodes(before, node => node.type === 'select')[0]
  style.props.onChange({ target: { value: 'ring' } })
  const rings = app.nodes(app.renderQuota('quota-pane'), node => node.props?.role === 'meter')
  assert.deepEqual(rings.map(node => node.props['aria-valuenow']), [91, 78, 24, 40, 58])
  assert.ok(rings.some(node => node.props['aria-label'] === 'Codex · Codex ID · Weekly limit'))
  codex.push(account('c2', 'Other Codex', []))
  assert.equal(windows(app.renderQuota('quota-pane')).length, 8)
})

test('HARP toolbar exposes its own account actions without invoking account-pool', () => {
  const app = fixture({ configured: false, usage: [] })
  const page = app.render()
  for (const label of ['Live usage', 'Wake', 'Balance now', 'Add account']) {
    assert.equal(app.nodes(page, n => n.type === 'button' && n.children.includes(label)).length, 1, label)
  }
  assert.equal(app.requests.some(request => typeof request === 'string' && request.includes('account-pool')), false)
})

test('balance asks for confirmation before sending a write to HARP backend', async () => {
  const app = fixture({ configured: false, usage: [] })
  let page = app.render()
  app.nodes(page, n => n.type === 'button' && n.children.includes('Balance now'))[0].props.onClick()
  page = app.render()
  assert.match(app.text(page), /90%/)
  assert.equal(app.requests.some(request => request?.method === 'POST'), false)
  const confirm = app.nodes(page, n => n.props?.['data-action'] === 'confirm-balance')[0]
  assert.ok(confirm, 'confirmation button must exist')
  await confirm.props.onClick()
  assert.ok(app.requests.some(request => request?.path === '/pool/balance' && request.method === 'POST'))
})

const button = (app, page, label) => app.nodes(page, n => n.type === 'button' && n.children.includes(label))[0]

test('live usage reads the pool through HARP backend', async () => {
  const app = fixture({ configured: false, usage: [] })
  await button(app, app.render(), 'Live usage').props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/pool/live').body)), { provider: 'anthropic' })
  assert.equal(app.nodes(app.render(), n => n.props?.['aria-label'] === 'Personal week: 32% left').length, 1)
})

test('wake sends only the accounts picked after confirmation', async () => {
  const app = fixture({ configured: false, usage: [] })
  button(app, app.render(), 'Wake').props.onClick()
  let page = app.render()
  assert.equal(app.requests.some(r => r?.method === 'POST'), false)
  app.nodes(page, n => n.type === 'input' && n.props.type === 'checkbox')[0].props.onChange({ target: { checked: true } })
  page = app.render()
  await app.nodes(page, n => n.props?.['data-action'] === 'confirm-wake')[0].props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/pool/wake').body)), { provider: 'anthropic', ids: ['acc-1'] })
})

test('add account starts Hermes sign-in with the entered name', async () => {
  const app = fixture({ configured: false, usage: [] })
  button(app, app.render(), 'Add account').props.onClick()
  let page = app.render()
  app.nodes(page, n => n.type === 'input' && n.props['aria-label'] === 'Account name')[0].props.onChange({ target: { value: 'me@example.com' } })
  page = app.render()
  await app.nodes(page, n => n.props?.['data-action'] === 'start-signin')[0].props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/signin/start').body)), { provider: 'anthropic', label: 'me@example.com' })
})

test('account rows show 24 h tokens, sessions and their own row actions', async () => {
  const app = fixture({ configured: false, usage: [] })
  let page = app.render()
  assert.match(app.text(page), /32\.5M/)
  assert.match(app.text(page), /idle/)
  const rowButton = label => app.nodes(app.render(), n => n.type === 'button' && n.props['aria-label'] === `${label}: Personal`)[0]
  for (const label of ['Sign in again', 'Park', 'Schedule', 'Remove']) assert.ok(rowButton(label), label)

  rowButton('Remove').props.onClick()
  page = app.render()
  assert.equal(app.requests.some(r => r?.path === '/pool/remove'), false)
  await app.nodes(page, n => n.props?.['data-action'] === 'confirm-remove')[0].props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/pool/remove').body)), { provider: 'anthropic', id: 'acc-1' })

  rowButton('Park').props.onClick()
  await app.nodes(app.render(), n => n.props?.['data-action'] === 'confirm-park')[0].props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/pool/park').body)), { provider: 'anthropic', id: 'acc-1', until: null })

  rowButton('Sign in again').props.onClick()
  await app.nodes(app.render(), n => n.props?.['data-action'] === 'start-signin')[0].props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/signin/start').body)), { provider: 'anthropic', replace: 'acc-1' })
})

test('type-safe accounts are API keys: add, replace and check them, no wake or balance', async () => {
  const app = fixture({ configured: false, usage: [] })
  app.slots[0] = 'jev'
  app.results[0].data.accounts[0].usage = { peak: null, windows: [], check: { ok: true, status: 200, ms: 180, models: ['jev-latest'] } }
  let page = app.render()
  assert.equal(button(app, page, 'Wake').props.disabled, true)
  assert.equal(button(app, page, 'Balance now').props.disabled, true)
  assert.equal(button(app, page, 'Live usage').props.disabled, false)
  assert.match(app.text(page), /Key works/)
  assert.match(app.text(page), /jev-latest/)

  button(app, page, 'Add account').props.onClick()
  page = app.render()
  assert.equal(app.nodes(page, n => n.props?.['data-action'] === 'start-signin').length, 0)
  const key = app.nodes(page, n => n.type === 'input' && n.props['aria-label'] === 'API key')[0]
  assert.equal(key.props.type, 'password')
  app.nodes(page, n => n.type === 'input' && n.props['aria-label'] === 'Account name')[0].props.onChange({ target: { value: 'team' } })
  key.props.onChange({ target: { value: 'ts-secret' } })
  await app.nodes(app.render(), n => n.props?.['data-action'] === 'save-key')[0].props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/pool/add-key').body)), { provider: 'jev', label: 'team', key: 'ts-secret' })

  const replace = app.nodes(app.render(), n => n.type === 'button' && n.props['aria-label'] === 'Replace key: Personal')[0]
  assert.ok(replace)
})

test('account actions are disabled for providers HARP cannot manage', () => {
  const app = fixture({ configured: false, usage: [] })
  app.slots[0] = 'diagram'
  const page = app.render()
  for (const label of ['Live usage', 'Wake', 'Balance now', 'Add account']) assert.equal(app.nodes(page, n => n.type === 'button' && n.children.includes(label)).length, 0, label)
})

test('registers the Desktop page, navigation, quota strip and right pane', () => {
  const app = fixture({ configured: false, usage: [] })
  assert.deepEqual(Array.from(app.registrations, entry => entry.area), ['routes', 'sidebar', 'palette', 'composer.underside', 'panes'])
  assert.equal(app.registrations[0].data.path, '/harp')
  assert.equal(app.registrations[1].data.path, '/harp')
  app.registrations[2].data.run()
  assert.equal(app.requests.at(-1), '/harp')
  const page = app.render()
  assert.match(app.text(page), /Local usage collection is off/)
  assert.match(app.text(page), /Personal/)
  assert.equal(app.nodes(page, node => node.props?.className === 'harp-dot').length, 1)
})

test('an account with no recorded error counts as usable', () => {
  const app = fixture({ configured: false, usage: [] })
  for (const status of ['active', 'ok']) {
    app.results[0].data.accounts[0].status = status
    const page = app.render()
    assert.match(app.text(page), /1 \/ 1/, status)
    assert.equal(app.nodes(page, n => n.props?.className === 'harp-dot').length, 1, status)
  }
  app.results[0].data.accounts[0].status = 'exhausted'
  assert.match(app.text(app.render()), /0 \/ 1/)
})

test('each account row carries its own session and week quota; no separate provider table', () => {
  const app = fixture({ configured: false, usage: [] })
  const page = app.render()
  assert.equal(app.nodes(page, n => n.props?.['aria-label'] === 'Personal session: 100% left').length, 1)
  assert.equal(app.nodes(page, n => n.props?.['aria-label'] === 'Personal week: 32% left').length, 1)
  assert.doesNotMatch(app.text(page), /PROVIDER QUOTA/)
  const headings = app.nodes(page, n => n.type === 'th').map(n => app.text(n) || n.children.join(''))
  assert.equal(headings.some(x => /Resets/.test(x)), false)
  assert.match(app.text(page), /not running/)
  assert.match(app.text(page), /resets in \d+d \d+h/)

  assert.equal(app.nodes(page, n => n.props?.['aria-label'] === '5h: 75% left').length, 0)

  // The provider tab's summary cards use the same "left" framing as the Summary tab's "Least left".
  assert.doesNotMatch(app.text(page), /Highest session|Highest week/)
  assert.match(app.text(page), /Lowest session left/)
  assert.match(app.text(page), /Lowest week left/)
  assert.match(app.text(page), /100% left/)
  assert.match(app.text(page), /32% left/)
})

test('tokens card shows the estimated cost and request count', () => {
  const app = fixture({ configured: false, usage: [] })
  app.results[0].data.accounts[0].requests_24h = 424
  app.results[0].data.accounts[0].price_24h = 37.82
  const page = app.render()
  const text = app.text(page)
  assert.match(text, /\$37\.82 at API prices/)
  assert.match(text, /424 requests/)
})

test('tokens card without a price estimate just shows requests', () => {
  const app = fixture({ configured: false, usage: [] })
  app.results[0].data.accounts[0].requests_24h = 5
  app.results[0].data.accounts[0].price_24h = null
  assert.doesNotMatch(app.text(app.render()), /at API prices/)
})

test('reset countdown counts down and lives under its own bar', () => {
  const app = fixture({ configured: false, usage: [] })
  const now = Date.parse('2026-11-21T23:00:00+07:00')
  const c = app.countdown
  assert.equal(c('2026-11-21T23:42:00+07:00', now), 'resets in 42m')
  assert.equal(c('2026-11-22T02:50:00+07:00', now), 'resets in 3h 50m')
  assert.equal(c('2026-11-24T06:00:00+07:00', now), 'resets in 2d 7h')
  assert.equal(c('2026-11-21T22:00:00+07:00', now), 'resetting…')
})


test('filters tagged usage, shows budgets, switches provider without losing query scope', () => {
  const app = fixture({ configured: true, day: '2026-11-21', accounts: [
    { account: 'work', tokens: 20, quota_tokens: 100, requests: 2, unmetered: 0 }
  ], usage: [
    { tag: 'qa', account: 'work', provider: 'anthropic', model: 'fast', tokens: 20, requests: 2, unmetered: 0 },
    { tag: 'dev', account: 'work', provider: 'anthropic', model: 'main', tokens: 10, requests: 1, unmetered: 0 }
  ] })
  let page = app.render()
  assert.match(app.nodes(page, n => n.props?.['aria-label']?.startsWith('Daily token budget for work'))[0].props['aria-label'], /20 of 100/)
  assert.match(app.text(page), /fast/)
  const tagSelect = app.nodes(page, n => n.type === 'select')[0]
  tagSelect.props.onChange({ target: { value: 'qa' } })
  page = app.render()
  assert.match(app.text(page), /fast/)
  assert.doesNotMatch(app.text(page), /main/)
  app.nodes(page, n => n.type === 'button' && n.children.includes('Type-safe'))[0].props.onClick()
  app.render()
  assert.equal(app.requests.findLast(x => x?.queryKey?.[1] === 'accounts').queryKey[2], 'jev')
  assert.equal(app.requests.findLast(x => x?.queryKey?.[1] === 'usage').queryKey.length, 2)
})

const tab = (app, label) => app.nodes(app.render(), n => n.type === 'button' && n.children.includes(label))[0]

test('top row picks Diagram or a provider; the second row holds the five views', () => {
  const app = fixture({ configured: false, usage: [] })
  const page = app.render()
  const groups = JSON.parse(JSON.stringify(app.nodes(page, n => n.props?.role === 'group').map(g => g.children.map(b => b.children[0]))))
  assert.deepEqual(groups[0], ['Summary', 'Diagram', 'Delegation', 'Type-safe', 'anthropic', 'Codex'])
  assert.deepEqual(groups[1], ['Accounts & usage', 'Activity', 'Report', 'Health', 'Alerts'])
  app.results.router = { data: routerData(), refetch() {} }
  tab(app, 'Diagram').props.onClick()
  const diagram = app.render()
  assert.match(app.text(diagram), /Jev Thinking/)
  assert.equal(app.nodes(diagram, n => n.props?.role === 'group' && app.text(n).includes('Activity')).length, 0)
  assert.equal(app.nodes(diagram, n => n.type === 'button' && n.children.includes('Add account')).length, 0)
  tab(app, 'Codex').props.onClick()
  app.render()
  assert.equal(app.requests.findLast(x => x?.queryKey?.[1] === 'accounts').queryKey[2], 'openai-codex')
})

test('activity, report, health and alerts render their own data', async () => {
  const app = fixture({ configured: false, usage: [] })
  app.results.activity = { data: { events: [{ ts: 1795270000, kind: 'park', by: 'page', text: 'Parked Personal' }] } }
  app.results.report = { data: { hours: 24, total: { tokens: 1200, requests: 3 }, snapshots: 0,
    accounts: [{ name: 'Personal', tokens: 1200, requests: 3, share: 100, peak: null, hot_min: 0 }],
    models: [{ name: 'claude-opus', tokens: 1200, requests: 3, share: 100 }], sources: [{ name: 'desktop', tokens: 1200, requests: 3, share: 100 }],
    sessions: [{ session: 's1', platform: 'desktop', accounts: ['Personal'], models: ['claude-opus'], tokens: 1200, requests: 3, first: 1, last: 2 }],
    chart: Array.from({ length: 7 }, (_, i) => ({ date: `11-${i}`, dow: 'Mon', tokens: Array(24).fill(i), requests: Array(24).fill(0) })) } }
  app.results.health = { data: { checks: [{ level: 'warn', text: 'Tracker: no call counted in 24 h' }], timer: { needed: false, installed: false, last_tick: null }, live_read_at: null, alerts: 0 } }
  app.results.alerts = { data: { settings: { desktop: false, telegram: false, threshold: 90 }, timer_installed: false, active: [{ key: 'session:Personal', text: 'Personal 5-hour window at 95%', since: 1 }] } }

  tab(app, 'Activity').props.onClick()
  assert.match(app.text(app.render()), /Parked Personal/)
  tab(app, 'Report').props.onClick()
  let page = app.render()
  assert.match(app.text(page), /claude-opus/)
  assert.match(app.text(page), /BY SESSION/)
  assert.equal(app.nodes(page, n => n.props?.className === 'harp-heat-cell').length, 7 * 24)
  tab(app, 'Health').props.onClick()
  assert.match(app.text(app.render()), /Tracker: no call counted/)
  tab(app, 'Alerts').props.onClick()
  page = app.render()
  assert.match(app.text(page), /Personal 5-hour window at 95%/)
  await app.nodes(page, n => n.type === 'switch' && n.props['aria-label'] === 'Desktop notifications')[0].props.onCheckedChange(true)
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/alerts').body)), { desktop: true })
})


function routerData(over = {}) {
  return {
    settings: { enabled: true, apply: false, mode: 'balanced', limit: 85, jev_model: 'jev-latest', jev_daily_cap: 300, plans: { anthropic: { a1: 'x5' } },
      candidates: [{ provider: 'openai-codex', model: 'gpt-6-sol', description: 'code and bugs', cap: 'high' },
        { provider: 'anthropic', model: 'claude-sonnet-5', description: 'general', cap: 'medium' }] },
    jev: { state: 'ok', label: 'Tony', ms: 310 },
    loads: { anthropic: { used: 40, left: 3, capacity: 6 }, 'openai-codex': { used: 91, left: 0.1, capacity: 1 } },
    stats: { decisions: 12, local: 9, jev: 3, samples: 15, corrections_total: 1, rated: 4, right: 3, corrections: [{ from: 'openai-codex:gpt-6-sol', to: 'anthropic:claude-sonnet-5', times: 1 }] },
    recent: [{ id: 7, ts: 1, platform: 'desktop', text: 'why does the app crash', pick: 'openai-codex:gpt-6-sol', effort: 'medium', by: 'jev', confidence: 0.9, moved_from: null, current: 'claude-opus-5-5', applied: 0, feedback: null }],
    jev_calls_today: 3, samples: 15,
    accounts: { anthropic: [{ id: 'a1', label: 'Personal', peak: 40 }], 'openai-codex': [{ id: 'c1', label: 'Main', peak: 91 }] },
    models: { anthropic: ['claude-opus-5-5', 'claude-sonnet-5'], 'openai-codex': ['gpt-6-sol', 'gpt-6-mini'] },
    ...over
  }
}

function routerPage(data = routerData()) {
  const app = fixture({ configured: false, usage: [] })
  app.results.router = { data, refetch() {} }
  app.slots[0] = 'diagram'
  return app
}

test('diagram shows the router: key state, flow, candidates, load and learning', () => {
  const app = routerPage()
  const text = app.text(app.render())
  for (const part of [/Jev API found/, /Tony/, /Jev Thinking/, /Provider/, /Agent/, /Effort/, /gpt-6-sol/, /Shadow/, /91%/, /near its limit/, /why does the app crash/, /9 of 12/])
    assert.match(text, part)
})

test('no Jev key: the local learner decides and the page offers to add one', () => {
  const app = routerPage(routerData({ jev: { state: 'none' } }))
  const page = app.render()
  assert.match(app.text(page), /No Jev API key/)
  assert.ok(button(app, page, 'Add Jev API key'))
})

test('router edits are saved together', async () => {
  const app = routerPage()
  let page = app.render()
  button(app, page, 'Best').props.onClick()
  page = app.render()
  app.nodes(page, n => n.type === 'select' && n.props['aria-label'] === 'Plan: Personal')[0].props.onChange({ target: { value: 'x20' } })
  page = app.render()
  await app.nodes(page, n => n.props?.['data-action'] === 'save-router')[0].props.onClick()
  const body = JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/router/settings').body))
  assert.equal(body.mode, 'best')
  assert.equal(body.plans.anthropic.a1, 'x20')
  assert.equal(body.candidates.length, 2)
})

test('feedback and try-it reach the backend', async () => {
  const app = routerPage()
  let page = app.render()
  await app.nodes(page, n => n.type === 'button' && n.props['aria-label'] === 'Right pick: why does the app crash')[0].props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/router/feedback').body)), { id: 7, label: '' })
  page = app.render()
  app.nodes(page, n => n.type === 'input' && n.props['aria-label'] === 'Try a message')[0].props.onChange({ target: { value: 'fix my build' } })
  page = app.render()
  await button(app, page, 'Ask Jev').props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/router/try').body)), { text: 'fix my build', use_jev: true })
})


test('clock times are 24-hour', () => {
  const app = fixture({ configured: false, usage: [] })
  const text = app.text(app.render())
  assert.match(text, /Live usage read \d{2}:\d{2}:\d{2}/)
  assert.doesNotMatch(text, /\b[AP]M\b/)
})


function heatData(span = 'week') {
  const days = span === 'month' ? 30 : span === 'day' ? 1 : 7
  return { span, days: Array.from({ length: days }, (_, i) => ({
    date: `11-${String(i + 1).padStart(2, '0')}`, dow: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][i % 7],
    tokens: Array.from({ length: 24 }, (_, hr) => (i === days - 1 && hr === 22 ? 7000 : 0)),
    requests: Array.from({ length: 24 }, (_, hr) => (i === days - 1 && hr === 22 ? 3 : 0)) })) }
}

test('summary shows every provider with what is left, plus the tokens card', () => {
  const app = fixture({ configured: false, usage: [] })
  app.results[0].data.accounts[0].requests_24h = 424
  app.results[0].data.accounts[0].price_24h = 37.82
  app.results.router = { data: routerData(), refetch() {} }
  app.results.heat = { data: heatData(), refetch() {} }
  app.slots[0] = 'summary'
  const page = app.render()
  const text = app.text(page)
  for (const part of [/ANTHROPIC/, /CODEX/, /TYPE-SAFE/, /32% left/, /100% left/, /resets in \d+d \d+h/, /\$\d+\.\d{2} at API prices/, /requests/, /Jev API found/])
    assert.match(text, part)
  const keys = app.requests.filter(r => r?.queryKey?.[1] === 'accounts').map(r => r.queryKey[2])
  for (const p of ['anthropic', 'openai-codex', 'jev']) assert.ok(keys.includes(p), p)
  assert.equal(app.nodes(page, n => n.props?.role === 'group' && app.text(n).includes('Activity')).length, 0)
  assert.equal(app.nodes(page, n => n.type === 'button' && n.children.includes('Add account')).length, 0)
  assert.equal(app.nodes(page, n => n.props?.['aria-label'] === 'Personal week: 32% left').length, 2)
})


test('activity heat grid sits in Summary with a Week/Month toggle', () => {
  // Style B: discrete 5-level cells (GitHub-contribution look), a Total column per row,
  // and the same grid shape for 24 h / Week / Month (hours or weekdays as columns).
  const app = fixture({ configured: false, usage: [] })
  app.results.router = { data: routerData(), refetch() {} }
  app.results.heat = { data: heatData('week'), refetch() {} }
  app.slots[0] = 'summary'
  let page = app.render()
  assert.match(app.text(page), /ACTIVITY/)
  assert.ok(!button(app, page, 'Today'))
  assert.ok(button(app, page, 'Week'))
  assert.ok(button(app, page, 'Month'))
  let cells = app.nodes(page, n => n.props?.className?.startsWith('harp-gb-cell'))
  assert.equal(cells.length, 7 * 24)
  assert.ok(cells.every(c => /harp-gb-cell lvl-[0-4]/.test(c.props.className)))
  const hot = cells.find(c => c.props.title && c.props.title.includes('7,000 tokens'))
  assert.ok(hot)
  assert.match(hot.props.title, /Sat 11-07 22:00/)
  assert.match(hot.props.className, /lvl-4/)
  const totals = app.nodes(page, n => n.props?.className === 'harp-gb-total')
  assert.equal(totals.length, 7)
  assert.ok(totals.some(t => app.text(t).includes('7,000') || app.text(t).includes('7000')))

  app.results.heat = { data: heatData('month'), refetch() {} }
  button(app, page, 'Month').props.onClick()
  page = app.render()
  cells = app.nodes(page, n => n.props?.className?.startsWith('harp-gb-cell'))
  // Full weeks covering a rolling 30-day window: between 30 and 36 cells, always a multiple of 7,
  // with exactly (total - 30) dashed pad cells — exact counts shift with which weekday "today" is.
  assert.ok(cells.length % 7 === 0 && cells.length >= 30 && cells.length <= 36, cells.length)
  const dashed = cells.filter(c => c.props.className.includes('dashed'))
  assert.equal(dashed.length, cells.length - 30)
  const today = cells.find(c => c.props.className.includes('ring'))
  assert.ok(today)
  assert.match(today.props.title, /7,000 tokens/)
  const headerText = app.nodes(page, n => n.props?.className === 'harp-gb-head').map(n => n.children[0]).filter(t => t !== 'Total')
  assert.deepEqual(headerText, ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])

  assert.equal(cells.filter(c => c.props.className.includes('harp-gb-date')).length, 30)
})

test('HARP opens on Summary', () => {
  const app = fixture({ configured: false, usage: [] })
  app.results.router = { data: routerData(), refetch() {} }
  app.results.heat = { data: heatData(), refetch() {} }
  app.slots[0] = undefined
  const page = app.render()
  const summary = app.nodes(page, n => n.type === 'button' && n.children.includes('Summary'))[0]
  assert.equal(summary.props['aria-pressed'], true)
  assert.match(app.text(page), /ACTIVITY/)
})
