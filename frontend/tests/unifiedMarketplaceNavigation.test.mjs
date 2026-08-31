import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) => readFile(new URL(path, import.meta.url), 'utf8')

function roleItems(shell, role) {
  const match = shell.match(new RegExp(`  ${role}: \\[([\\s\\S]*?)\\n  \\],`))
  assert.ok(match, `missing ${role} menu`)
  return match[1]
}

test('primary navigation exposes one data marketplace and one model marketplace', async () => {
  const shell = await source('../src/roadshow/RoadshowShell.tsx')
  assert.match(shell, /data: \{ key: '\/data-catalog',[\s\S]*label: '数据商城' \}/)
  assert.match(shell, /models: \{ key: '\/model-catalog',[\s\S]*label: '模型商城' \}/)

  for (const role of ['space_operator', 'data_provider', 'model_provider', 'data_requester']) {
    const items = roleItems(shell, role)
    assert.doesNotMatch(items, /公共数据目录|公共模型目录|数据产品审核|模型产品审核/)
  }

  assert.match(roleItems(shell, 'space_operator'), /common\.data[\s\S]*common\.models/)
  assert.match(roleItems(shell, 'data_requester'), /common\.data[\s\S]*common\.models/)
  assert.doesNotMatch(roleItems(shell, 'data_requester'), /externalCatalog|externalModels/)
})

test('legacy catalog, sync and review deep links remain routed', async () => {
  const router = await source('../src/router/index.tsx')
  for (const path of [
    '/external-catalog/datasets',
    '/external-catalog/models',
    '/portal/operator/external-catalog',
    '/portal/operator/external-model-catalog',
    '/data-products',
    '/model-products',
  ]) assert.ok(router.includes(`path: '${path}'`), path)
})

test('operator workbench exposes categorized review queues in the primary approval card', async () => {
  const [shell, overview] = await Promise.all([
    source('../src/roadshow/RoadshowShell.tsx'),
    source('../src/roadshow/RoadshowPages.tsx'),
  ])
  const operatorMenu = roleItems(shell, 'space_operator')
  assert.doesNotMatch(operatorMenu, /common\.dataProducts|common\.modelProducts/)
  assert.match(overview, /label: '服务申请'[\s\S]*path: '\/applications'/)
  assert.match(overview, /label: '数据上架'[\s\S]*path: '\/data-products'/)
  assert.match(overview, /label: '模型上架'[\s\S]*path: '\/model-products'/)
  assert.doesNotMatch(overview, /商城运营入口|role-workbench-shortcuts/)
})

test('legacy catalog pages select their unified marketplace parent', async () => {
  const shell = await source('../src/roadshow/RoadshowShell.tsx')
  assert.match(shell, /startsWith\('\/external-catalog\/datasets'\)[\s\S]*'\/data-catalog'/)
  assert.match(shell, /startsWith\('\/external-catalog\/models'\)[\s\S]*'\/model-catalog'/)
  assert.match(shell, /'data-catalog': '数据商城'/)
  assert.match(shell, /'model-catalog': '模型商城'/)
})
