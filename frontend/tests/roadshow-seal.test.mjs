import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(
  new URL('../src/roadshow/RoadshowSealPage.tsx', import.meta.url),
  'utf8',
)
const router = readFileSync(new URL('../src/router/index.tsx', import.meta.url), 'utf8')

test('unified overview reads live counts without mutating business state', () => {
  assert.match(router, /path: '\/roadshow', element: <RoadshowSealPage/)
  assert.match(router, /path: '\/roadshow\/workflow', element: <RoadshowExperiencePage/)
  assert.match(page, /platformGet<SealState>\('\/roadshow-seal\/overview'/)
  assert.doesNotMatch(page, /platformCommand|roadshowCommand|fetch\(/)
})

test('overview presents one task-led hero, three resources, and a four-step flow', () => {
  assert.match(page, /一句话提出任务/)
  assert.match(page, /打开智能助手/)
  assert.equal((page.match(/<ResourceCard/g) || []).length, 3)
  for (const label of ['数据资源', '模型资源', '协作需求', '发现资源', '提出需求', '多方审批', '结果交付']) {
    assert.ok(page.includes(label), label)
  }
  assert.doesNotMatch(page, /ENGINEERING ROADSHOW|hard_isolation|\u975e临床诊断|\u5de5程原型|\u53ea读展示/)
})
