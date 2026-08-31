import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const pagePath = new URL('../src/roadshow/RoadshowExperiencePage.tsx', import.meta.url)
const contextPath = new URL('../src/roadshow/RoadshowContext.tsx', import.meta.url)
const shellPath = new URL('../src/roadshow/RoadshowShell.tsx', import.meta.url)
const routerPath = new URL('../src/router/index.tsx', import.meta.url)
const stylePath = new URL('../src/styles.css', import.meta.url)

async function source(path) {
  return readFile(path, 'utf8')
}

test('Phase 5.8 roadshow route and real aggregate APIs are wired', async () => {
  const [page, router] = await Promise.all([source(pagePath), source(routerPath)])
  assert.ok(router.includes("path: '/roadshow'"))
  for (const path of [
    '/roadshow-experience/chains',
    '/events?view=',
    '/roadshow-experience/health',
  ]) assert.ok(page.includes(path), path)
})

test('roadshow session is session-only and never persists business state', async () => {
  const context = await source(contextPath)
  assert.ok(context.includes('window.sessionStorage'))
  assert.ok(context.includes('medtrust.roadshow.session'))
  assert.ok(context.includes('applicationId'))
  assert.ok(context.includes('currentNode'))
  assert.ok(context.includes('guideHidden'))
  assert.ok(!context.includes('window.localStorage'))
  for (const forbidden of ['artifactStatus', 'contractStatus', 'downloadToken', 'grantToken']) {
    assert.ok(!context.includes(forbidden), forbidden)
  }
})

test('roadshow exposes both modes, 12-node chain and next responsible role', async () => {
  const page = await source(pagePath)
  for (const label of [
    '精简流程',
    '完整流程',
    '切换到下一责任方',
    '当前讲解',
    '真实后台证据',
    '核心安全反差',
    '系统健康与预检',
  ]) assert.ok(page.includes(label), label)
  assert.ok(page.includes('detail.nodes.map'))
  assert.ok(page.includes('detail.next_role'))
  assert.ok(page.includes('currentNode'))
  assert.ok(page.includes("find((item) => item.status === 'active')"))
})

test('completed events stop polling and technical view remains explicit', async () => {
  const page = await source(pagePath)
  assert.ok(page.includes("detail.status === 'completed'"))
  assert.ok(page.includes('window.setInterval(load, 5000)'))
  assert.ok(page.includes('window.clearInterval(timer)'))
  assert.ok(page.includes('关键事件'))
  assert.ok(page.includes('全部技术事件'))
})

test('shell preserves roadshow chain across role switches and detail navigation', async () => {
  const shell = await source(shellPath)
  assert.ok(shell.includes('common.roadshow'))
  assert.ok(shell.includes("if (!roadshow.enabled) navigate('/overview')"))
  assert.ok(shell.includes('phase58-persistent'))
  assert.ok(shell.includes('返回主链'))
})

test('responsive layout contains chain overflow locally', async () => {
  const styles = await source(stylePath)
  for (const selector of [
    '.phase58-chain',
    '.phase58-layout',
    '.phase58-commandbar',
    '.phase58-side',
  ]) assert.ok(styles.includes(selector), selector)
  assert.ok(styles.includes('overflow-x: auto'))
  assert.ok(styles.includes('@media (max-width: 520px)'))
})

test('roadshow chain exposes the current node for layout and viewport positioning', async () => {
  const page = await source(pagePath)
  assert.ok(page.includes('phase58-chain phase58-chain--overview'))
  assert.ok(page.includes('data-current-node={currentNode?.key}'))
  assert.ok(page.includes('data-node-key={node.key}'))
  assert.ok(page.includes("aria-current={currentNode?.key === node.key ? 'step' : undefined}"))
  assert.ok(page.includes('ref={currentNode?.key === node.key ? currentNodeRef : undefined}'))
  assert.ok(page.includes("scrollIntoView({ block: 'nearest', inline: 'nearest' })"))
})

test('roadshow UI does not expose secrets or storage references', async () => {
  const page = await source(pagePath)
  for (const forbidden of [
    'X-Download-Token',
    'storage_reference',
    'object_key',
    'bucket_name',
    'secret_key',
    'access_key',
    'database_url',
    'traceback',
  ]) assert.ok(!page.includes(forbidden), forbidden)
  assert.ok(!page.includes('hard_isolation=false'))
  assert.ok(page.includes('Artifact'))
  assert.ok(page.includes('quarantined'))
})
