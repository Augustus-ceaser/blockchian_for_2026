import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import {
  availableOfferings,
  modeAppliesToKind,
  offeringLabel,
} from '../src/roadshow/serviceAccess.ts'

const requestUiPath = new URL('../src/roadshow/ServiceAccessRequests.tsx', import.meta.url)
const dataPagePath = new URL('../src/roadshow/DataProductLifecyclePages.tsx', import.meta.url)
const modelPagePath = new URL('../src/roadshow/ModelProductLifecyclePages.tsx', import.meta.url)
const assistantPath = new URL('../src/roadshow/RoleAssistant.tsx', import.meta.url)

test('legacy internal catalog items keep controlled-compute compatibility', () => {
  const offerings = availableOfferings(undefined, 'data', true)
  assert.equal(offerings.length, 1)
  assert.equal(offerings[0].mode, 'controlled_compute')
  assert.equal(offerings[0].requestable, true)
})

test('an explicit empty offering list is not fabricated as downloadable access', () => {
  assert.deepEqual(availableOfferings([], 'model', true), [])
})

test('delivery and license modes stay scoped to the right product kind', () => {
  assert.equal(modeAppliesToKind('deidentified_data_delivery', 'data'), true)
  assert.equal(modeAppliesToKind('deidentified_data_delivery', 'model'), false)
  assert.equal(modeAppliesToKind('model_artifact_license', 'model'), true)
  assert.equal(modeAppliesToKind('model_artifact_license', 'data'), false)
})

test('the marketplace uses the user-facing authorization labels', () => {
  assert.equal(offeringLabel({
    mode: 'deidentified_data_delivery',
    label: '',
    requestable: true,
    fulfillment_status: 'requires_review',
    requires_contract: true,
  }), '匿名化数据授权交付')
})

test('marketplaces submit separate authorization requests without purchase or download claims', async () => {
  const [requestUi, dataPage, modelPage] = await Promise.all([
    readFile(requestUiPath, 'utf8'),
    readFile(dataPagePath, 'utf8'),
    readFile(modelPagePath, 'utf8'),
  ])
  assert.ok(requestUi.includes("'/service-access-requests'"))
  assert.ok(requestUi.includes('${stage}-decision'))
  assert.ok(requestUi.includes("stage === 'provider' ? 'provider_decide' : 'operator_decide'"))
  assert.ok(requestUi.includes('申请授权'))
  assert.ok(dataPage.includes("service_modes: ['controlled_compute', 'deidentified_data_delivery']"))
  assert.ok(modelPage.includes("['controlled_compute', 'model_artifact_license']"))
  for (const page of [requestUi, dataPage, modelPage]) {
    assert.ok(!page.includes('>立即购买<'))
    assert.ok(!page.includes('>立即下载<'))
  }
})

test('assistant routes service intents to the right existing workflow', async () => {
  const assistant = await readFile(assistantPath, 'utf8')
  assert.match(assistant, /\(脱敏数据\|数据授权/)
  assert.match(assistant, /return '\/data-catalog'/)
  assert.match(assistant, /\(模型许可\|模型授权/)
  assert.match(assistant, /return '\/model-catalog'/)
  assert.match(assistant, /return '\/applications\/new'/)
})
