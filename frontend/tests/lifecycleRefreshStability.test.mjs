import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const dataPagePath = new URL('../src/roadshow/DataProductLifecyclePages.tsx', import.meta.url)
const modelPagePath = new URL('../src/roadshow/ModelProductLifecyclePages.tsx', import.meta.url)

async function lifecycleSources() {
  return Promise.all([readFile(dataPagePath, 'utf8'), readFile(modelPagePath, 'utf8')])
}

function hookSource(page) {
  return page.slice(page.indexOf('function useLoad<T>'), page.indexOf('export function', page.indexOf('function useLoad<T>')))
}

test('lifecycle loaders only block initial and identity-change loads', async () => {
  for (const page of await lifecycleSources()) {
    const hook = hookSource(page)
    for (const text of [
      'const [initialized, setInitialized] = useState(false)',
      'const identityRef = useRef(identity)',
      'const identityChanged = identityRef.current !== identity',
      'const needLoading = !initialized || identityChanged',
      'if (identityChanged) setData(null)',
      'if (needLoading) setLoading(true)',
      'setInitialized(true)',
      'identityRef.current = identity',
    ]) assert.ok(hook.includes(text), text)

    assert.doesNotMatch(hook, /setLoading\(true\);\s*setError\(''\)/)
  }
})

test('later lifecycle refresh failures retain rendered content and show an inline error', async () => {
  for (const page of await lifecycleSources()) {
    assert.ok(page.includes('if (error && !hasContent)'))
    assert.ok(page.includes('刷新失败，已保留当前内容'))
    assert.equal((page.match(/hasContent=\{state\.data !== null\}/g) || []).length, 4)
  }
})

test('data and model marketplaces distinguish filtered emptiness and can clear both filters', async () => {
  const [dataPage, modelPage] = await lifecycleSources()
  for (const [page, productLabel] of [[dataPage, '数据产品'], [modelPage, '模型产品']]) {
    assert.ok(page.includes("const filtersActive = modeFilter !== 'all' || sourceFilter !== 'all'"))
    assert.ok(page.includes(`filtersActive ? '当前没有匹配筛选条件的${productLabel}'`))
    assert.ok(page.includes("setModeFilter('all'); setSourceFilter('all')"))
    assert.ok(page.includes('>清除筛选</Button>'))
  }
  assert.doesNotMatch(modelPage, /description=\{modeFilter === 'all' \? '当前没有已发布模型'/)
})
