import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'

const source = readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
  .replace(/^import .*$/gm, '')
  .replace('export default {', 'globalThis.plugin = {')

function fixture(usage) {
  const requests = []
  const slots = ['anthropic', 'usage', '']
  let index = 0
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
    host: { navigate: path => requests.push(path), notify: message => requests.push({ notify: message }) },
    PALETTE_AREA: 'palette', ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'sidebar',
    Button: ({ children, ...props }) => ({ type: 'button', props, children }),
    Codicon: () => null,
    Dialog: pass('dialog'), DialogContent: pass('div'), DialogHeader: pass('header'), DialogFooter: pass('footer'), DialogTitle: pass('h2'),
    Input: props => ({ type: 'input', props, children: [] }),
    useEffect: () => {},
    SegmentedControl: ({ options, value, onChange }) => ({ type: 'div', props: { role: 'group' },
      children: options.map(option => ({ type: 'button', props: { onClick: () => onChange(option.id), 'aria-pressed': value === option.id }, children: [option.label] })) }),
    useQuery: options => { requests.push(options); return options.queryKey[1] === 'usage' ? results[1] : results[0] },
    useState: initial => { const slot = index++; return [slots[slot] ?? initial, value => { slots[slot] = value }] },
    h: (type, props, ...children) => ({ type, props: props || {}, children }),
    console
  }
  vm.runInNewContext(source, sandbox, { filename: 'desktop/plugin.js' })
  const registrations = []
  sandbox.plugin.register({ rest: (path, options) => { requests.push(options ? { path, ...options } : path); return Promise.resolve({ changed: false, results: [], message: 'ok' }) }, os: {}, onDispose() {}, registerMany: entries => registrations.push(...entries) })
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
  const text = tree => nodes(tree, n => typeof n.type === 'string').flatMap(n => n.children.filter(c => typeof c === 'string')).join(' ')
  return { registrations, requests, render, nodes, text, slots }
}

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
  assert.equal(app.nodes(app.render(), n => n.props?.['aria-label'] === 'Personal week: 68% used').length, 1)
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

test('account actions are disabled for providers HARP cannot manage', () => {
  const app = fixture({ configured: false, usage: [] })
  app.slots[0] = 'jev'
  const page = app.render()
  for (const label of ['Live usage', 'Wake', 'Balance now', 'Add account']) assert.equal(button(app, page, label).props.disabled, true, label)
})

test('registers one Desktop page, sidebar entry and palette navigation', () => {
  const app = fixture({ configured: false, usage: [] })
  assert.deepEqual(Array.from(app.registrations, entry => entry.area), ['routes', 'sidebar', 'palette'])
  assert.equal(app.registrations[0].data.path, '/harp')
  assert.equal(app.registrations[1].data.path, '/harp')
  app.registrations[2].data.run()
  assert.equal(app.requests.at(-1), '/harp')
  const page = app.render()
  assert.match(app.text(page), /Local usage collection is off/)
  assert.match(app.text(page), /Personal/)
  assert.equal(app.nodes(page, node => node.props?.className === 'harp-dot').length, 1)
})

test('each account row carries its own session and week quota; no separate provider table', () => {
  const app = fixture({ configured: false, usage: [] })
  const page = app.render()
  assert.equal(app.nodes(page, n => n.props?.['aria-label'] === 'Personal session: 0% used').length, 1)
  assert.equal(app.nodes(page, n => n.props?.['aria-label'] === 'Personal week: 68% used').length, 1)
  assert.match(app.text(page), /Resets/)
  assert.doesNotMatch(app.text(page), /PROVIDER QUOTA/)
  assert.equal(app.nodes(page, n => n.props?.['aria-label'] === '5h: 25% used').length, 0)
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
  app.nodes(page, n => n.type === 'button' && n.children.includes('jev'))[0].props.onClick()
  app.render()
  assert.equal(app.requests.findLast(x => x?.queryKey?.[1] === 'accounts').queryKey[2], 'jev')
  assert.equal(app.requests.findLast(x => x?.queryKey?.[1] === 'usage').queryKey.length, 2)
})
