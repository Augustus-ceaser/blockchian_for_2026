import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const pagePath = new URL('../src/roadshow/ExecutionReadinessPages.tsx', import.meta.url)
const routerPath = new URL('../src/router/index.tsx', import.meta.url)
const stylePath = new URL('../src/styles.css', import.meta.url)

async function source(path) {
  return readFile(path, 'utf8')
}

test('execution readiness routes use the real Phase 5.5 API', async () => {
  const [page, router] = await Promise.all([source(pagePath), source(routerPath)])
  assert.ok(router.includes("path: '/execution'"))
  assert.ok(router.includes("path: '/execution/:contractId'"))
  for (const path of [
    '/execution-readiness',
    "path: 'data-readiness'",
    "path: 'model-readiness'",
    '/eligibility-check',
    '/jobs',
    '/audit-events',
  ]) assert.ok(page.includes(path))
})

test('role-specific writes are single-flight and idempotent', async () => {
  const page = await source(pagePath)
  assert.ok(page.includes("import { createSingleFlight, startAbortableLoad } from './requestLifecycle'"))
  assert.ok(page.includes('useRef(createSingleFlight()).current'))
  assert.ok(page.includes('await guard.run'))
  assert.ok(page.includes('secureUuid()'))
  assert.ok(page.includes("identity === 'data_provider'"))
  assert.ok(page.includes("identity === 'model_provider'"))
  assert.ok(page.includes("identity === 'space_operator'"))
  assert.ok(page.includes("identity === 'data_requester'"))
})

test('job page exposes controlled dispatch and stops at quarantined Artifact', async () => {
  const page = await source(pagePath)
  assert.ok(page.includes('发起受控执行'))
  assert.ok(page.includes('/dispatch'))
  assert.ok(page.includes('真实执行时间线'))
  assert.ok(page.includes('Artifact 状态'))
  assert.ok(page.includes('安全结果包未生成'))
  assert.ok(page.includes('下载权限为无'))
  assert.ok(page.includes('逻辑只读 + 完整性校验'))
  assert.ok(page.includes('合同网络策略：禁止外网'))
  assert.ok(!page.includes('hard_isolation=false'))
  assert.ok(!page.includes('artifact/download'))
})

test('eligibility matrix and readiness grids stay locally responsive', async () => {
  const [page, styles] = await Promise.all([source(pagePath), source(stylePath)])
  assert.ok(page.includes('scroll={{ x: 900 }}'))
  assert.ok(page.includes('className="phase55-readiness-grid"'))
  assert.ok(page.includes('className="phase55-job-grid"'))
  assert.ok(styles.includes('.phase55-check-table'))
  assert.ok(styles.includes('overflow-x: auto'))
  assert.ok(styles.includes('grid-template-columns: minmax(0, 1fr)'))
  assert.ok(styles.includes('.phase55-detail-page .ant-descriptions-view > table'))
  assert.ok(styles.includes('table-layout: fixed !important'))
  assert.ok(styles.includes('min-width: 0'))
  assert.ok(styles.includes('.phase55-detail-page .ant-descriptions-item-content .ant-tag'))
  assert.ok(styles.includes('overflow-wrap: anywhere'))
  assert.ok(styles.includes('word-break: break-word'))
  assert.ok(!styles.includes('body { overflow-x: hidden'))
  assert.ok(!styles.includes('html { overflow-x: hidden'))
})

test('provider confirmation note has a programmatically associated label and description', async () => {
  const page = await source(pagePath)
  assert.ok(page.includes('htmlFor="readiness-confirmation-note"'))
  assert.ok(page.includes('id="readiness-confirmation-note"'))
  assert.ok(page.includes('id="readiness-confirmation-note-label"'))
  assert.ok(page.includes('aria-labelledby="readiness-confirmation-note-label"'))
  assert.ok(page.includes('id="readiness-confirmation-note-description"'))
  assert.ok(page.includes('aria-describedby="readiness-confirmation-note-description"'))
})

test('technical evidence avoids infrastructure secrets and download controls', async () => {
  const page = await source(pagePath)
  for (const forbidden of [
    'connector_credentials',
    'database_url',
    'access_token',
    'secret_key',
    'object_key',
    'storage_reference',
    'traceback',
  ]) assert.ok(!page.includes(forbidden), forbidden)
  for (const evidence of ['Event ID', 'Previous hash', 'Current hash', 'Outbox']) {
    assert.ok(page.includes(evidence))
  }
})
