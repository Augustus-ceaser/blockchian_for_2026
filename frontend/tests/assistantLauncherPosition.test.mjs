import assert from 'node:assert/strict'
import test from 'node:test'

import {
  assistantLauncherBottomForPointer,
  clampAssistantLauncherBottom,
  isAssistantLauncherDrag,
} from '../src/roadshow/assistantLauncherPosition.ts'

test('assistant launcher drag moves vertically and stays inside the viewport', () => {
  assert.equal(clampAssistantLauncherBottom(220, 72, 720), 220)
  assert.equal(clampAssistantLauncherBottom(-30, 72, 720), 12)
  assert.equal(clampAssistantLauncherBottom(900, 72, 720), 636)
  assert.equal(clampAssistantLauncherBottom(30, 72, 80), 12)

  assert.equal(assistantLauncherBottomForPointer(28, 500, 400, 72, 720), 128)
  assert.equal(assistantLauncherBottomForPointer(28, 500, 620, 72, 720), 12)
  assert.equal(assistantLauncherBottomForPointer(620, 500, 350, 72, 720), 636)
})

test('assistant launcher distinguishes a click from a real drag', () => {
  assert.equal(isAssistantLauncherDrag(100, 104), false)
  assert.equal(isAssistantLauncherDrag(100, 105), true)
  assert.equal(isAssistantLauncherDrag(100, 94), true)
})
