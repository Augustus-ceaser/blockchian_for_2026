import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const dataPagePath = new URL('../src/roadshow/DataProductLifecyclePages.tsx', import.meta.url)
const modelPagePath = new URL('../src/roadshow/ModelProductLifecyclePages.tsx', import.meta.url)
const stylesPath = new URL('../src/styles.css', import.meta.url)

test('data and model marketplaces expose one source filter and concise card summary', async () => {
  const [dataPage, modelPage] = await Promise.all([
    readFile(dataPagePath, 'utf8'),
    readFile(modelPagePath, 'utf8'),
  ])

  assert.match(dataPage, /title="数据商城"/)
  assert.match(dataPage, /aria-label="按数据来源筛选"/)
  assert.match(dataPage, /公共来源/)
  assert.match(dataPage, /机构自有/)
  assert.match(dataPage, /marketplace-card-summary/)
  assert.match(dataPage, /marketplace-card-details/)
  assert.doesNotMatch(dataPage, /marketplace-card-summary[\s\S]{0,500}marketplace-card-facts/)

  assert.match(modelPage, /title="模型商城"/)
  assert.match(modelPage, /aria-label="按模型来源筛选"/)
  assert.match(modelPage, /公共开源/)
  assert.match(modelPage, /机构模型/)
  assert.match(modelPage, /合作展示/)
  assert.match(modelPage, /marketplace-card-summary/)
  assert.match(modelPage, /marketplace-card-details/)
  assert.doesNotMatch(modelPage, /marketplace-card-summary[\s\S]{0,500}marketplace-card-facts/)
})

test('technical tags stay hidden without changing card height until hover or keyboard focus', async () => {
  const styles = await readFile(stylesPath, 'utf8')

  assert.match(styles, /\.marketplace-card-details\s*\{[\s\S]*?min-height:\s*64px;[\s\S]*?max-height:\s*64px;/)
  assert.match(styles, /\.marketplace-card-details > \*\s*\{[\s\S]*?opacity:\s*0;/)
  assert.match(styles, /\.marketplace-card:hover \.marketplace-card-details > \*,[\s\S]*?\.marketplace-card:focus-within \.marketplace-card-details > \*\s*\{\s*opacity:\s*1;/)
  assert.match(styles, /@media \(hover: none\), \(pointer: coarse\)/)
})
