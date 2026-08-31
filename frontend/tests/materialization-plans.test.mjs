import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = await readFile(
  new URL('../src/roadshow/MaterializationPlanPages.tsx', import.meta.url),
  'utf8',
)

test('materialization page preserves real preparation state without generic boundary prose', () => {
  assert.match(source, /asset_downloaded: false/)
  assert.match(source, /data_materialized: false/)
  assert.match(source, /model_materialized: false/)
  assert.match(source, /execution_ready: false/)
  assert.doesNotMatch(source, /不会自动启动下载|不触发下载或计算/)
  assert.doesNotMatch(source, /platformCommand\([^\n]*download/)
  assert.doesNotMatch(source, /platformCommand\([^\n]*execute/)
})

test('operator decisions exist without materialization controls', () => {
  assert.match(source, /'approve'/)
  assert.match(source, /'reject'/)
  assert.doesNotMatch(source, /\/materialize/)
})
