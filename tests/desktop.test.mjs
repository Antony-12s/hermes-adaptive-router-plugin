import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'

const source = readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
  .replace(/^import .*$/gm, '')
  .replace('export default {', 'globalThis.plugin = {')

test('routing provider cards fill the available row without a fixed width cap', () => {
  const rule = source.match(/\.harp-load\{([^}]+)\}/)?.[1]
  assert.ok(rule, 'routing card grid must have a CSS rule')
  assert.doesNotMatch(rule, /max-width\s*:/, 'wide panes must not leave a blank strip')
  assert.match(rule, /grid-template-columns\s*:\s*repeat\(auto-fit,\s*minmax\(min\(100%,\s*280px\),\s*1fr\)\)/)
})

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
      { label: 'Current week', used_percent: 68, reset_at: new Date(Date.now() + (2 * 24 + 7) * 3600000 + 60000).toISOString() }] } }],
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
      request: async (op, args) => { requests.push({ op, ...args }); if (session.fail?.[op]) throw new Error(session.fail[op]); return session.reply?.[op] ?? {} },
      state: session.id ? {
        focusedSessionId: { get: () => session.id },
        busyBySession: { get: () => ({ [session.id]: session.busy }), listen: fn => { busyListener = fn } },
        focusedUsage: { get: () => session.usage ?? null, listen: () => () => {} },
      } : undefined },
    sdk: { COMPOSER_AREAS: { underside: 'composer.underside' } },
    window: { confirm: () => globalThis.__confirmAnswer ?? true },
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

test('auto-switch waits for a working chat, then switches as soon as it is idle', async (t) => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] })
  const session = { id: 'chat', busy: true }
  const app = fixture({}, session)
  t.after(app.dispose)
  const quota = { provider: 'openai-codex', used: 83, auto: true, pick: { provider: 'anthropic', model: 'claude-haiku-5-5', effort: 'low' } }
  app.quotaEvent({ session_id: 'chat', quota })
  t.mock.timers.tick(21000)
  assert.equal(app.requests.filter(x => x.op === 'config.set').length, 0)
  session.busy = false
  app.onBusy()
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(app.requests.filter(x => x.op === 'config.set').map(x => [x.session_id, x.key, x.value]), [
    ['chat', 'model', 'claude-haiku-5-5 --provider anthropic --session'], ['chat', 'reasoning', 'low']
  ])
})

test('Settings dropdowns use the themed picker, so every option is readable', () => {
  const app = fixture({ configured: false, usage: [] })
  app.results.compression = { data: { enabled: true, threshold: 0.5, presets: [0.5, 0.6, 0.75, 0.85] }, refetch() {} }
  app.results.router = { data: { settings: { apply: false } } }
  button(app, app.render(), 'Settings').props.onClick()
  const page = app.render()
  for (const label of ['Auto-compress context at', 'Quota display']) {
    assert.equal(app.nodes(page, n => n.type === 'select' && n.props['aria-label'] === label).length, 1, label)
  }
})

test('Settings shows the fallback chain and sets it up in one click', async () => {
  const app = fixture({ configured: false, usage: [] })
  app.results.router = { data: { settings: { apply: false } } }
  app.results.fallback = { data: { primary: 'anthropic', chain: [], suggested: [{ provider: 'nous', model: 'google/gemini-3.1-pro-preview' }] }, refetch() {} }
  button(app, app.render(), 'Settings').props.onClick()
  let page = app.render()
  assert.match(app.text(page), /PROVIDER FALLBACK/)
  assert.match(app.text(page), /No fallback/)
  await button(app, page, 'Set up fallback').props.onClick()
  assert.ok(app.requests.find(r => r?.path === '/fallback/setup' && r.method === 'POST'))
  app.results.fallback = { data: { primary: 'anthropic', chain: [{ provider: 'nous', model: 'google/gemini-3.1-pro-preview' }], suggested: [] }, refetch() {} }
  page = app.render()
  assert.match(app.text(page), /nous · google\/gemini-3.1-pro-preview/)
  assert.equal(button(app, page, 'Set up fallback').props.disabled, true)
})

test('the focused chat can ignore, toggle and explicitly apply its quota route', async (t) => {
  const session = { id: 'chat', busy: true }
  const app = fixture({}, session)
  t.after(app.dispose)
  const quota = { provider: 'anthropic', used: 93, auto: true, pick: { provider: 'openai-codex', model: 'gpt-sol', effort: 'medium' } }
  app.quotaEvent({ session_id: 'other', quota })
  assert.equal(app.renderQuota('quota-notice'), null)
  app.quotaEvent({ session_id: 'chat', quota })
  assert.match(app.text(app.renderQuota('quota-pane')), /Context.*Not reported yet.*93% used/)
  session.id = 'other'
  assert.doesNotMatch(app.text(app.renderQuota('quota-pane')), /93% used/)
  session.id = 'chat'
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

test('context card shows tokens, percent, the auto-compress mark and Compress now', (t) => {
  const app = fixture({ configured: false, usage: [] }, { id: 's1', busy: false,
    usage: { context_used: 124000, context_max: 200000, context_percent: 62, compressions: 2 } })
  t.after(app.dispose)
  app.results.compression = { data: { enabled: true, threshold: 0.75, presets: [0.5, 0.6, 0.75, 0.85] }, refetch() {} }
  const pane = app.renderQuota('quota-pane')
  const text = app.text(pane)
  assert.match(text, /124K \/ 200K tokens · 62%/)
  assert.match(text, /Auto-compresses at 75%/)
  assert.match(text, /compressed 2×/)
  const bar = app.nodes(pane, n => n.props?.role === 'progressbar')
  assert.equal(bar.length, 1)
  assert.equal(bar[0].props['aria-valuenow'], 62)
  const compress = app.nodes(pane, n => n.type === 'button' && app.text(n) === 'Compress now')
  assert.equal(compress.length, 1)
  assert.equal(compress[0].props.disabled, false)
  compress[0].props.onClick()
  assert.ok(app.requests.some(r => r.op === 'session.compress' && r.session_id === 's1'))
  assert.ok(!app.requests.some(r => r.op === 'session.usage'), 'session.usage is not available to desktop plugins')
})

test('after Compress now the context bar slides to 0, then reads the new count', async (t) => {
  const app = fixture({ configured: false, usage: [] }, { id: 's1', busy: false,
    usage: { context_used: 342000, context_max: 1000000, context_percent: 34, compressions: 0 } })
  t.after(app.dispose)
  app.results.compression = { data: { enabled: true, threshold: 0.5 }, refetch() {} }
  let pane = app.renderQuota('quota-pane')
  await app.nodes(pane, n => n.type === 'button' && app.text(n) === 'Compress now')[0].props.onClick()
  pane = app.renderQuota('quota-pane')
  const bar = app.nodes(pane, n => n.props?.role === 'progressbar')[0]
  assert.equal(bar.props['aria-valuenow'], 0)
  assert.match(bar.children[0].props.style.transition, /width \d+ms/)
  assert.match(app.text(pane), /0 \/ 1M tokens · reading again/)
  assert.doesNotMatch(app.text(pane), /timed out/)
})

test('a compress that outlasts the request timeout keeps saying it is compressing', async (t) => {
  const app = fixture({ configured: false, usage: [] }, { id: 's1', busy: false,
    usage: { context_used: 342000, context_max: 1000000, context_percent: 34, compressions: 0 },
    fail: { 'session.compress': 'request timed out after 30s: session.mutate' } })
  t.after(app.dispose)
  app.results.compression = { data: { enabled: true, threshold: 0.5 }, refetch() {} }
  let pane = app.renderQuota('quota-pane')
  await app.nodes(pane, n => n.type === 'button' && app.text(n) === 'Compress now')[0].props.onClick()
  pane = app.renderQuota('quota-pane')
  assert.match(app.text(pane), /Still compressing/)
  assert.doesNotMatch(app.text(pane), /timed out/)
  assert.equal(app.nodes(pane, n => n.type === 'button' && /Compressing/.test(app.text(n))).length, 1)
})

test('context card says so when Hermes has not reported a window, and disables Compress while busy', (t) => {
  const app = fixture({ configured: false, usage: [] }, { id: 's1', busy: true })
  t.after(app.dispose)
  app.results.compression = { data: { enabled: false }, refetch() {} }
  const pane = app.renderQuota('quota-pane')
  assert.match(app.text(pane), /Not reported yet/)
  assert.match(app.text(pane), /Auto-compress off/)
  const compress = app.nodes(pane, n => n.type === 'button' && app.text(n) === 'Compress now')
  assert.equal(compress[0].props.disabled, true)
})

test('settings offer the auto-compress presets and Off', (t) => {
  const app = fixture({ configured: false, usage: [] })
  t.after(app.dispose)
  app.results.compression = { data: { enabled: true, threshold: 0.75 }, refetch() {} }
  app.results.router = { data: routerData(), refetch() {} }
  app.slots[0] = 'settings'
  const text = app.text(app.render())
  assert.match(text, /Auto-compress context at/)
  assert.match(text, /50%.*60%.*75%.*85%.*Off/)
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
  const codex = [account('c1', 'Codex ID', [reading('5-hour', 100), reading('Weekly', 58)])]
  app.results.accountsByProvider = {
    anthropic: { data: { accounts: anthropic }, refetch() {} },
    'openai-codex': { data: { accounts: codex }, refetch() {} },
  }
  const before = app.renderQuota('quota-pane')
  assert.match(app.text(before), /User Tracker.*Live routing.*Context/)
  assert.equal(app.nodes(before, node => node.type === 'details' && node.props.className === 'harp-tracker-provider').length, 2)
  const windows = tree => app.nodes(tree, node => node.props?.className === 'harp-tracker-window')
  assert.equal(windows(before).length, 6)
  assert.match(app.text(windows(before)[0]), /5-hour limit.*9%.*resets in 2h 10m/)
  assert.match(app.text(windows(before)[1]), /Weekly limit.*22%.*resets in 1d 2h/)
  assert.match(app.text(windows(before)[2]), /5-hour limit.*No reading/)
  assert.match(app.text(windows(before)[3]), /Weekly limit.*76%/)
  assert.match(app.text(windows(before)[3]), /Reset time unavailable/)
  assert.match(app.text(windows(before)[4]), /5-hour limit.*0%/)
  assert.match(app.text(before), /Live routing.*Preview the Conversation.*claude-haiku.*aux:next_prompt.*anthropic.*effort not set · 1\.5s · 150 tok.*why does the app crash.*gpt-6-sol.*main · Jev · step 1.*Codex.*effort medium/)
  const liveRows = app.nodes(before, node => node.type === 'li' && node.props?.className === 'harp-live-call')
  assert.equal(liveRows.length, 2)
  for (const row of liveRows) assert.equal(app.nodes(row, node => node.props?.className?.startsWith('harp-live-line')).length, 3)
  for (const detail of ['Codex', 'Anthropic', '1.5s', '150 tok']) {
    assert.ok(app.text(before).includes(detail), detail)
  }
  app.slots[0] = 'summary'
  button(app, app.render(), 'Settings').props.onClick()
  const settings = app.render()
  assert.match(app.text(settings), /Provider weights.*Delegation.*Diagram.*Copy into a new Hermes chat/)
  const style = app.nodes(settings, node => node.type === 'select' && node.props.value === 'bar')[0]
  style.props.onChange({ target: { value: 'ring' } })
  const rings = app.nodes(app.renderQuota('quota-pane'), node => node.props?.role === 'meter')
  assert.deepEqual(rings.map(node => node.props['aria-valuenow']), [9, 22, 76, 0, 42])
  assert.ok(rings.some(node => node.props['aria-label'] === 'Codex · Codex ID · Weekly limit'))
  codex.push(account('c2', 'Other Codex', []))
  assert.equal(windows(app.renderQuota('quota-pane')).length, 8)
})

test('Quota Tracker offers an Anthropic display plan only when metadata is missing', async () => {
  const app = fixture({})
  app.results.accountsByProvider = {
    anthropic: { data: { accounts: [
      { id: 'a1', label: 'Detected', usage: { plan: 'Max 20x' }, display_plan: 'Pro' },
      { id: 'a2', label: 'Manual', display_plan: 'Max 5x' },
    ] }, refetch() {} },
    'openai-codex': { data: { accounts: [{ id: 'c1', label: 'Personal', usage: { plan: 'Plus' } }] }, refetch() {} },
  }
  const pane = app.renderQuota('quota-pane')
  assert.match(app.text(pane), /Anthropic Max 20x.*Detected.*Anthropic Max 5x.*Manual.*Codex Plus/)
  const plan = app.nodes(pane, node => node.type === 'select' && node.props['aria-label'] === 'Plan for Manual')[0]
  assert.equal(plan.props.value, 'Max 5x')
  assert.equal(app.nodes(pane, node => node.type === 'select' && /^Plan for/.test(node.props['aria-label'])).length, 1)
  await plan.props.onChange({ target: { value: 'Pro' } })
  const request = app.requests.find(item => item.path === '/pool/anthropic/display-plan')
  assert.equal(request.body.id, 'a2')
  assert.equal(request.body.plan, 'Pro')
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
  assert.deepEqual(groups[0], ['Summary', 'Diagram', 'Delegation', 'Type-safe', 'anthropic', 'Codex', 'API key', 'Settings'])
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

test('alerts tab can switch accounts automatically before one runs out', async () => {
  const app = fixture({ configured: false, usage: [] })
  app.results.alerts = { data: { settings: { desktop: false, telegram: false, threshold: 80, auto_balance: false }, timer_installed: false, active: [] } }
  tab(app, 'Alerts').props.onClick()
  const page = app.render()
  assert.match(app.text(page), /Switch accounts automatically/)
  const toggle = app.nodes(page, n => n.type === 'switch' && n.props['aria-label'] === 'Switch accounts automatically')[0]
  assert.equal(toggle.props.checked, false)
  await toggle.props.onCheckedChange(true)
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/alerts').body)), { auto_balance: true })
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
  app.results.delegation = { data: { settings: { classifier: 'local', jobs: [] }, models: {}, latest: null }, refetch() {} }
  app.slots[0] = 'diagram'
  return app
}

test('diagram shows the router: key state, flow, candidates, load and learning', () => {
  const app = routerPage()
  const text = app.text(app.render())
  for (const part of [/Jev API found/, /Tony/, /Jev Thinking/, /Provider/, /Agent/, /Effort/, /gpt-6-sol/, /Shadow/, /why does the app crash/, /9 of 12/])
    assert.match(text, part)
  assert.doesNotMatch(text, /Provider load limit/, 'focus and provider load moved to Delegation')
  app.slots[0] = 'delegation'
  const delegation = app.text(app.render())
  for (const part of [/HERMES DELEGATION/, /Provider load limit/, /91%/, /near its limit/]) assert.match(delegation, part)
})

const LADDER = {
  efforts: ['low', 'medium', 'high', 'xhigh', 'max'], modes: { save: 2, balanced: 4, best: 6 },
  tiers: ['trivial', 'easy', 'routine', 'hard', 'very hard', 'extreme', 'maximum'].map((name, tier) => ({
    tier, name,
    anthropic: { provider: 'anthropic', model: tier < 2 ? 'claude-haiku-5-5' : 'claude-opus-5-5', effort: tier ? 'xhigh' : 'low', cost: tier + 1 },
    'openai-codex': { provider: 'openai-codex', model: tier < 2 ? 'gpt-6-luna' : 'gpt-6.2-sol', effort: tier ? 'high' : 'low', cost: tier + 2 }
  }))
}

test('with the cost ladder on, Delegation shows the ladder and provider-only heavyweight; Diagram keeps the switch', async () => {
  const base = routerData()
  const app = routerPage(routerData({ ladder: LADDER, settings: { ...base.settings, ladder: true, heavyweight: '' } }))
  app.slots[0] = 'delegation'
  const delegation = app.render()
  const text = app.text(delegation)
  assert.match(text, /COST LADDER/)
  assert.match(text, /gpt-6-luna/)
  assert.match(text, /mode stops at tier 4/)
  const heavyweight = app.nodes(delegation, n => n.type === 'select' && n.props['aria-label'] === 'Heavyweight')[0]
  assert.deepEqual(app.nodes(heavyweight, n => n.type === 'option').map(n => n.props.value), ['', 'anthropic', 'openai-codex'])
  app.slots[0] = 'diagram'
  let page = app.render()
  assert.doesNotMatch(app.text(page), /COST LADDER|MODELS IT CAN PICK/)
  app.nodes(page, n => n.type === 'switch' && n.props['aria-label'] === 'Cost ladder')[0].props.onCheckedChange(false)
  page = app.render()
  await app.nodes(page, n => n.props?.['data-action'] === 'save-router')[0].props.onClick()
  const body = JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/router/settings').body))
  assert.equal(body.ladder, false)
})

test('no Jev key: the local learner decides and the page offers to add one', () => {
  const app = routerPage(routerData({ jev: { state: 'none' } }))
  const page = app.render()
  assert.match(app.text(page), /No Jev API key/)
  assert.ok(button(app, page, 'Add Jev API key'))
})

test('focus, plan weights and heavyweight are saved together from Delegation', async () => {
  const app = routerPage()
  app.slots[0] = 'delegation'
  let page = app.render()
  button(app, page, 'Best').props.onClick()
  page = app.render()
  app.nodes(page, n => n.type === 'select' && n.props['aria-label'] === 'Plan: Personal')[0].props.onChange({ target: { value: 'x20' } })
  page = app.render()
  const heavyweight = app.nodes(page, n => n.type === 'select' && n.props['aria-label'] === 'Heavyweight')[0]
  const choice = app.nodes(heavyweight, n => n.type === 'option' && !!n.props.value)[0].props.value
  heavyweight.props.onChange({ target: { value: choice } })
  page = app.render()
  await app.nodes(page, n => n.props?.['data-action'] === 'save-routing')[0].props.onClick()
  const body = JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/router/settings').body))
  assert.equal(body.mode, 'best')
  assert.equal(body.heavyweight, choice)
  assert.equal(body.plans.anthropic.a1, 'x20')
  assert.equal(body.candidates.length, 2)
})

test('feedback reaches the backend and Diagram has no sample runner', async () => {
  const app = routerPage()
  let page = app.render()
  await app.nodes(page, n => n.type === 'button' && n.props['aria-label'] === 'Right pick: why does the app crash')[0].props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/router/feedback').body)), { id: 7, label: '' })
  assert.doesNotMatch(app.text(app.render()), /TRY A MESSAGE|Ask Jev/)
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
  // Full weeks covering a rolling 30-day window: between 30 and 42 cells (6 weeks when the window starts on a Sunday), always a multiple of 7,
  // with exactly (total - 30) dashed pad cells — exact counts shift with which weekday "today" is.
  assert.ok(cells.length % 7 === 0 && cells.length >= 30 && cells.length <= 42, `month cells: ${cells.length}`)
  const dashed = cells.filter(c => c.props.className.includes('dashed'))
  assert.equal(dashed.length, cells.length - 30)
  const today = cells.find(c => c.props.className.includes('ring'))
  assert.ok(today)
  assert.match(today.props.title, /7,000 tokens/)
  const headerText = app.nodes(page, n => n.props?.className === 'harp-gb-head').map(n => n.children[0]).filter(t => t !== 'Total')
  assert.deepEqual(headerText, ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])

  assert.equal(cells.filter(c => c.props.className.includes('harp-gb-date')).length, 30)
})

test('summary keeps a running token total until Reset is confirmed', async (t) => {
  const app = fixture({ configured: false, usage: [] })
  t.after(app.dispose)
  app.results.router = { data: routerData(), refetch() {} }
  app.results.heat = { data: heatData(), refetch() {} }
  app.results['running-total'] = { data: { since: 1795323120, tokens: 1234567890, requests: 4321, price: 2345.6 }, refetch() {} }
  app.slots[0] = 'summary'
  let page = app.render()
  assert.match(app.text(page), /Tokens since reset.*1\.2B.*since .*\$2,345\.60 at API prices.*4,321 requests/)
  assert.equal(app.nodes(page, n => n.props?.className === 'harp-cards')[0].children.filter(Boolean).length, 5)
  assert.match(app.text(page), /Tokens since reset.*Tokens, last 24 h/, 'running total comes first')
  button(app, page, 'Reset').props.onClick()
  assert.ok(!app.requests.some(r => r?.path === '/tokens/total/reset'), 'first click only asks')
  page = app.render()
  await button(app, page, 'Reset to 0').props.onClick()
  assert.ok(app.requests.some(r => r?.path === '/tokens/total/reset' && r.method === 'POST'))
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

test('delegation lists saved work as rows and says so when there is none', () => {
  const app = fixture({ configured: false, usage: [] })
  app.results.delegation = { data: { settings: { classifier: 'local', jobs: [] }, models: { anthropic: ['opus'] }, latest: null }, refetch() {} }
  app.results.router = { data: routerData(), refetch() {} }
  app.slots[0] = 'delegation'
  assert.match(app.text(app.render()), /No saved work yet/)
  app.results.delegation.data.settings.jobs = [{ name: 'Review PR', description: 'Review', provider: 'anthropic', model: 'opus', effort: 'medium' }]
  assert.equal(app.nodes(app.render(), n => n.props?.className === 'harp-job').length, 1)
  assert.doesNotMatch(app.text(app.render()), /No saved work yet/)
})

function apiPage() {
  const app = fixture({ configured: false, usage: [] })
  const row = (id, label, env, configured, extra = {}) => ({ id, label, env, configured, key_source: configured ? env : '', use: false,
    cheap: `${id}-small`, strong: `${id}-big`, price: null, prices: { cheap: 0.25, strong: 1 }, models: [`${id}-big`, `${id}-small`],
    spent_today: 0, tokens_today: 0, requests_today: 0, unpriced_tokens: 0, ...extra })
  app.results['api-keys'] = { data: {
    settings: { enabled: false, daily_budget: 5, order: ['deepseek', 'zai', 'alibaba'], providers: {} },
    providers: [row('deepseek', 'DeepSeek', 'DEEPSEEK_API_KEY', true, { spent_today: 0.42, requests_today: 3 }),
      row('zai', 'GLM (Z.AI)', 'GLM_API_KEY', true, { prices: { cheap: null, strong: null }, unpriced_tokens: 120000, requests_today: 1 }),
      row('alibaba', 'Qwen (DashScope)', 'DASHSCOPE_API_KEY', false)],
    spent_today: 0.42, budget_left: 4.58 }, refetch() {} }
  app.slots[0] = 'api'
  return app
}

test('API key tab lists DeepSeek, GLM and Qwen with key state, prices and today\'s spend', () => {
  const app = apiPage()
  const page = app.render()
  const text = app.text(page)
  for (const part of [/API KEYS/, /DeepSeek/, /GLM \(Z\.AI\)/, /Qwen \(DashScope\)/, /Not set/, /\$0\.42 · 3 req/,
    /unknown: enter one/, /120K tokens unpriced/, /Keys found in Hermes/, /2 \/ 3/, /\$4\.58/])
    assert.match(text, part)
  assert.equal(app.nodes(page, n => n.type === 'switch' && n.props['aria-label'] === 'Use Qwen (DashScope)')[0].props.disabled, true)
  assert.equal(app.nodes(page, n => n.props?.['aria-label'] === 'Key: Qwen (DashScope)')[0].props.placeholder, 'Paste DASHSCOPE_API_KEY')
  assert.equal(app.nodes(page, n => n.type === 'switch' && n.props['aria-label'] === 'Use DeepSeek')[0].props.disabled, false)
})

test('API key settings are saved together: switch, budget, order, models and a price', async () => {
  const app = apiPage()
  let page = app.render()
  app.nodes(page, n => n.type === 'switch' && n.props['aria-label'] === 'Use API keys')[0].props.onCheckedChange(true)
  page = app.render()
  app.nodes(page, n => n.type === 'input' && n.props['aria-label'] === 'Daily budget')[0].props.onChange({ target: { value: '12' } })
  page = app.render()
  app.nodes(page, n => n.type === 'switch' && n.props['aria-label'] === 'Use DeepSeek')[0].props.onCheckedChange(true)
  page = app.render()
  app.nodes(page, n => n.type === 'input' && n.props['aria-label'] === 'Price: GLM (Z.AI)')[0].props.onChange({ target: { value: '1.2' } })
  page = app.render()
  app.nodes(page, n => n.type === 'button' && n.props['aria-label'] === 'Move up GLM (Z.AI)')[0].props.onClick()
  page = app.render()
  await app.nodes(page, n => n.props?.['data-action'] === 'save-api-keys')[0].props.onClick()
  const body = JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/api-keys' && r.method === 'POST').body))
  assert.equal(body.enabled, true)
  assert.equal(body.daily_budget, 12)
  assert.deepEqual(body.order, ['zai', 'deepseek', 'alibaba'])
  assert.equal(body.providers.deepseek.use, true)
  assert.equal(body.providers.deepseek.strong, 'deepseek-big')
  assert.equal(body.providers.zai.price, 1.2)
  assert.equal(body.providers.alibaba.use, false)
})

test('Delegation offers API providers that have a key', () => {
  const app = fixture({ configured: false, usage: [] })
  app.results.delegation = { data: { settings: { classifier: 'local', jobs: [{ name: 'Batch', description: '', provider: 'deepseek', model: 'deepseek-flash', effort: 'low' }] },
    models: { anthropic: ['opus'], 'openai-codex': ['sol'], deepseek: ['deepseek-flash'] },
    labels: { anthropic: 'anthropic', 'openai-codex': 'openai-codex', deepseek: 'DeepSeek' }, latest: null }, refetch() {} }
  app.results.router = { data: routerData(), refetch() {} }
  app.slots[0] = 'delegation'
  const page = app.render()
  const pick = app.nodes(page, n => n.type === 'select' && n.props['aria-label'] === 'Work provider 1')[0]
  assert.deepEqual(app.nodes(pick, n => n.type === 'option').map(n => n.props.value), ['deepseek', 'anthropic', 'openai-codex'])
  assert.match(app.text(pick), /DeepSeek/)
})

test('a pasted key is a password field, goes to Hermes once, and is cleared from the page', async () => {
  const app = apiPage()
  let page = app.render()
  const input = app.nodes(page, n => n.type === 'input' && n.props['aria-label'] === 'Key: Qwen (DashScope)')[0]
  assert.equal(input.props.type, 'password')
  assert.equal(input.props.autoComplete, 'off')
  input.props.onChange({ target: { value: 'sk-fake-test-key-000' } })
  page = app.render()
  await app.nodes(page, n => n.props?.['data-action'] === 'save-key-alibaba')[0].props.onClick()
  const sent = app.requests.find(r => r?.path === '/api-keys/key' && r.method === 'POST')
  assert.deepEqual(JSON.parse(JSON.stringify(sent.body)), { provider: 'alibaba', key: 'sk-fake-test-key-000' })
  page = app.render()
  assert.equal(app.nodes(page, n => n.type === 'input' && n.props['aria-label'] === 'Key: Qwen (DashScope)')[0].props.value, '')
  assert.doesNotMatch(JSON.stringify(app.requests.filter(r => r?.path !== '/api-keys/key')), /sk-fake-test-key/)
})

test('Save key stays disabled until something is pasted', () => {
  const app = apiPage()
  const page = app.render()
  assert.equal(app.nodes(page, n => n.props?.['data-action'] === 'save-key-deepseek')[0].props.disabled, true)
})

test('Remove key appears only for a key HARP added, and asks before removing', async () => {
  const app = apiPage()
  app.results['api-keys'].data.providers[0].removable = true
  const page = app.render()
  assert.equal(app.nodes(page, n => n.props?.['data-action'] === 'remove-key-zai').length, 0)
  const remove = app.nodes(page, n => n.props?.['data-action'] === 'remove-key-deepseek')[0]
  assert.ok(remove, 'remove button for a HARP-added key')
  globalThis.__confirmAnswer = false
  try {
    await remove.props.onClick()
    assert.ok(!app.requests.some(r => r?.path === '/api-keys/key/remove'), 'cancel sends nothing')
    globalThis.__confirmAnswer = true
    await remove.props.onClick()
    assert.deepEqual(JSON.parse(JSON.stringify(app.requests.find(r => r?.path === '/api-keys/key/remove').body)), { provider: 'deepseek' })
  } finally { delete globalThis.__confirmAnswer }
})
