import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) => readFile(new URL(path, import.meta.url), 'utf8')

test('role overview is a compact business workbench backed by live overview and workflow fields', async () => {
  const page = await source('../src/roadshow/RoadshowPages.tsx')
  const overview = page.slice(page.indexOf('export function RoadshowOverviewPage'), page.indexOf('function WorkflowSummary'))

  for (const label of ['业务概览', '当前协作链下一步', '待办审核', '数据使用审批', '模型使用审批', '我的计算需求', '数字合约', '执行准备', '执行进度', '结果审核', '结果下载']) {
    assert.ok(page.includes(label), label)
  }
  for (const route of ["'/applications'", "'/contracts'", "'/execution'", "'/results'"]) {
    assert.ok(overview.includes(route), route)
  }
  assert.match(overview, /来自当前账号可见业务/)
  assert.doesNotMatch(overview, /<Statistic|本角色职责边界|可信协作主链/)
})

test('role overview aggregates real lifecycle list totals without background polling', async () => {
  const page = await source('../src/roadshow/RoadshowPages.tsx')
  const summary = page.slice(page.indexOf('function useRoleBusinessSummary'), page.indexOf('function PageBoundary'))
  const overview = page.slice(page.indexOf('export function RoadshowOverviewPage'), page.indexOf('function WorkflowSummary'))

  for (const endpoint of [
    "'/application-management'",
    "'/application-review-queue'",
    "'/data-product-review-queue'",
    "'/model-product-review-queue'",
    "'/digital-contracts'",
    "'/execution-readiness'",
    "'/result-artifacts'",
  ]) assert.ok(summary.includes(endpoint), endpoint)
  for (const field of ['applications.total', 'contracts.total', 'executions.total', 'results.total']) {
    assert.ok(summary.includes(field), field)
  }
  assert.match(summary, /dataProductReviews\.items\.length/)
  assert.match(summary, /modelProductReviews\.items\.length/)
  assert.match(overview, /business\.summary\?\.applications/)
  assert.match(overview, /business\.summary\?\.approvalQueues/)
  assert.match(overview, /business\.summary\?\.contracts/)
  assert.match(overview, /business\.summary\?\.executions/)
  assert.match(overview, /business\.summary\?\.results/)
  assert.doesNotMatch(summary, /setInterval/)
})

test('operator approval queues are visible in the primary workbench card without a duplicate collapsed entrance', async () => {
  const page = await source('../src/roadshow/RoadshowPages.tsx')
  const overview = page.slice(page.indexOf('export function RoadshowOverviewPage'), page.indexOf('function CatalogPage'))

  assert.match(overview, /className="role-workbench-grid"/)
  assert.match(overview, /className="content-card role-workbench-card"/)
  assert.match(overview, /className="role-workbench-next-action"/)
  assert.match(overview, /className="role-workbench-chain-details"/)
  for (const label of ['服务申请', '数据上架', '模型上架']) assert.match(overview, new RegExp(label))
  for (const route of ["'/applications'", "'/data-products'", "'/model-products'"]) assert.match(overview, new RegExp(route))
  assert.doesNotMatch(overview, /role-workbench-shortcuts|marketplace-operations|商城运营入口/)
  assert.match(overview, /label: '查看当前协作链详情'/)
  assert.match(overview, /<div className="role-workbench-next-action">\s*<ActionPanel overview=\{overview\} workflow=\{workflow\}/)
  assert.match(overview, /key: 'current-chain',[\s\S]*?children: <WorkflowSummary workflow=\{workflow\} \/>/)
  assert.equal((overview.match(/<ActionPanel overview=\{overview\} workflow=\{workflow\}/g) || []).length, 1)
  assert.ok(overview.indexOf('role-workbench-next-action') < overview.indexOf("key: 'current-chain'"))
  assert.doesNotMatch(overview, /PathMNIST|Phase 4|hard_isolation/)
})

test('role-facing hero copy stays task-oriented', async () => {
  const page = await source('../src/roadshow/RoadshowPages.tsx')
  for (const copy of [
    '集中处理平台审核、合约、执行与结果事项。',
    '查看数据使用申请、合约确认、执行准备与结果审核。',
    '查看模型使用申请、合约确认、执行准备与质量审核。',
    '从需求申请开始，跟进合约、执行与结果交付。',
  ]) assert.ok(page.includes(copy), copy)
})
