import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const contextPath = new URL('../src/roadshow/RoadshowContext.tsx', import.meta.url)
const loginPath = new URL('../src/roadshow/RoadshowLoginPage.tsx', import.meta.url)
const apiPath = new URL('../src/roadshow/api.ts', import.meta.url)
const routerPath = new URL('../src/router/index.tsx', import.meta.url)
const shellPath = new URL('../src/roadshow/RoadshowShell.tsx', import.meta.url)
const lifecyclePath = new URL('../src/roadshow/ProductLifecycleGovernance.tsx', import.meta.url)

async function source(path) {
  return readFile(path, 'utf8')
}

test('Phase 5.9 uses cookie authentication and no client identity authority', async () => {
  const [context, api] = await Promise.all([source(contextPath), source(apiPath)])
  assert.ok(context.includes("authMe<{ role: unknown }>"))
  assert.ok(context.includes('isDemoIdentity(value.role)'))
  assert.ok(context.includes('isDemoIdentity(profile.role)'))
  assert.ok(context.includes('authLogin(username, password)'))
  assert.ok(api.includes("credentials: 'include'"))
  assert.ok(!api.includes("'X-Demo-Identity'"))
  assert.ok(!api.includes('"X-Demo-Identity"'))
  assert.ok(!context.includes('window.localStorage'))
})

test('login is username/password based and the debug role switch is hidden by default', async () => {
  const [login, shell, context] = await Promise.all([
    source(loginPath),
    source(shellPath),
    source(contextPath),
  ])
  assert.ok(login.includes('username'))
  assert.ok(login.includes('password'))
  assert.ok(shell.includes("VITE_ENABLE_DEMO_ROLE_SWITCH === 'true'"))
  assert.ok(context.includes("VITE_ENABLE_DEMO_ROLE_SWITCH === 'true'"))
  assert.ok(!shell.includes("VITE_ENABLE_DEMO_ROLE_SWITCH !== 'false'"))
})

test('local demo login pre-fills the configured password and Enter uses form submit', async () => {
  const login = await source(loginPath)
  assert.ok(login.includes('VITE_MEDTRUST_DEMO_PASSWORD'))
  assert.ok(login.includes('password: localDemoPassword'))
  assert.ok(login.includes('form.setFieldsValue({ username: usernames[selected], password: localDemoPassword })'))
  assert.ok(login.includes('}, [form, selected])'))
  assert.ok(login.includes('passwordInputRef.current?.focus()'))
  assert.ok(login.includes('onPressEnter={() => form.submit()}'))
  assert.ok(login.includes('htmlType="submit"'))
  assert.ok(!login.includes('min: 8'))
})

test('direct API-mode navigation has explicit role guards and a 403 page', async () => {
  const router = await source(routerPath)
  assert.ok(router.includes('function RoleGuard'))
  assert.ok(router.includes('status="403"'))
  assert.ok(router.includes("allowed={['data_provider']}"))
  assert.ok(router.includes("allowed={['model_provider']}"))
  assert.ok(router.includes("allowed={['data_requester']}"))
  assert.ok(router.includes("allowed={['space_operator']}"))
  assert.ok(router.includes('if (!isApi) return <ModeShell />'))
})

test('lifecycle UI exposes request, cancellation, impact and operator decision controls', async () => {
  const lifecycle = await source(lifecyclePath)
  for (const required of [
    'unpublish',
    'relist',
    'archive',
    '/cancel',
    '/decision',
    'impact',
    'blockers',
  ]) assert.ok(lifecycle.includes(required), required)
})
