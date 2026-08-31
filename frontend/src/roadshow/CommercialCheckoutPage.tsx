import {
  AlipayCircleOutlined,
  ArrowLeftOutlined,
  BankOutlined,
  CheckCircleFilled,
  CloudDownloadOutlined,
  CreditCardOutlined,
  FileProtectOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  WechatOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Empty,
  message,
  Radio,
  Result,
  Skeleton,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { secureUuid } from '../lib/secureUuid'
import {
  acceptCommercialAgreement,
  channelCostMinor,
  completeDemoPayment,
  createCommercialDownloadGrant,
  downloadCommercialPackage,
  formatCnyMinor,
  getCommercialOrder,
  getCommercialProviderSettlements,
  listCommercialOrders,
  offerDisplayLabel,
  offerUnitLabel,
  orderLineUnitLabel,
  platformNetAfterChannelMinor,
  type CommercialOffer,
  type CommercialOrder,
  type CommercialSettlementProjection,
  type DemoPaymentMethod,
  useCommercialOffers,
} from './commerce'
import { createSingleFlight } from './requestLifecycle'
import { useRoadshow } from './RoadshowContext'
import type { ProductKind, ServiceMode } from './serviceAccess'

const { Paragraph, Text, Title } = Typography

const paymentMethods: Array<{
  value: DemoPaymentMethod
  label: string
  icon: React.ReactNode
}> = [
  { value: 'wechat_demo', label: '微信（演示）', icon: <WechatOutlined /> },
  { value: 'alipay_demo', label: '支付宝（演示）', icon: <AlipayCircleOutlined /> },
  { value: 'bank_card_demo', label: '银行卡（演示）', icon: <CreditCardOutlined /> },
]

const orderStatusLabels: Record<string, { label: string; color: string }> = {
  agreement_pending: { label: '待确认协议', color: 'gold' },
  awaiting_payment: { label: '待模拟支付', color: 'blue' },
  paid: { label: '模拟支付已完成', color: 'green' },
}

function offerRows(offers: CommercialOffer[], serviceMode?: ServiceMode) {
  return serviceMode ? offers.filter((item) => item.service_mode === serviceMode) : offers
}

export function CommercialOfferPreview({
  productKind,
  versionId,
  serviceMode,
  compact = false,
}: {
  productKind: ProductKind
  versionId: string
  serviceMode?: ServiceMode
  compact?: boolean
}) {
  const { identity } = useRoadshow()
  const state = useCommercialOffers(productKind, versionId, identity)
  const rows = offerRows(state.items, serviceMode)

  if (state.loading) return <Skeleton.Input active size="small" className="commerce-price-skeleton" />
  if (state.error || !rows.length) {
    return <Text type="secondary" className="commerce-price-fallback">报价将在结算前由平台锁定</Text>
  }
  if (compact) {
    const paidRows = rows.filter((item) => item.unit_amount_minor > 0)
    const startingPrice = Math.min(...(paidRows.length ? paidRows : rows).map((item) => item.unit_amount_minor))
    const hasFreeAuthorization = rows.some((item) => item.unit_amount_minor === 0 && item.service_mode !== 'controlled_compute')
    return <div className="commerce-offer-list is-compact">
      <div className="commerce-offer-head">
        <Text type="secondary">参考价</Text>
        <Space size={6} wrap>
          <Text strong>{formatCnyMinor(startingPrice)} 起</Text>
          <Text type="secondary">· {rows.length} 种服务{hasFreeAuthorization ? ' · 含免费授权' : ''}</Text>
        </Space>
      </div>
    </div>
  }
  return <div className="commerce-offer-list">
    {!serviceMode && <div className="commerce-offer-head">
      <Text type="secondary">平台演示报价</Text>
      <Text strong>起价 {formatCnyMinor(Math.min(...rows.map((item) => item.unit_amount_minor)))}</Text>
    </div>}
    {rows.map((offer) => <div className="commerce-offer-chip" key={offer.service_mode}>
      <span>{offerDisplayLabel(offer)}</span>
      <strong>{formatCnyMinor(offer.unit_amount_minor)}</strong>
      <small>{offerUnitLabel(offer)}</small>
    </div>)}
    <Text type="secondary" className="commerce-offer-note">以上为对外总价，已含资源使用与平台编排，最终金额以结算清单为准</Text>
  </div>
}

export function CommercialComputeQuotePreview({
  dataVersionId,
  modelVersionId,
}: {
  dataVersionId: string
  modelVersionId: string
}) {
  const { identity } = useRoadshow()
  const dataState = useCommercialOffers('data', dataVersionId, identity)
  const modelState = useCommercialOffers('model', modelVersionId, identity)
  const dataOffer = dataState.items.find((item) => item.service_mode === 'controlled_compute')
  const modelOffer = modelState.items.find((item) => item.service_mode === 'controlled_compute')
  const quoteComplete = !dataState.error && !modelState.error && Boolean(dataOffer && modelOffer)
  const lines = quoteComplete ? [dataOffer!, modelOffer!] : []
  const total = lines.reduce((sum, item) => sum + item.unit_amount_minor, 0)

  if (!dataVersionId || !modelVersionId) return null
  if (dataState.loading || modelState.loading) return <Skeleton active paragraph={{ rows: 2 }} />
  return <Card className="commerce-quote-preview" size="small" title="本次组合参考报价" extra={<Tag color="cyan">申请阶段不付款</Tag>}>
    {lines.length ? <>
      {lines.map((item) => <div className="commerce-quote-row" key={`${item.product_kind}:${item.version_id}`}>
        <span>{item.product_kind === 'data' ? '数据受控调用' : '模型受控推理'}</span>
        <Text strong>{formatCnyMinor(item.unit_amount_minor)}</Text>
      </div>)}
      <div className="commerce-quote-total"><span>组合预计总额</span><strong>{formatCnyMinor(total)}</strong></div>
    </> : <Text type="secondary">组合报价待锁定：数据与模型两项报价都可用后才会显示总额。</Text>}
    <Paragraph type="secondary">报价仅用于预算参考；提交申请不会扣款。多方审批与数字合同完成后，平台才会生成不可变结算清单。</Paragraph>
  </Card>
}

function triggerBrowserDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename || 'medtrust-commercial-package.zip'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export function CommercialOrdersPanel() {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const [items, setItems] = useState<CommercialOrder[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    listCommercialOrders(identity, controller.signal)
      .then((result) => setItems(result.items || []))
      .catch((reason) => {
        if ((reason as Error).name !== 'AbortError') setError(reason instanceof Error ? reason.message : '订单未加载')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }

  useEffect(load, [identity])
  if (identity !== 'data_requester') return null

  return <Card title="费用与交付" extra={<Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>}>
    {error && <Alert type="error" showIcon title="商业订单未加载" description={error} />}
    {!error && !items.length && !loading
      ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="审批和数字合同完成后，可在这里查看结算订单" />
      : <Table
        size="small"
        loading={loading}
        rowKey="order_id"
        dataSource={items}
        pagination={{ pageSize: 5 }}
        columns={[
          { title: '订单', dataIndex: 'order_number', render: (value) => <Text code>{value}</Text> },
          { title: '服务', render: (_, item) => item.lines.map((line) => offerDisplayLabel(line)).join(' + ') },
          { title: '买方总额', dataIndex: 'gross_amount_minor', render: formatCnyMinor },
          { title: '状态', dataIndex: 'status', render: (value) => {
            const item = orderStatusLabels[value] || { label: value, color: 'default' }
            return <Tag color={item.color}>{item.label}</Tag>
          } },
          { title: '操作', render: (_, item) => <Button type="link" onClick={() => navigate(`/commercial-checkout/${item.order_id}`)}>{item.status === 'paid' ? '查看回执与交付' : '继续结算'}</Button> },
        ]}
      />}
  </Card>
}

export function CommercialProviderSettlementPanel() {
  const { identity } = useRoadshow()
  const [projection, setProjection] = useState<CommercialSettlementProjection | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    getCommercialProviderSettlements(identity, controller.signal)
      .then(setProjection)
      .catch((reason) => {
        if ((reason as Error).name !== 'AbortError') setError(reason instanceof Error ? reason.message : '收益结算未加载')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }

  useEffect(load, [identity])
  if (identity === 'data_requester') return null
  const operator = identity === 'space_operator'
  const provider = identity === 'data_provider' || identity === 'model_provider'
  const summary = projection?.summary
  const paidOrders = (projection?.items || []).reduce((sum, item) => sum + item.paid_order_count, 0)
  const platformNet = Math.max(0, (summary?.platform_fee_minor || 0) - (summary?.channel_fee_minor || 0))

  return <Card
    title={operator ? '平台交易与服务收入' : '我的服务收益'}
    extra={<Space><Tag color="cyan">演示结算·未发生真实资金划转</Tag><Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button></Space>}
  >
    {error && <Alert type="error" showIcon title="收益结算未加载" description={error} />}
    <Spin spinning={loading}>
      {summary && <div className="commerce-settlement-stats">
        <Statistic title={operator ? '平台交易额' : '服务成交额'} value={formatCnyMinor(summary.gross_amount_minor)} />
        {operator && <Statistic title="平台毛服务费" value={formatCnyMinor(summary.platform_fee_minor ?? 0)} />}
        <Statistic title={operator ? '通道模拟成本' : '已支付订单'} value={operator ? formatCnyMinor(summary.channel_fee_minor ?? 0) : paidOrders} suffix={operator ? undefined : '项'} />
        <Statistic title={operator ? '平台净服务收入' : '提供方应收'} value={formatCnyMinor(operator ? platformNet : summary.provider_net_minor)} />
        {operator && <Statistic title="算力与存储成本" value="待云账单" />}
      </div>}
      {projection?.items.length ? <Table
        className="commerce-settlement-table"
        size="small"
        rowKey="provider_organization_id"
        dataSource={projection.items}
        pagination={operator && projection.items.length > 6 ? { pageSize: 6 } : false}
        scroll={{ x: 650 }}
        columns={[
          { title: operator ? '提供方' : '结算主体', dataIndex: 'provider_name' },
          { title: '已支付订单', dataIndex: 'paid_order_count', align: 'right' },
          { title: '成交额', dataIndex: 'gross_amount_minor', align: 'right' as const, render: formatCnyMinor },
          ...(operator ? [{ title: '平台服务费', dataIndex: 'platform_fee_minor', align: 'right' as const, render: formatCnyMinor }] : []),
          { title: '提供方应收', dataIndex: 'provider_net_minor', align: 'right' as const, render: (value) => <Text strong>{formatCnyMinor(value)}</Text> },
        ]}
      /> : !loading && !error && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="完成首笔模拟支付后，这里将生成结算投影" />}
      {operator && summary && <Paragraph type="secondary">演示内部估算：当前仅核算平台服务费与模拟支付通道成本；算力尚未接入可核验云账单，因此不展示精确金额。</Paragraph>}
      {provider && summary && <Paragraph type="secondary">这里只展示本机构的成交总额与可结算收入，平台内部成本不在提供方视图公开。</Paragraph>}
    </Spin>
  </Card>
}

export function CommercialCheckoutPage() {
  const { identity } = useRoadshow()
  const { orderId = '' } = useParams()
  const navigate = useNavigate()
  const [api, holder] = message.useMessage()
  const [order, setOrder] = useState<CommercialOrder | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [agreementChecked, setAgreementChecked] = useState(false)
  const [paymentMethod, setPaymentMethod] = useState<DemoPaymentMethod | null>(null)
  const guard = useRef(createSingleFlight()).current

  const load = () => {
    if (!orderId) return () => undefined
    const controller = new AbortController()
    setLoading(true)
    setError('')
    getCommercialOrder(orderId, identity, controller.signal)
      .then(setOrder)
      .catch((reason) => {
        if ((reason as Error).name !== 'AbortError') setError(reason instanceof Error ? reason.message : '订单未加载')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }

  useEffect(load, [identity, orderId])

  const confirmAgreement = async () => {
    if (!order || !agreementChecked) return
    await guard.run(async () => {
      setBusy('agreement'); setError('')
      try {
        const result = await acceptCommercialAgreement(order.order_id, identity, `commerce-agreement-${secureUuid()}`)
        setOrder(result)
        api.success('服务协议已确认，可选择演示支付方式')
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '协议确认失败')
      } finally { setBusy('') }
    })
  }

  const pay = async () => {
    if (!order || !paymentMethod) return
    await guard.run(async () => {
      setBusy('pay'); setError('')
      try {
        const result = await completeDemoPayment(order.order_id, paymentMethod, identity, `commerce-pay-${secureUuid()}`)
        setOrder(result)
        api.success('模拟支付已完成，未发生真实扣款')
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '模拟支付失败')
      } finally { setBusy('') }
    })
  }

  const download = async () => {
    if (!order) return
    await guard.run(async () => {
      setBusy('download'); setError('')
      try {
        const grant = await createCommercialDownloadGrant(order.order_id, identity, `commerce-grant-${secureUuid()}`)
        const blob = await downloadCommercialPackage(grant.token, identity, `commerce-download-${secureUuid()}`)
        triggerBrowserDownload(blob, grant.filename)
        api.success('安全履约包已下载，本次下载已记录')
        load()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '履约包下载失败')
      } finally { setBusy('') }
    })
  }

  const executionContractId = useMemo(() => order?.fulfillments.find((item) => item.kind === 'execution_entitlement')?.contract_id
    || (order?.source_type === 'contract' ? order.source_id : null), [order])
  const downloadableFulfillment = order?.fulfillments.find((item) => item.downloadable)
  const canCreateDownloadGrant = Boolean(order?.allowed_actions.includes('create_download_grant'))
  const deliveryCompleted = downloadableFulfillment?.download_grant_status === 'consumed'

  return <div className="page-stack commerce-checkout-page">
    {holder}
    <div className="commerce-checkout-heading">
      <div>
        <Text className="commerce-eyebrow">TRUSTED COMMERCIAL WORKFLOW</Text>
        <Title level={2}>结算与自动交付</Title>
        <Paragraph>审批与合同完成后，在此确认不可变报价、完成模拟支付并进入受控履约。</Paragraph>
      </div>
      <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/applications')}>返回我的申请</Button>
    </div>
    {error && <Alert type="error" showIcon title="操作未完成" description={error} />}
    <Spin spinning={loading}>
      {order && <>
        <div className="commerce-checkout-steps" aria-label="商业结算流程">
          {['审批与合同', '确认协议', '模拟支付', '执行或交付'].map((label, index) => {
            const current = order.status === 'agreement_pending' ? 1 : order.status === 'awaiting_payment' ? 2 : 3
            return <div className={index <= current ? 'is-active' : ''} key={label}><span>{index < current ? <CheckCircleFilled /> : index + 1}</span>{label}</div>
          })}
        </div>
        <div className="commerce-checkout-grid">
          <main className="commerce-checkout-main">
            <Card title={<Space><FileProtectOutlined />订单清单</Space>} extra={<Tag color={(orderStatusLabels[order.status] || {}).color}>{(orderStatusLabels[order.status] || {}).label || order.status}</Tag>}>
              <Descriptions size="small" column={{ xs: 1, md: 2 }}>
                <Descriptions.Item label="订单编号"><Text code>{order.order_number}</Text></Descriptions.Item>
                <Descriptions.Item label="计价币种">人民币 CNY</Descriptions.Item>
              </Descriptions>
              <Table
                rowKey="id"
                dataSource={order.lines}
                pagination={false}
                scroll={{ x: 660 }}
                columns={[
                  { title: '产品与服务', render: (_, line) => <div><strong>{line.product_name}</strong><Text type="secondary" className="phase51-code">{offerDisplayLabel(line)}</Text></div> },
                  { title: '计费单位', render: (_, line) => orderLineUnitLabel(line) },
                  { title: '金额', dataIndex: 'gross_amount_minor', align: 'right', render: (value) => <Text strong>{formatCnyMinor(value)}</Text> },
                ]}
              />
            </Card>

            {order.status === 'agreement_pending' && <Card title="确认服务协议" className="commerce-stage-card">
              <Alert type="info" showIcon title="先确认协议，再选择支付方式" description="服务范围、固定版本、许可期限、审批结论与交付边界已写入当前订单快照。付款不会替代任何数据、模型或结果审核。" />
              <Checkbox checked={agreementChecked} onChange={(event) => setAgreementChecked(event.target.checked)}>
                我已核对订单范围，并同意按当前用途、期限和交付边界履约
              </Checkbox>
              <Button type="primary" icon={<SafetyCertificateOutlined />} disabled={!agreementChecked} loading={busy === 'agreement'} onClick={confirmAgreement}>确认协议并继续</Button>
            </Card>}

            {order.status === 'awaiting_payment' && <Card title="选择演示支付方式" className="commerce-stage-card">
              <Alert type="warning" showIcon title="本页面仅模拟商业流程" description="不连接微信、支付宝或银行真实支付接口，不会扣款，也不会收集卡号、验证码或手机号。" />
              <Radio.Group value={paymentMethod} onChange={(event) => setPaymentMethod(event.target.value)} className="commerce-payment-methods">
                {paymentMethods.map((item) => <Radio.Button value={item.value} key={item.value}>{item.icon}<span>{item.label}</span></Radio.Button>)}
              </Radio.Group>
              <Button type="primary" size="large" disabled={!paymentMethod} loading={busy === 'pay'} onClick={pay}>完成模拟支付</Button>
            </Card>}

            {order.status === 'paid' && <Card className="commerce-paid-card">
              <Result
                status="success"
                title="模拟支付成功"
                subTitle={order.payment ? `DEMO-PAY 回执 ${order.payment.transaction_number} · ${new Date(order.payment.paid_at).toLocaleString()}` : 'DEMO-PAY 回执已生成'}
                extra={<Space wrap>
                  {executionContractId && <Button type="primary" icon={<BankOutlined />} onClick={() => navigate(`/execution/${executionContractId}`)}>进入受控执行准备</Button>}
                  {canCreateDownloadGrant && <Button type="primary" icon={<CloudDownloadOutlined />} loading={busy === 'download'} onClick={download}>下载安全履约包</Button>}
                  {downloadableFulfillment && !canCreateDownloadGrant && <Button icon={<CheckCircleFilled />} disabled>{deliveryCompleted ? '已完成一次性交付' : '一次性下载凭证已生成'}</Button>}
                  <Button onClick={load}>刷新履约状态</Button>
                </Space>}
              />
              <Alert type="success" showIcon title={executionContractId ? '受控调用权益已生效' : '授权交付权益已生效'} description={executionContractId
                ? '下一步仍需执行准备、受控计算和结果审核；付款不会直接释放原始数据或模型。'
                : '履约包只包含当前白名单中的许可、模型卡或公开数据清单；不提供未审批患者数据或模型权重。'} />
            </Card>}
          </main>

          <aside className="commerce-settlement-card">
            <Card title={identity === 'space_operator' ? '运营结算摘要' : identity === 'data_provider' || identity === 'model_provider' ? '我的结算摘要' : '费用摘要'}>
              {identity === 'space_operator' ? <>
                <div className="commerce-buyer-total"><Text type="secondary">对外订单总额</Text><strong>{formatCnyMinor(order.gross_amount_minor ?? 0)}</strong></div>
                <div className="commerce-settlement-row"><span>提供方收入合计</span><Text strong>{formatCnyMinor(order.provider_net_minor ?? 0)}</Text></div>
                {order.lines.map((line) => <div className="commerce-provider-line" key={line.id}><span>{line.provider_name || line.product_name}</span><Text>{formatCnyMinor(line.provider_net_minor ?? 0)}</Text></div>)}
                <div className="commerce-settlement-row"><span>平台技术服务费（5%）</span><Text strong>{formatCnyMinor(order.platform_fee_minor ?? 0)}</Text></div>
                <div className="commerce-provider-line"><span>通道模拟成本（0.6%）</span><Text>-{formatCnyMinor(channelCostMinor(order))}</Text></div>
                <div className="commerce-provider-line"><span>算力与存储成本</span><Text>待接入云账单</Text></div>
                <div className="commerce-settlement-row is-net"><span>平台服务费净额</span><Text strong>{formatCnyMinor(platformNetAfterChannelMinor(order))}</Text></div>
                <Paragraph type="secondary">演示内部估算：平台报价已覆盖资源使用与编排；当前没有接入可核验的云账单，因此不展示虚构的精确算力金额。</Paragraph>
              </> : identity === 'data_provider' || identity === 'model_provider' ? <>
                <div className="commerce-buyer-total"><Text type="secondary">本机构服务成交额</Text><strong>{formatCnyMinor(order.subtotal_amount_minor ?? 0)}</strong></div>
                <div className="commerce-settlement-row is-net"><span>本机构可结算收入</span><Text strong>{formatCnyMinor(order.provider_net_minor ?? 0)}</Text></div>
              </> : <>
                <div className="commerce-buyer-total"><Text type="secondary">需求方应付总额</Text><strong>{formatCnyMinor(order.gross_amount_minor ?? 0)}</strong><small>按所选服务统一报价，不展示平台内部成本构成</small></div>
                <Paragraph type="secondary">总价覆盖当前订单约定的服务与履约范围，最终以已确认的结算清单为准。</Paragraph>
              </>}
            </Card>
          </aside>
        </div>
      </>}
      {!order && !loading && !error && <Empty description="未找到商业订单" />}
    </Spin>
  </div>
}
