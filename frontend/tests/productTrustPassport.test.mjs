import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const [dataProducts, modelProducts] = await Promise.all([
  readFile(new URL('../src/roadshow/DataProductLifecyclePages.tsx', import.meta.url), 'utf8'),
  readFile(new URL('../src/roadshow/ModelProductLifecyclePages.tsx', import.meta.url), 'utf8'),
])

test('data and model products use the same four-axis status language', () => {
  for (const source of [dataProducts, modelProducts]) {
    for (const label of ['生命周期', '资产就绪', '服务/执行', '可信证据']) {
      assert.match(source, new RegExp(label), label)
    }
    assert.match(source, /title="产品状态"/)
    assert.match(source, /title="可信护照"/)
  }
})

test('trust passports only project existing catalog and detail evidence fields', () => {
  for (const field of [
    'quality_summary',
    'allowed_purposes',
    'official_source_url',
    'capability.evidence_at',
    'detail.snapshot_digest',
    'detail.updated_at',
    'upstream_rights_holder',
  ]) assert.ok(dataProducts.includes(field), field)

  for (const field of [
    'detail.license.allowed_purposes',
    'platform_validation',
    'governance_snapshot_digest',
    'detail.snapshot_digest',
    'detail.updated_at',
  ]) assert.ok(modelProducts.includes(field), field)
})

test('external product detail states use current backend facts instead of fixed placeholders', () => {
  assert.match(dataProducts, /materializationStatus\(detail\.external_metadata\.materialization_status\)/)
  assert.match(dataProducts, /detail\.service_capability\.runtime_availability_label/)
  assert.match(dataProducts, /detail\.service_capability\.requestability_label/)
  assert.match(modelProducts, /materializationStatus\(detail\.external_source\.materialization_status\)/)
  assert.match(modelProducts, /executionReadinessLabel\(detail\.external_source\.execution_readiness\)/)
  assert.match(modelProducts, /modelValidationLabel\(detail\.external_source\.platform_validation\)/)
})

test('trust passports present registered purposes and external source names for people', () => {
  assert.match(dataProducts, /catalog_discovery: '目录检索'/)
  assert.match(dataProducts, /governance_revalidation: '治理复核'/)
  assert.match(dataProducts, /trustSourceLabel\(item\.upstream_rights_holder, item\.provider, item\.official_source_url\)/)
  assert.match(modelProducts, /model_validation: '模型验证'/)
})

test('requester catalog actions carry the exact selected version into the application handoff', () => {
  assert.match(dataProducts, /identity === 'data_requester' && offerings\.filter\(\(offering\) => offering\.requestable\)/)
  assert.match(dataProducts, /productSelection: \{ dataVersionId: item\.version_id \}/)
  assert.match(modelProducts, /identity === 'data_requester' && offerings\.filter\(\(offering\) => offering\.requestable\)/)
  assert.match(modelProducts, /productSelection: \{ modelVersionId: item\.version_id \}/)

  const dataCatalog = dataProducts.slice(dataProducts.indexOf('export function PublishedDataCatalogPage'))
  const modelCatalog = modelProducts.slice(modelProducts.indexOf('export function PublishedModelCatalogPage'))
  for (const catalog of [dataCatalog, modelCatalog]) {
    assert.doesNotMatch(catalog, /disabled[^>]*>发起/)
    assert.doesNotMatch(catalog, /Phase 5\.3/)
  }
})

test('requester detail actions preserve the catalog return path and selected version', () => {
  assert.match(dataProducts, /identity === 'data_requester' \? '\/data-catalog' : '\/data-products'/)
  assert.match(dataProducts, /productSelection: \{ dataVersionId: detail\.version_id \}/)
  assert.match(modelProducts, /identity === 'data_requester' \? '\/model-catalog' : '\/model-products'/)
  assert.match(modelProducts, /productSelection: \{ modelVersionId: detail\.version_id \}/)
})
