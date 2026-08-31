import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import {
  basisPointsAmount,
  formatCnyMinor,
  simulatedChannelCostMinor,
} from '../src/roadshow/commerceMoney.ts'

const commerceApiPath = new URL('../src/roadshow/commerce.ts', import.meta.url)
const checkoutPath = new URL('../src/roadshow/CommercialCheckoutPage.tsx', import.meta.url)
const routerPath = new URL('../src/router/index.tsx', import.meta.url)
const serviceAccessPath = new URL('../src/roadshow/serviceAccess.ts', import.meta.url)
const applicationPath = new URL('../src/roadshow/ApplicationLifecyclePages.tsx', import.meta.url)
const contractPath = new URL('../src/roadshow/ContractLifecyclePages.tsx', import.meta.url)

test('all settlement arithmetic uses integer minor units with half-up basis-point rounding', () => {
  assert.equal(basisPointsAmount(107900, 500), 5395)
  assert.equal(basisPointsAmount(107900, 60), 647)
  assert.equal(simulatedChannelCostMinor(107900), 647)
  assert.equal(simulatedChannelCostMinor(107900, 646), 646)
  assert.equal(5395 - 647, 4748)
})

test('CNY amounts are rendered from integer cents without hiding decimals', () => {
  assert.equal(formatCnyMinor(107900), '¥1,079.00')
  assert.equal(formatCnyMinor(39900), '¥399.00')
  assert.equal(formatCnyMinor(0), '¥0.00')
  assert.equal(formatCnyMinor(1.5), '¥--')
})

test('commerce writes carry identity and idempotency headers and never submit a client amount', async () => {
  const api = await readFile(commerceApiPath, 'utf8')
  assert.ok(!api.includes("'X-Demo-Identity': identity"))
  assert.ok(api.includes('function commerceHeaders(_identity: DemoIdentity'))
  assert.ok(api.includes("'Idempotency-Key': idempotencyKey"))
  assert.ok(api.includes('identity, idempotencyKey, { method }'))
  assert.ok(!api.includes('JSON.stringify({ method, amount'))
  assert.ok(api.includes("body: JSON.stringify({ token })"))
})

test('checkout is explicitly simulated, gated by agreement and does not collect payment credentials', async () => {
  const page = await readFile(checkoutPath, 'utf8')
  assert.ok(page.includes('先确认协议，再选择支付方式'))
  assert.ok(page.includes('完成模拟支付'))
  assert.ok(page.includes('数据与模型两项报价都可用后才会显示总额'))
  assert.ok(page.includes('DEMO-PAY 回执'))
  assert.ok(page.includes("allowed_actions.includes('create_download_grant')"))
  assert.ok(page.includes('已完成一次性交付'))
  assert.ok(page.includes('不连接微信、支付宝或银行真实支付接口，不会扣款'))
  assert.ok(page.includes("identity === 'space_operator' ? <>"))
  assert.ok(page.includes('通道模拟成本（0.6%）'))
  assert.ok(page.includes('平台技术服务费（5%）'))
  assert.ok(page.includes('起价 '))
  assert.ok(page.includes('按所选服务统一报价，不展示平台内部成本构成'))
  assert.ok(page.includes('演示内部估算'))
  assert.ok(page.includes('当前没有接入可核验的云账单'))
  assert.ok(!page.includes('name="card_number"'))
  assert.ok(!page.includes('name="cvv"'))
  assert.ok(!page.includes('name="phone"'))
  assert.ok(!page.includes('立即支付'))
})

test('checkout stays a hidden detail route and service wording does not sell patient data', async () => {
  const [router, labels, page, commerce] = await Promise.all([
    readFile(routerPath, 'utf8'),
    readFile(serviceAccessPath, 'utf8'),
    readFile(checkoutPath, 'utf8'),
    readFile(commerceApiPath, 'utf8'),
  ])
  assert.ok(router.includes("path: '/commercial-checkout/:orderId'"))
  assert.ok(labels.includes("deidentified_data_delivery: '匿名化数据授权交付'"))
  assert.ok(commerce.includes('公开数据授权交付（许可¥0）'))
  assert.ok(commerce.includes('仅在受控环境完成本次计算，不交付原始数据或模型权重'))
  assert.ok(commerce.includes('交付公开数据清单、许可与授权文件'))
  assert.ok(commerce.includes('交付模型卡、清单与使用许可，不包含模型权重'))
  for (const source of [labels, page]) {
    assert.ok(!source.includes('购买患者数据'))
    assert.ok(!source.includes('出售患者数据'))
  }
})

test('provider and operator settlement projections stay inside the existing workbench', async () => {
  const [api, page, applications] = await Promise.all([
    readFile(commerceApiPath, 'utf8'),
    readFile(checkoutPath, 'utf8'),
    readFile(applicationPath, 'utf8'),
  ])
  assert.ok(api.includes("commerceGet('/commercial-provider-settlements'"))
  assert.ok(page.includes('export function CommercialProviderSettlementPanel'))
  assert.ok(page.includes("operator ? '平台交易与服务收入' : '我的服务收益'"))
  for (const label of ['平台交易额', '平台毛服务费', '通道模拟成本', '平台净服务收入', '提供方应收', '算力与存储成本', '待云账单']) {
    assert.ok(page.includes(label), label)
  }
  assert.match(page, /operator && <Statistic title="平台毛服务费"/)
  assert.match(page, /\.\.\.\(operator \? \[\{ title: '平台服务费'/)
  assert.ok(page.includes('这里只展示本机构的成交总额与可结算收入'))
  assert.ok(page.includes('演示结算·未发生真实资金划转'))
  assert.ok(applications.includes('<CommercialProviderSettlementPanel />'))
})

test('marketplace compact quotes stay one-line and do not expose fee composition', async () => {
  const page = await readFile(checkoutPath, 'utf8')
  assert.match(page, /if \(compact\)[\s\S]*参考价[\s\S]*种服务/)
  assert.ok(page.includes('含免费授权'))
  assert.ok(page.includes('已含资源使用与平台编排'))
  assert.ok(!page.includes('已含 5% 平台技术服务费'))
  assert.ok(!page.includes("platform_fee_rate_bps: 500"))
  assert.ok(!page.includes("channel_fee_rate_bps: 60"))
})

test('an active contract cannot enter execution until its paid order carries an execution entitlement', async () => {
  const contract = await readFile(contractPath, 'utf8')
  assert.ok(contract.includes('listCommercialOrders(identity, controller.signal)'))
  assert.ok(!contract.includes("detail.status !== 'active' || identity === 'data_requester'"))
  assert.ok(contract.includes(': activationSecurityReady'))
  assert.ok(contract.includes('当前不可结算'))
  assert.ok(contract.includes('查看结算与履约'))
  assert.match(contract, /const contractOrder[\s\S]*order\.source_type === 'contract'[\s\S]*order\.source_id === detail\.contract_id/)
  assert.match(contract, /const executionOrder[\s\S]*contractOrder\?\.status === 'paid'[\s\S]*item\.kind === 'execution_entitlement'/)
  assert.ok(contract.includes('等待需求方完成结算'))
  assert.match(contract, /executionOrder[\s\S]*进入执行准备/)
})
