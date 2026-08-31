import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const page = await readFile(new URL('../src/roadshow/ConnectorControlPages.tsx', import.meta.url), 'utf8')
const router = await readFile(new URL('../src/router/index.tsx', import.meta.url), 'utf8')
const shell = await readFile(new URL('../src/roadshow/RoadshowShell.tsx', import.meta.url), 'utf8')
const legacyMockPage = await readFile(new URL('../src/pages/ConnectorsPage.tsx', import.meta.url), 'utf8')
const legacyApiPage = await readFile(new URL('../src/api/ApiPages.tsx', import.meta.url), 'utf8')

test('operator and hospital have separate connector routes', () => {
  assert.match(router, /portal\/operator\/connectors/)
  assert.match(router, /portal\/hospital\/connectors/)
  assert.match(router, /allowed=\{\['space_operator'\]\}/)
  assert.match(router, /allowed=\{\['data_provider'\]\}/)
})

test('control page keeps real execution and transfer controls without generic boundary chrome', () => {
  for (const label of ['general execution disabled', 'data transfer disabled']) {
    assert.ok(page.includes(label))
  }
  assert.ok(page.includes('Hospital Connector 控制与证据中心'))
  assert.ok(page.includes('Local Test CA'))
  assert.ok(page.includes('仅显示一次'))
  assert.ok(page.includes('Hospital Local Executors'))
  assert.ok(page.includes('PATHMNIST_REFERENCE_V1 readiness only'))
  assert.ok(page.includes('/connector-control/executors'))
  assert.doesNotMatch(page, /hard_isolation=false|第一层 · Connector|Central metadata mirror only/)
  assert.doesNotMatch(page, />Execute</)
})

test('connector and policy routes remain titled but stay out of the presentation menu', () => {
  const menus = shell.slice(shell.indexOf('const roleMenus'), shell.indexOf('const titleByPath'))
  assert.doesNotMatch(menus, /connectorControl|policyControl|hospitalConnectors|hospitalPolicyControl/)
  assert.ok(shell.includes("'/portal/operator/policy-control': 'Policy Control'"))
  assert.ok(shell.includes("'/portal/hospital/policy-control': '本组织 Policy Control'"))
  assert.ok(shell.includes("titleByPath[location.pathname]"))
})

test('compatibility connector pages keep current control links without presentation disclaimers', () => {
  assert.ok(legacyMockPage.includes('title="节点中心"'))
  assert.ok(legacyApiPage.includes('title="Connector 兼容记录"'))
  assert.ok(legacyApiPage.includes("'/portal/operator/connectors'"))
  assert.ok(legacyApiPage.includes("'/portal/hospital/connectors'"))
  assert.doesNotMatch(legacyMockPage, /历史架构演示|模拟页面|工程原型|当前原型入口/)
  assert.doesNotMatch(legacyApiPage, /历史架构演示|工程原型|hard_isolation=false|不用于临床|兼容既有演示流程/)
})
