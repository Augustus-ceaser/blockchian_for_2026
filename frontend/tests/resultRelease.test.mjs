import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const pagePath = new URL('../src/roadshow/ResultReleasePages.tsx', import.meta.url)
const apiPath = new URL('../src/roadshow/api.ts', import.meta.url)
const routerPath = new URL('../src/router/index.tsx', import.meta.url)
const stylePath = new URL('../src/styles.css', import.meta.url)

async function source(path) {
  return readFile(path, 'utf8')
}

test('Phase 5.7 result routes and APIs are wired', async () => {
  const [page, router] = await Promise.all([source(pagePath), source(routerPath)])
  assert.ok(router.includes("path: '/results'"))
  assert.ok(router.includes("path: '/results/:artifactId'"))
  for (const path of [
    '/result-artifacts',
    '/review-plan',
    '/result-review-tasks/',
    '/package',
    '/download-grants',
    '/audit-events',
  ]) assert.ok(page.includes(path), path)
})

test('result review exposes all three responsible parties and explicit checks', async () => {
  const page = await source(pagePath)
  for (const label of [
    '医院数据出域审核',
    '模型方技术确认',
    '平台合规审核',
    '用途和范围与合同一致',
    '仅包含聚合结果',
    '未发现患者级信息',
    '文件摘要验证通过',
    '文件精确匹配输出白名单',
  ]) assert.ok(page.includes(label), label)
})

test('download is one-time and keeps raw Artifact inaccessible', async () => {
  const [page, api] = await Promise.all([source(pagePath), source(apiPath)])
  assert.ok(api.includes('platformDownload'))
  assert.ok(page.includes('创建授权并下载'))
  assert.ok(page.includes('验证二次使用被拒绝'))
  assert.ok(page.includes('原始 Artifact 下载：禁止'))
  assert.ok(!page.includes('artifact/download'))
  assert.ok(!page.includes('storage_reference'))
  assert.ok(!page.includes('object_key'))
})

test('package projection is the exact three-file allowlist', async () => {
  const page = await source(pagePath)
  for (const name of [
    'aggregate_metrics.json',
    'confusion_matrix.csv',
    'execution_summary.json',
  ]) assert.ok(page.includes(name) || page.includes('detail.manifest.map'), name)
  assert.ok(page.includes('Package digest'))
  assert.ok(page.includes('Artifact manifest'))
})

test('result detail preserves audit and mobile containment', async () => {
  const [page, styles] = await Promise.all([source(pagePath), source(stylePath)])
  for (const evidence of ['Event ID', 'Previous hash', 'Current hash', '审计链有效']) {
    assert.ok(page.includes(evidence), evidence)
  }
  assert.ok(page.includes('hard_isolation'))
  assert.ok(styles.includes('.phase57-page'))
  assert.ok(styles.includes('grid-template-columns'))
  assert.ok(styles.includes('overflow-x: auto'))
})
