import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const pagePath = new URL('../src/roadshow/ContractLifecyclePages.tsx', import.meta.url)
const applicationPath = new URL('../src/roadshow/ApplicationLifecyclePages.tsx', import.meta.url)
const roadshowPath = new URL('../src/roadshow/RoadshowPages.tsx', import.meta.url)
const routerPath = new URL('../src/router/index.tsx', import.meta.url)
const stylePath = new URL('../src/styles.css', import.meta.url)
const typesPath = new URL('../src/roadshow/types.ts', import.meta.url)

async function source(path) {
  return readFile(path, 'utf8')
}

test('digital contract routes and approved-application entry are present', async () => {
  const [page, application, router] = await Promise.all([
    source(pagePath),
    source(applicationPath),
    source(routerPath),
  ])
  assert.ok(router.includes("path: '/contracts'"))
  assert.ok(router.includes("path: '/contracts/:contractId'"))
  assert.ok(page.includes("'/digital-contracts'"))
  assert.ok(page.includes('`/digital-contracts/${contractId}`'))
  assert.ok(application.includes('生成数字合约草稿'))
  assert.ok(application.includes('进入数字合约'))
  assert.ok(application.includes('等待平台生成合约'))
})

test('confirmation binds the current revision and digest as an internal structured confirmation', async () => {
  const page = await source(pagePath)
  assert.ok(page.includes('contract_revision_id: detail.revision_id'))
  assert.ok(page.includes('content_digest: detail.content_digest'))
  assert.ok(page.includes('declaration_accepted: accepted'))
  assert.ok(page.includes('平台内部结构化确认'))
  assert.ok(!page.includes('不等同于CA数字证书、可靠电子签名或线下法律意见'))
})

test('contract detail presents the authoritative security validation without a second portal', async () => {
  const [page, types, styles] = await Promise.all([
    source(pagePath),
    source(typesPath),
    source(stylePath),
  ])

  assert.ok(types.includes('export type ContractSecurityValidation'))
  assert.ok(page.includes('security_validation: ContractSecurityValidation | null'))
  assert.ok(page.includes('安全合约验证'))
  for (const field of [
    'purpose_code',
    'run_count',
    'effective_until',
    'allowed_outputs',
    'network_allowed',
    'output_review_required',
    'prohibited_actions',
    'identity_assurance',
  ]) assert.ok(page.includes(field), field)
  for (const label of [
    '主体权限',
    '条款完整性',
    '资产版本',
    '策略完整性',
    '合约内容',
    '有效期限',
    '四方确认',
    '执行绑定',
    '平台账号、组织准入与角色校验',
  ]) assert.ok(page.includes(label), label)
  assert.ok(page.includes("securityValidation?.overall !== 'BLOCKER'"))
  assert.ok(page.includes("securityValidation?.overall === 'PASS'"))
  assert.ok(page.includes('disabled={!accepted || !confirmationSecurityReady}'))
  assert.ok(page.includes('disabled={!activationSecurityReady}'))
  assert.ok(styles.includes('.phase54-security-check-grid'))
})

test('legacy overview routes security-sensitive actions into the governed pages', async () => {
  const roadshow = await source(roadshowPath)
  for (const legacyWrite of [
    "endpoint: '/contracts/sign'",
    "endpoint: '/contracts/activate'",
    "endpoint: '/readiness/data'",
    "endpoint: '/readiness/model'",
    "endpoint: '/readiness/platform'",
    "endpoint: '/compute-runs'",
  ]) assert.ok(!roadshow.includes(legacyWrite), legacyWrite)
  assert.ok(roadshow.includes('navigateTo: `/contracts/${workflow.contract.id}`'))
  assert.ok(roadshow.includes('navigateTo: `/execution/${workflow.contract.id}`'))
})

test('policy convergence is progressively disclosed and active contracts link to readiness', async () => {
  const page = await source(pagePath)
  assert.ok(page.includes('<details className="phase54-technical-evidence">'))
  assert.ok(page.includes('<summary>查看技术证据</summary>'))
  assert.ok(page.includes('navigate(`/execution/${detail.contract_id}`)'))
  assert.ok(page.includes('进入执行准备'))
})

test('active contract requires settlement before readiness without exposing execution controls', async () => {
  const page = await source(pagePath)
  assert.ok(page.includes('当前版本已经冻结，完成结算后进入受控执行准备。'))
  assert.ok(page.includes('等待需求方完成结算'))
  assert.ok(page.includes('查看结算与履约'))
  assert.ok(page.includes("detail?.status === 'signed'"))
  assert.ok(page.includes('激活数字合约'))
  assert.ok(page.includes('进入执行准备'))
  for (const forbidden of ['ComputeRun', 'Artifact', 'model upload', 'raw data download']) {
    assert.ok(!page.includes(forbidden))
  }
})

test('policy matrix scrolls locally and mobile party layout remains single-column', async () => {
  const [page, styles] = await Promise.all([source(pagePath), source(stylePath)])
  assert.ok(page.includes('className="phase54-policy-scroll"'))
  assert.ok(page.includes('scroll={{ x: 860 }}'))
  assert.ok(styles.includes('.phase54-policy-scroll'))
  assert.ok(styles.includes('overflow-x: auto'))
  assert.ok(styles.includes('grid-template-columns: minmax(0, 1fr)'))
})
