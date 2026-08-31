import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  new URL('../src/roadshow/PolicyControlPages.tsx', import.meta.url),
  'utf8',
)
const router = readFileSync(
  new URL('../src/router/index.tsx', import.meta.url),
  'utf8',
)

test('policy control routes are role guarded for operator and hospital', () => {
  assert.match(router, /portal\/operator\/policy-control/)
  assert.match(router, /portal\/hospital\/policy-control/)
  assert.match(router, /allowed=\{\['space_operator'\]\}/)
  assert.match(router, /allowed=\{\['data_provider'\]\}/)
})

test('policy control UI supports fixed authorization without execution controls', () => {
  assert.match(source, /CONTROL_POLICY_VALIDATION/)
  assert.match(source, /FIXED_REFERENCE_EXECUTION/)
  assert.match(source, /consumed_count/)
  assert.match(source, /max_execution_count/)
  assert.match(source, /Execution started/)
  assert.match(source, /Local decision required; formal authorization remains unconsumed/)
  assert.doesNotMatch(source, /maximum one authorization \/ not executed|hard_isolation=false|Authorization control only/)
  assert.doesNotMatch(source, />Execute</)
  assert.doesNotMatch(source, />Run</)
  assert.doesNotMatch(source, />Start</)
  assert.doesNotMatch(source, />Upload</)
  assert.doesNotMatch(source, />Download</)
  assert.doesNotMatch(source, />Publish</)
  assert.doesNotMatch(source, />Evidence</)
})

test('operator writes use dedicated policy-control endpoints', () => {
  assert.match(source, /policy-control\/policies\/compile/)
  assert.match(source, /sign-activate/)
  assert.match(source, /policy-control\/orders/)
  assert.match(source, /\/revoke/)
})
