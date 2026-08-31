import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const dataCatalog = await readFile(
  new URL('../src/roadshow/DataProductLifecyclePages.tsx', import.meta.url),
  'utf8',
)
const assistantContract = await readFile(
  new URL('../src/roadshow/roleAssistantContract.ts', import.meta.url),
  'utf8',
)

test('published data catalog folds service availability into the four-axis product state', () => {
  for (const token of [
    'service_capability',
    '生命周期',
    '资产就绪',
    '服务/执行',
    '可信证据',
    'runtime_availability_label',
    'evidence_at',
    'runtime_availability',
    'requestability',
    'detail.service_capability',
  ]) assert.match(dataCatalog, new RegExp(token))
})

test('service presentation never renders connector secrets or internal endpoints', () => {
  const catalogSection = dataCatalog.slice(dataCatalog.indexOf('export function PublishedDataCatalogPage'))
  for (const forbidden of [
    'endpoint_metadata',
    'certificate_fingerprint',
    'credential_ref',
    'local_resource_alias',
  ]) assert.doesNotMatch(catalogSection, new RegExp(forbidden))
})

test('assistant contract has a first-class read-only service result kind', () => {
  assert.match(assistantContract, /'service'/)
})
