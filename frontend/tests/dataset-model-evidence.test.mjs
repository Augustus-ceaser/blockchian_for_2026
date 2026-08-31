import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(
  new URL('../src/roadshow/DatasetModelEvidencePages.tsx', import.meta.url),
  'utf8',
)
const router = readFileSync(new URL('../src/router/index.tsx', import.meta.url), 'utf8')
const shell = readFileSync(new URL('../src/roadshow/RoadshowShell.tsx', import.meta.url), 'utf8')
const dataDetail = readFileSync(
  new URL('../src/roadshow/DataProductLifecyclePages.tsx', import.meta.url),
  'utf8',
)
const modelDetail = readFileSync(
  new URL('../src/roadshow/ModelProductLifecyclePages.tsx', import.meta.url),
  'utf8',
)

test('operator evidence matrix is routed and uses relative APIs', () => {
  assert.match(router, /portal\/operator\/dataset-model-evidence/)
  assert.match(shell, /数据—模型证据/)
  assert.match(page, /\/dataset-model-relations\?matrix=true/)
  assert.doesNotMatch(page, /https?:\/\//)
})

test('evidence UI distinguishes static and verified historical evidence', () => {
  assert.match(page, /实际运行：\{runtime \? '有历史证据' : '尚无证据'\}/)
  assert.match(page, /平台验证：\{verified \? '已完成' : '尚无证据'\}/)
  assert.match(page, /有历史证据/)
  assert.match(page, /平台静态审查/)
  assert.match(page, /blocking_reasons/)
  assert.doesNotMatch(page, /静态证据不代表已运行|不代表完整 PathMNIST/)
  assert.doesNotMatch(page, /最佳模型|推荐模型|临床可用/)
  assert.doesNotMatch(page, /下载权重|运行模型|创建任务/)
})

test('data and model details expose bidirectional evidence summaries', () => {
  assert.match(dataDetail, /direction="data-to-models"/)
  assert.match(modelDetail, /direction="model-to-data"/)
})
