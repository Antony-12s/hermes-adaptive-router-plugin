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
    { data: { accounts: [{ label: 'Personal', status: 'active' }], windows: [{ label: '5h', used_percent: 25, reset_at: null }] }, refetch() {} },
    { data: usage, refetch() {} }
  ]
  const sandbox = {
    Intl, Date, Set, JSON, Math, Number, document: {
      head: { appendChild() {} }, createElement: () => ({ dataset: {}, remove() {} })
    },
    host: { navigate: path => requests.push(path) },
    PALETTE_AREA: 'palette', ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'sidebar',
    useQuery: options => { requests.push(options); return options.queryKey[1] === 'usage' ? results[1] : results[0] },
    useState: initial => { const slot = index++; return [slots[slot] ?? initial, value => { slots[slot] = value }] },
    h: (type, props, ...children) => ({ type, props: props || {}, children }),
    console
  }
  vm.runInNewContext(source, sandbox, { filename: 'desktop/plugin.js' })
  const registrations = []
  sandbox.plugin.register({ rest: path => { requests.push(path); return Promise.resolve({}) }, onDispose() {}, registerMany: entries => registrations.push(...entries) })
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

test('registers one Desktop page, sidebar entry and palette navigation', () => {
  const app = fixture({ configured: false, usage: [] })
  assert.deepEqual(Array.from(app.registrations, entry => entry.area), ['routes', 'sidebar', 'palette'])
  assert.equal(app.registrations[0].data.path, '/harp')
  assert.equal(app.registrations[1].data.path, '/harp')
  app.registrations[2].data.run()
  assert.equal(app.requests.at(-1), '/harp')
  const page = app.render()
  assert.match(app.text(page), /Usage collection is off/)
  assert.match(app.text(page), /Personal/)
})

test('filters tagged usage, shows budgets, switches provider without losing query scope', () => {
  const app = fixture({ configured: true, day: '2026-11-21', accounts: [
    { account: 'work', tokens: 20, quota_tokens: 100, requests: 2, unmetered: 0 }
  ], usage: [
    { tag: 'qa', account: 'work', provider: 'anthropic', model: 'fast', tokens: 20, requests: 2, unmetered: 0 },
    { tag: 'dev', account: 'work', provider: 'anthropic', model: 'main', tokens: 10, requests: 1, unmetered: 0 }
  ] })
  let page = app.render()
  assert.equal(app.nodes(page, n => n.type === 'progress')[0].props['aria-label'], 'Daily token budget for work')
  assert.match(app.text(page), /fast/)
  const selects = app.nodes(page, n => n.type === 'select')
  selects[1].props.onChange({ target: { value: 'qa' } })
  page = app.render()
  assert.match(app.text(page), /fast/)
  assert.doesNotMatch(app.text(page), /main/)
  app.nodes(page, n => n.type === 'select')[0].props.onChange({ target: { value: 'jev' } })
  app.render()
  assert.equal(app.requests.findLast(x => x?.queryKey?.[1] === 'accounts').queryKey[2], 'jev')
  assert.equal(app.requests.findLast(x => x?.queryKey?.[1] === 'usage').queryKey.length, 2)
})
