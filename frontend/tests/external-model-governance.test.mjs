import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const page = await readFile(new URL('../src/roadshow/ExternalModelGovernancePages.tsx', import.meta.url), 'utf8')
const router = await readFile(new URL('../src/router/index.tsx', import.meta.url), 'utf8')
const shell = await readFile(new URL('../src/roadshow/RoadshowShell.tsx', import.meta.url), 'utf8')

test('model governance has catalog-reader and operator routes', () => {
  assert.match(router, /\/external-catalog\/models\/governance/)
  assert.match(router, /\/portal\/operator\/external-model-catalog\/governance/)
  assert.match(router, /allowed=\{modelCatalogRoles\}/)
  assert.match(shell, /公共模型治理/)
  assert.match(shell, /模型治理工作台/)
})

test('governance UI preserves metadata and execution boundaries', () => {
  assert.match(page, /草稿资格、物化和执行状态分别展示/)
  assert.match(page, /published_metadata_only/)
  assert.match(page, /materialization_status/)
  assert.match(page, /execution_readiness/)
  assert.match(page, /platform_validation/)
  assert.match(page, /detail\.boundaries\.local_weights/)
  assert.match(page, /detail\.boundaries\.executable \? '可执行' : '不可执行'/)
  assert.doesNotMatch(page, /公开权重不等于本地已下载|eligible_for_model_draft 也不等于模型可执行/)
  assert.doesNotMatch(page, /<Alert/)
  assert.doesNotMatch(page, /创建 ModelProduct 草稿/)
})

test('operator controls use relative governance APIs and append review', () => {
  assert.match(page, /\/external-model-catalog\/governance\/summary/)
  assert.match(page, /\/external-model-catalog\/governance\/recalculate/)
  assert.match(page, /\/external-model-catalog\/models\/\$\{detail\.model\.id\}\/reviews/)
  assert.match(page, /追加 Review/)
  assert.doesNotMatch(page, /https?:\/\/[^'"]+\/api/)
})

test('narrow layouts contain the governance table and drawer', () => {
  assert.match(page, /scroll=\{\{ x: 1250 \}\}/)
  assert.match(page, /size=\{820\}/)
  assert.match(page, /maxWidth: '100vw'/)
})
