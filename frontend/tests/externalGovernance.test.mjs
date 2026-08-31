import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const pagePath = new URL('../src/roadshow/ExternalGovernancePages.tsx', import.meta.url)
const routerPath = new URL('../src/router/index.tsx', import.meta.url)
const stylePath = new URL('../src/styles.css', import.meta.url)

test('governance routes separate four-role reads from operator writes', async () => {
  const [page, router] = await Promise.all([
    readFile(pagePath, 'utf8'),
    readFile(routerPath, 'utf8'),
  ])
  assert.ok(router.includes("path: '/external-catalog/governance'"))
  assert.ok(router.includes("path: '/portal/operator/external-catalog/governance'"))
  assert.ok(router.includes("<RoleGuard allowed={['space_operator']}><ExternalGovernancePage operator"))
  assert.ok(page.includes('{operator && <Form'))
  assert.ok(page.includes('{operator && <Button'))
})

test('governance dashboard exposes status, detail, timeline, and boundaries', async () => {
  const page = await readFile(pagePath, 'utf8')
  for (const text of [
    '公共数据治理工作台',
    '待许可证审核',
    '重复项待处理',
    '可进入草稿',
    '审核时间线',
    '尚无正式审核记录',
    '来源、许可与访问条件按状态分别展示',
  ]) assert.ok(page.includes(text), text)
  assert.equal(page.includes('目录收录不代表来源、许可或访问条件已经核验'), false)
})

test('governance review form captures structured source and license evidence', async () => {
  const page = await readFile(pagePath, 'utf8')
  for (const field of [
    'official_source_url',
    'license_name',
    'license_url',
    'research_use',
    'commercial_use',
    'redistribution',
    'derivatives',
    'rehosting',
  ]) assert.ok(page.includes(field), field)
})

test('governed candidates expose metadata-only draft conversion with readiness boundary', async () => {
  const page = await readFile(pagePath, 'utf8')
  for (const text of [
    '/data-product-draft',
    'data_product_draft_status',
    'materialization_status',
    'default_use_mode',
    'execution_readiness',
  ]) assert.ok(page.includes(text), text)
})

test('governance table overflow is contained locally on narrow screens', async () => {
  const styles = await readFile(stylePath, 'utf8')
  assert.match(styles, /\.external-governance-page \.ant-table-wrapper[\s\S]*overflow: hidden/)
  assert.match(styles, /@media \(max-width: 768px\)[\s\S]*external-governance-stats/)
})
