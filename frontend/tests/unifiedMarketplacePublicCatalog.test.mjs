import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const dataPagePath = new URL('../src/roadshow/DataProductLifecyclePages.tsx', import.meta.url)
const modelPagePath = new URL('../src/roadshow/ModelProductLifecyclePages.tsx', import.meta.url)

test('unified marketplaces page through the real public catalogs and remove published duplicates', async () => {
  const [dataPage, modelPage] = await Promise.all([
    readFile(dataPagePath, 'utf8'),
    readFile(modelPagePath, 'utf8'),
  ])

  assert.match(dataPage, /`\/external-catalog\/datasets\?\$\{parameters\}`/)
  assert.match(dataPage, /offset: String\(\(publicPage - 1\) \* PUBLIC_DATA_PAGE_SIZE\)/)
  assert.match(dataPage, /total=\{publicState\.data\.total\}/)
  assert.match(dataPage, /filter\(\(item\) => !item\.published_product_version_id\)/)
  assert.match(dataPage, /key=\{item\.id\}/)
  assert.match(dataPage, /<Drawer[\s\S]*公共数据目录详情/)
  assert.match(dataPage, /正式产品 \{state\.data\?\.items\.length[\s\S]*公共目录 \{publicState\.data\?\.total/)

  assert.match(modelPage, /`\/external-model-catalog\/models\?\$\{parameters\}`/)
  assert.match(modelPage, /offset: String\(\(publicPage - 1\) \* PUBLIC_MODEL_PAGE_SIZE\)/)
  assert.match(modelPage, /total=\{publicState\.data\.total\}/)
  assert.match(modelPage, /filter\(\(item\) => !item\.published_product_version_id\)/)
  assert.match(modelPage, /key=\{item\.id\}/)
  assert.match(modelPage, /<Drawer[\s\S]*公共模型目录详情/)
  assert.match(modelPage, /正式产品 \{state\.data\?\.items\.length[\s\S]*公共目录 \{publicState\.data\?\.total/)
})

test('catalog candidates stay read-only until governance and service integration finish', async () => {
  const [dataPage, modelPage] = await Promise.all([
    readFile(dataPagePath, 'utf8'),
    readFile(modelPagePath, 'utf8'),
  ])

  const dataCard = dataPage.match(/function PublicDatasetCatalogCard[\s\S]*?\n}\n\nfunction dataMarketplaceSource/)?.[0]
  const modelCard = modelPage.match(/function PublicModelCatalogCard[\s\S]*?\n}\n\nfunction modelMarketplaceSource/)?.[0]
  for (const card of [dataCard, modelCard]) {
    assert.ok(card, 'missing public catalog card')
    assert.match(card, /目录资源/)
    assert.match(card, /待治理接入/)
    assert.match(card, /查看目录详情/)
    assert.doesNotMatch(card, /CommercialOfferPreview|申请调用|申请授权|navigate\(|<Link/)
  }
})

test('marketplace filters cover discovery fields and skip irrelevant public catalog requests', async () => {
  const [dataPage, modelPage] = await Promise.all([
    readFile(dataPagePath, 'utf8'),
    readFile(modelPagePath, 'utf8'),
  ])

  assert.match(dataPage, /aria-label="搜索数据名称"/)
  assert.match(dataPage, /aria-label="搜索疾病或器官"/)
  assert.match(dataPage, /aria-label="按数据模态筛选"/)
  assert.match(dataPage, /\(sourceFilter === 'all' \|\| sourceFilter === 'public'\) && modeFilter === 'all'/)

  assert.match(modelPage, /aria-label="搜索模型名称"/)
  assert.match(modelPage, /aria-label="按模型类别筛选"/)
  assert.match(modelPage, /\(sourceFilter === 'all' \|\| sourceFilter === 'public'\) && modeFilter === 'all'/)
})

test('catalog list and detail requests preserve content and reject stale detail responses', async () => {
  const [dataPage, modelPage] = await Promise.all([
    readFile(dataPagePath, 'utf8'),
    readFile(modelPagePath, 'utf8'),
  ])

  for (const page of [dataPage, modelPage]) {
    assert.match(page, /const publicDetailRequestId = useRef\(0\)/)
    assert.match(page, /requestId === publicDetailRequestId\.current/)
    assert.match(page, /publicState\.error[\s\S]*已保留已发布/)
    assert.match(page, /publicState\.loading && publicState\.data === null/)
  }
})
