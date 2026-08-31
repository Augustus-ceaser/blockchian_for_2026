import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) => readFile(new URL(path, import.meta.url), 'utf8')

test('the public portal exposes exactly four personas', async () => {
  const [types, context, login, portal, router, modelGovernance, roadshowPages] = await Promise.all([
    source('../src/roadshow/types.ts'),
    source('../src/roadshow/RoadshowContext.tsx'),
    source('../src/roadshow/RoadshowLoginPage.tsx'),
    source('../src/roadshow/PortalEntry.tsx'),
    source('../src/router/index.tsx'),
    source('../src/roadshow/ExternalModelGovernancePages.tsx'),
    source('../src/roadshow/RoadshowPages.tsx'),
  ])
  const identityBlock = types.match(/export const demoIdentities = \[([\s\S]*?)\] as const/)
  assert.ok(identityBlock, 'missing public identity registry')
  assert.deepEqual(
    [...identityBlock[1].matchAll(/'([^']+)'/g)].map((match) => match[1]),
    ['space_operator', 'data_provider', 'model_provider', 'data_requester'],
  )
  const publicSurface = [types, context, login, portal, router, modelGovernance, roadshowPages].join('\n')
  for (const removed of [
    'catalog_curator',
    'catalog.curator.demo',
    '/portal/catalog-curator',
    '公共数据目录整理方',
    '目录整理方',
    '提交模型目录产品审核',
  ]) assert.doesNotMatch(publicSurface, new RegExp(removed.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  assert.match(router, /allowed=\{\['space_operator'\]\}><MaterializationPlanPage/)
})

test('roadshow pages omit demo chrome while preserving live safeguards', async () => {
  const page = await source('../src/roadshow/RoadshowPages.tsx')
  for (const removed of [
    '前端切换不等于授权',
    '29 步后端权威演示',
    '无法读取路演状态',
    '路演全流程',
    '指定受控计算节点（演示）',
    '本机演示',
    '本地演示',
    'Phase 4',
    'hard_isolation=false',
    '当前工程原型',
    '工程能力边界',
    '本地内置执行器',
    'label="硬隔离"',
  ]) assert.ok(!page.includes(removed), removed)
  for (const required of [
    '无法读取平台状态',
    '操作未完成',
    '核对并确认数据使用条款',
    '进入执行准备',
    '需求企业在强制审核完成前看不到结果内容或下载按钮',
    '结果包永不包含',
  ]) assert.ok(page.includes(required), required)
})

test('authentication rejects backend-only roles before rendering the portal', async () => {
  const context = await source('../src/roadshow/RoadshowContext.tsx')
  assert.match(context, /authMe<\{ role: unknown \}>/)
  assert.match(context, /if \(!isDemoIdentity\(value\.role\)\)/)
  assert.match(context, /if \(!isDemoIdentity\(profile\.role\)\)/)
  assert.match(context, /authLogout\(\)\.catch\(\(\) => undefined\)/)
  assert.match(context, /setAuthenticated\(false\)/)
})

test('login copy is product-facing rather than an engineering demo notice', async () => {
  const login = await source('../src/roadshow/RoadshowLoginPage.tsx')
  for (const removed of [
    'Phase 4',
    '真实后端路演模式',
    '登录独立演示门户',
    '本地演示密码',
    'login-disclaimer',
    'AuditEvent',
    'Outbox',
  ]) assert.ok(!login.includes(removed), removed)
  assert.match(login, />选择身份登录</)
})
