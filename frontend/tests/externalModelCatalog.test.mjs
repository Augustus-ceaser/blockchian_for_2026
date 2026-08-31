import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const page = await readFile(new URL('../src/roadshow/ExternalModelCatalogPages.tsx', import.meta.url), 'utf8')
const router = await readFile(new URL('../src/router/index.tsx', import.meta.url), 'utf8')
const shell = await readFile(new URL('../src/roadshow/RoadshowShell.tsx', import.meta.url), 'utf8')

test('external model catalog uses four public roles and operator-only sync routes', () => {
  assert.ok(router.includes("path: '/external-catalog/models'"))
  assert.ok(router.includes("path: '/portal/operator/external-model-catalog'"))
  assert.ok(router.includes("['space_operator', 'data_provider', 'model_provider', 'data_requester']"))
  assert.ok(!router.includes('catalog_curator'))
  assert.ok(shell.includes("externalModelSync: { key: '/portal/operator/external-model-catalog'"))
})

test('model candidate UI preserves compact metadata-only and non-executable status', () => {
  for (const text of [
    '目录元数据',
    '未物化',
    '不可执行',
    '权重状态',
    '未下载',
    'External declaration',
  ]) assert.ok(page.includes(text), text)
  assert.equal(page.includes('模型目录收录不代表权重已经下载、部署或经过 MedTrust 运行验证'), false)
  assert.equal(page.includes(' / HTTP '), false)
  assert.equal(page.includes('下载权重'), false)
})

test('model catalog filters and external links are explicit', () => {
  for (const text of [
    "parameters.set('q'",
    "parameters.set('category'",
    "parameters.set('weights_status'",
    'target="_blank"',
    'rel="noreferrer noopener"',
    '（外部链接）',
  ]) assert.ok(page.includes(text), text)
})
