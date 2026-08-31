import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildPublicDemoDraft,
  validateDraftBoundary,
} from '../src/roadshow/dataProductForm.ts'

test('public demo sample fills a complete safe form without writing data', () => {
  const draft = buildPublicDemoDraft('connector-demo')
  assert.equal(draft.basic.source_type, 'public_demo_dataset')
  assert.equal(draft.basic.is_demo, true)
  assert.equal(draft.binding.connector_id, 'connector-demo')
  assert.deepEqual(draft.policy.service_modes, ['controlled_compute', 'deidentified_data_delivery'])
  assert.deepEqual(validateDraftBoundary(draft), [])
})

test('a data product must expose at least one explicit service mode', () => {
  const draft = buildPublicDemoDraft('connector-demo')
  draft.policy.service_modes = []
  assert.ok(validateDraftBoundary(draft).some((item) => item.includes('授权方式')))
})

test('unsafe outputs and local-style resource identifiers are rejected before API submission', () => {
  const draft = buildPublicDemoDraft('connector-demo')
  draft.policy.allowed_outputs.push('raw_images')
  draft.binding.resource_identifier = 'D:\\patient-data\\images'
  const errors = validateDraftBoundary(draft)
  assert.ok(errors.some((item) => item.includes('敏感类型')))
  assert.ok(errors.some((item) => item.includes('资源标识')))
})

test('current engineering boundary cannot be presented as hard isolation', () => {
  const draft = buildPublicDemoDraft('connector-demo')
  draft.policy.hard_isolation = true
  assert.ok(validateDraftBoundary(draft).some((item) => item.includes('hard_isolation=false')))
})

test('incomplete wizard values return validation messages instead of throwing', () => {
  const errors = validateDraftBoundary({
    basic: { is_demo: true },
  })
  assert.ok(errors.some((item) => item.includes('资源标识')))
  assert.ok(errors.some((item) => item.includes('Connector')))
  assert.ok(errors.some((item) => item.includes('数据已就绪')))
})
