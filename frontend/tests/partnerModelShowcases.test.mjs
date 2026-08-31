import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const modelPagePath = new URL('../src/roadshow/ModelProductLifecyclePages.tsx', import.meta.url)

test('model marketplace includes clearly bounded partner showcase cards', async () => {
  const source = await readFile(modelPagePath, 'utf8')

  assert.match(source, /name: 'PathoWish'/)
  assert.match(source, /provider: '罗小罗科技（北京）有限公司'/)
  assert.match(source, /name: '沐光矩阵'/)
  assert.match(source, /合作展示/)
  assert.match(source, /待接入/)
  assert.match(source, /接入后开放/)
})

test('showcase details are progressive and cannot enter application or payment', async () => {
  const source = await readFile(modelPagePath, 'utf8')
  const showcase = source.match(/function PartnerModelShowcaseCard[\s\S]*?\n}\n\nfunction PageLoad/)?.[0]

  assert.ok(showcase, 'missing PartnerModelShowcaseCard')
  assert.match(showcase, /marketplace-card-details/)
  assert.match(showcase, /<Popover/)
  assert.match(showcase, /<Button type="primary" disabled>待接入<\/Button>/)
  assert.doesNotMatch(showcase, /navigate\(|<Link|CommercialOfferPreview|setAuthorizationTarget|申请调用|申请授权|立即支付/)
})
