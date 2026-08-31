import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const api = readFileSync(new URL('../src/roadshow/api.ts', import.meta.url), 'utf8')
const client = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const router = readFileSync(new URL('../src/router/index.tsx', import.meta.url), 'utf8')
const join = readFileSync(new URL('../src/roadshow/JoinPage.tsx', import.meta.url), 'utf8')

test('production API defaults are same-origin relative paths', () => {
  assert.match(api, /VITE_API_BASE_URL \|\| '\/api\/v1'/)
  assert.match(client, /VITE_API_BASE_URL \|\| '\/api\/v1'/)
})

test('join and four portal routes are registered', () => {
  for (const path of ['/join', '/portal/hospital', '/portal/model-provider', '/portal/requester', '/portal/operator']) {
    assert.ok(router.includes(`path: '${path}'`), path)
  }
})

test('QR payloads are URL-only and do not embed credentials', () => {
  assert.match(join, /QRCodeSVG value=\{url\}/)
  for (const forbidden of ['password', 'token', 'session', 'grant', 'minio']) {
    assert.ok(!join.toLowerCase().includes(`value={${forbidden}`))
  }
})
