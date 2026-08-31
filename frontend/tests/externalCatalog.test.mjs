import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const pagePath = new URL('../src/roadshow/ExternalCatalogPages.tsx', import.meta.url)
const routerPath = new URL('../src/router/index.tsx', import.meta.url)
const shellPath = new URL('../src/roadshow/RoadshowShell.tsx', import.meta.url)

test('external candidate catalog is routed for authenticated users and platform-managed sync', async () => {
  const [router, shell] = await Promise.all([
    readFile(routerPath, 'utf8'),
    readFile(shellPath, 'utf8'),
  ])
  assert.ok(router.includes("path: '/external-catalog/datasets'"))
  assert.ok(router.includes("path: '/portal/operator/external-catalog'"))
  assert.ok(router.includes("<RoleGuard allowed={['space_operator']}><ExternalCatalogPage operator"))
  assert.ok(!router.includes('catalog_curator'))
  assert.ok(shell.includes("externalCatalogSync: { key: '/portal/operator/external-catalog'"))
  assert.ok(shell.includes("data: { key: '/data-catalog'"))
})

test('catalog UI keeps metadata and DataProduct boundaries visible without a large warning banner', async () => {
  const page = await readFile(pagePath, 'utf8')
  for (const text of [
    '目录元数据',
    '<Tag>否</Tag>',
    '已下载',
    '正式 DataProduct',
    '可执行',
    '/external-catalog/datasets',
    '/sync',
  ]) assert.ok(page.includes(text), text)
  assert.equal(page.includes('候选目录不代表数据已下载、持有或获得再分发许可'), false)
  assert.equal(page.includes(' / HTTP '), false)
})

test('catalog UI provides paging plus independent name and disease-or-organ search', async () => {
  const page = await readFile(pagePath, 'utf8')
  for (const text of [
    'pageSize = 50',
    "parameters.set('q'",
    "parameters.set('disease_or_organ'",
    "parameters.set('modality'",
    "parameters.set('license_status'",
    "parameters.set('quality_flag'",
    "const [diseaseOrOrgan, setDiseaseOrOrgan] = useState('')",
    '输入疾病或器官',
    'setPage(1); setDiseaseOrOrgan',
  ]) assert.ok(page.includes(text), text)
})

test('catalog text search is debounced, ignores stale responses and keeps later refreshes quiet', async () => {
  const page = await readFile(pagePath, 'utf8')
  for (const text of [
    'const SEARCH_DEBOUNCE_MS = 275',
    'useDebouncedText(query)',
    'useDebouncedText(diseaseOrOrgan)',
    'requestId !== latestRequestId.current',
    'loading={initialLoading}',
    '目录加载失败：{loadError}',
  ]) assert.ok(page.includes(text), text)
  assert.match(page, /placeholder="输入疾病或器官[^\"]*" maxLength=\{120\}/)
  assert.equal(page.includes('setLoading(true)'), false)
})
