import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/lib/secureUuid.ts', import.meta.url), 'utf8')

test('secure UUID helper prefers native randomUUID and has a getRandomValues fallback', () => {
  assert.match(source, /typeof source\?\.randomUUID === 'function'/)
  assert.match(source, /source\.getRandomValues\(bytes\)/)
  assert.doesNotMatch(source, /Math\.random/)
})

test('fallback sets UUID v4 version and RFC variant bits', () => {
  assert.match(source, /bytes\[6\].*0x40/)
  assert.match(source, /bytes\[8\].*0x80/)
})

test('missing secure randomness throws explicitly', () => {
  assert.match(source, /Secure random UUID generation is unavailable/)
})
