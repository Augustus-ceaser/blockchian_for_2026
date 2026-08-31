import assert from 'node:assert/strict'
import test from 'node:test'
import { buildPathmnistModelDraft, validateModelBoundary } from '../src/roadshow/modelProductForm.ts'

const asset = {
  asset_id: 'sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0',
  name: 'PathMNIST',
  version: '1',
  model_digest: 'sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0',
  registry_digest: 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  entrypoint_id: 'pathmnist_resnet18_v1',
  runtime: 'python-pytorch-cpu',
  input_schema_version: 'pathmnist-rgb-28x28/v1',
  output_schema_version: 'pathmnist-aggregate-inference/v1',
  network_access: false,
  cpu_limit: 1,
  memory_limit_mb: 2048,
  timeout_seconds: 120,
  executor_type: 'local_builtin',
  runtime_status: 'ready',
  model_ready: true,
  allowed_output_files: [],
}

test('PathMNIST sample fills a safe fixed-registry model draft', () => {
  const draft = buildPathmnistModelDraft(asset)
  assert.equal(draft.runtime.entrypoint_id, 'pathmnist_resnet18_v1')
  assert.equal(draft.basic.clinical_use, false)
  assert.deepEqual(draft.policy.service_modes, ['controlled_compute', 'model_artifact_license'])
  assert.deepEqual(validateModelBoundary(draft), [])
})

test('a model product must expose at least one explicit service mode', () => {
  const draft = buildPathmnistModelDraft(asset)
  draft.policy.service_modes = []
  assert.ok(validateModelBoundary(draft).some((item) => item.includes('授权方式')))
})

test('unsafe runtime, download and weight output are rejected', () => {
  const draft = buildPathmnistModelDraft(asset)
  draft.runtime.network_access = true
  draft.schema.allowed_outputs.push('model_weights')
  draft.policy.model_download = true
  const errors = validateModelBoundary(draft)
  assert.ok(errors.some((item) => item.includes('运行边界')))
  assert.ok(errors.some((item) => item.includes('禁止内容')))
  assert.ok(errors.some((item) => item.includes('下载')))
})

test('incomplete model wizard values produce messages instead of throwing', () => {
  const errors = validateModelBoundary({ basic: { is_demo: true, clinical_use: false } })
  assert.ok(errors.some((item) => item.includes('digest')))
  assert.ok(errors.some((item) => item.includes('entrypoint')))
})
