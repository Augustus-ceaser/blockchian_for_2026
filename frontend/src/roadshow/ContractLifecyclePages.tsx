import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Empty,
  Flex,
  List,
  Progress,
  Space,
  Spin,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import { secureUuid } from '../lib/secureUuid'
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CodeSandboxOutlined,
  CopyOutlined,
  EyeOutlined,
  FileProtectOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ShoppingCartOutlined,
} from '@ant-design/icons'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { platformCommand, platformGet } from './api'
import { createSingleFlight, startAbortableLoad } from './requestLifecycle'
import { useRoadshow } from './RoadshowContext'
import type { ContractSecurityResult, ContractSecurityValidation } from './types'
import {
  createOrderFromContract,
  listCommercialOrders,
  type CommercialOrder,
} from './commerce'

const { Paragraph, Text, Title } = Typography

type Party = {
  party_id: string
  organization_name: string
  role: string
  signing_order: number
  confirmed: boolean
  confirmed_at: string | null
  confirmed_digest: string | null
}

type DigitalContract = {
  contract_id: string
  contract_number: string
  application_id: string
  application_number: string
  revision_id: string
  revision_no: number
  status: string
  name: string
  summary: string
  content_digest: string
  digest_short: string
  created_at: string
  proposed_at: string | null
  activated_at: string | null
  effective_until: string | null
  terms: Record<string, any>
  policy_convergence: {
    final?: Record<string, any>
    matrix?: Array<Record<string, any>>
    blockers?: Array<{ code: string; message: string }>
  }
  data_object: { name: string; version_id: string; snapshot_digest: string } | null
  model_object: { name: string; version_id: string; snapshot_digest: string } | null
  parties: Party[]
  confirmation_progress: { completed: number; required: number }
  policies: Array<Record<string, any>>
  next_step: string
  security_validation: ContractSecurityValidation | null
}

const securityResultMeta: Record<ContractSecurityResult, { color: string; label: string }> = {
  PASS: { color: 'green', label: '通过' },
  PENDING: { color: 'gold', label: '待完成' },
  BLOCKER: { color: 'red', label: '已阻断' },
}

const securityCheckLabels: Record<string, string> = {
  party_authority: '主体权限',
  terms_integrity: '条款完整性',
  asset_integrity: '资产版本',
  policy_integrity: '策略完整性',
  content_integrity: '合约内容',
  effective_window: '有效期限',
  signature_binding: '四方确认',
  execution_binding: '执行绑定',
}

const purposeLabels: Record<string, string> = {
  research_analysis: '科研分析',
  model_validation: '模型验证',
  model_training: '模型训练',
  inference: '受控推理',
}

const outputLabels: Record<string, string> = {
  aggregate_metrics: '聚合性能指标',
  confusion_matrix: '混淆矩阵',
  execution_summary: '执行摘要',
  approved_report: '审批报告',
}

const prohibitedActionLabels: Record<string, string> = {
  raw_data_export: '禁止原始数据导出',
  raw_data_download: '禁止原始数据导出',
  reidentification: '禁止重识别',
  re_identification: '禁止重识别',
  redistribution: '禁止再分发',
  onward_transfer: '禁止再分发',
}

const identityAssuranceLabels: Record<string, string> = {
  platform_session_and_admitted_organization: '平台账号、组织准入与角色校验',
}

const securityCheckOrder = [
  'party_authority',
  'terms_integrity',
  'asset_integrity',
  'policy_integrity',
  'content_integrity',
  'effective_window',
  'signature_binding',
  'execution_binding',
]

function securityResultTag(result: ContractSecurityResult) {
  const meta = securityResultMeta[result]
  return <Tag color={meta.color}>{meta.label}</Tag>
}

function displayCode(value: string, labels: Record<string, string>) {
  return labels[value] || value.replaceAll('_', ' ')
}

function formatTimestamp(value: string) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function SecurityValidationCard({ validation }: { validation: ContractSecurityValidation | null }) {
  if (!validation) {
    return <Card title={<Space><SafetyCertificateOutlined /> 安全合约验证</Space>}>
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待服务端生成安全验证结果" />
    </Card>
  }
  const summary = validation.summary
  const checks = [...validation.checks].sort((left, right) => {
    const leftIndex = securityCheckOrder.indexOf(left.code)
    const rightIndex = securityCheckOrder.indexOf(right.code)
    return (leftIndex < 0 ? securityCheckOrder.length : leftIndex) - (rightIndex < 0 ? securityCheckOrder.length : rightIndex)
  })
  const identityAssurance = summary.identity_assurance
    ? identityAssuranceLabels[summary.identity_assurance] || '平台账号、组织准入与角色校验'
    : '平台账号、组织准入与角色校验'

  return <Card
    className="phase54-security-card"
    title={<Space><SafetyCertificateOutlined /> 安全合约验证 {securityResultTag(validation.overall)}</Space>}
    extra={<Text type="secondary">校验于 {formatTimestamp(validation.checked_at)}</Text>}
  >
    <Descriptions bordered size="small" column={{ xs: 1, md: 2, xl: 3 }}>
      <Descriptions.Item label="许可用途">{displayCode(summary.purpose_code, purposeLabels)}</Descriptions.Item>
      <Descriptions.Item label="运行次数">{summary.run_count} 次</Descriptions.Item>
      <Descriptions.Item label="有效期至">{formatTimestamp(summary.effective_until)}</Descriptions.Item>
      <Descriptions.Item label="允许输出" span={{ xs: 1, md: 2 }}>
        <Space size={[4, 4]} wrap>{summary.allowed_outputs.map((item) => <Tag key={item}>{displayCode(item, outputLabels)}</Tag>)}</Space>
      </Descriptions.Item>
      <Descriptions.Item label="执行边界">
        <Space size={[4, 4]} wrap>
          <Tag color={summary.network_allowed ? 'gold' : 'green'}>{summary.network_allowed ? '允许联网' : '禁止外网'}</Tag>
          <Tag color={summary.output_review_required ? 'blue' : 'default'}>{summary.output_review_required ? '输出需审核' : '无需输出审核'}</Tag>
        </Space>
      </Descriptions.Item>
      <Descriptions.Item label="身份保障" span={{ xs: 1, md: 3 }}>{identityAssurance}</Descriptions.Item>
    </Descriptions>

    <div className="phase54-prohibited-actions">
      <Text strong>明确禁止</Text>
      <Space size={[6, 6]} wrap>
        {summary.prohibited_actions.map((item) => <Tag color="red" key={item}>{displayCode(item, prohibitedActionLabels)}</Tag>)}
      </Space>
    </div>

    <div className="phase54-security-check-grid">
      {checks.map((check) => <div className={`phase54-security-check is-${check.result.toLowerCase()}`} key={check.code}>
        <div><Text strong>{securityCheckLabels[check.code] || check.message || check.code}</Text>{securityResultTag(check.result)}</div>
        <Text type="secondary">{check.message}</Text>
      </div>)}
    </div>

    <div className="phase54-security-snapshot">
      <Text type="secondary">验证快照</Text>
      <Text code copyable>{validation.snapshot_digest}</Text>
      <Text type="secondary">规则版本 {validation.profile_version}</Text>
    </div>
  </Card>
}

function statusTag(status: string) {
  const colors: Record<string, string> = {
    draft: 'default',
    proposed: 'gold',
    signed: 'blue',
    active: 'green',
    returned: 'orange',
    declined: 'red',
    expired: 'volcano',
  }
  const labels: Record<string, string> = {
    draft: '草稿',
    proposed: '待四方确认',
    signed: '四方已确认',
    active: '已生效',
  }
  return <Tag color={colors[status] || 'default'}>{labels[status] || status}</Tag>
}

function useLoad<T>(path: string) {
  const { identity } = useRoadshow()
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    setLoading(true)
    setError('')
    return startAbortableLoad(
      (signal) => platformGet<T>(path, identity, signal),
      {
        onSuccess: setData,
        onError: (reason) => setError(reason instanceof Error ? reason.message : '读取失败'),
        onSettled: () => setLoading(false),
      },
    )
  }, [identity, nonce, path])
  return { data, error, loading, refresh: () => setNonce((value) => value + 1) }
}

export function ContractManagementPage() {
  const navigate = useNavigate()
  const state = useLoad<{ items: DigitalContract[]; total: number }>('/digital-contracts')
  return <div className="page-stack">
    <Flex justify="space-between" align="center" wrap>
      <div><Title level={2}>数字合约</Title><Paragraph type="secondary">查看与本组织相关的固定版本合约和四方确认状态。</Paragraph></div>
      <Button icon={<ReloadOutlined />} onClick={state.refresh}>刷新</Button>
    </Flex>
    {state.error && <Alert type="error" showIcon title="无法读取数字合约" description={state.error} />}
    <Spin spinning={state.loading}>
      <Card>
        <Table
          rowKey="contract_id"
          dataSource={state.data?.items || []}
          locale={{ emptyText: <Empty description="暂无相关数字合约" /> }}
          scroll={{ x: 900 }}
          columns={[
            { title: '合约编号', dataIndex: 'contract_number' },
            { title: '关联申请', dataIndex: 'application_number' },
            { title: '版本', render: (_, item) => `v${item.revision_no}` },
            { title: '状态', render: (_, item) => statusTag(item.status) },
            { title: '确认进度', render: (_, item) => `${item.confirmation_progress.completed}/${item.confirmation_progress.required}` },
            { title: '有效期', render: (_, item) => item.effective_until ? new Date(item.effective_until).toLocaleDateString() : '-' },
            { title: '操作', render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => navigate(`/contracts/${item.contract_id}`)}>查看</Button> },
          ]}
        />
      </Card>
    </Spin>
  </div>
}

export function ContractDetailPage() {
  const { identity } = useRoadshow()
  const { contractId = '' } = useParams()
  const navigate = useNavigate()
  const [api, holder] = message.useMessage()
  const state = useLoad<DigitalContract>(`/digital-contracts/${contractId}`)
  const audit = useLoad<{ items: Array<Record<string, any>>; total: number }>(`/digital-contracts/${contractId}/audit-events`)
  const [accepted, setAccepted] = useState(false)
  const [busy, setBusy] = useState('')
  const [commercialOrders, setCommercialOrders] = useState<CommercialOrder[]>([])
  const [commercialLoading, setCommercialLoading] = useState(false)
  const [commercialError, setCommercialError] = useState('')
  const guard = useRef(createSingleFlight()).current
  const detail = state.data
  const myRole = {
    data_requester: 'data_requester',
    data_provider: 'data_provider',
    model_provider: 'model_provider',
    space_operator: 'operator_witness',
  }[identity]
  const myParty = detail?.parties.find((item) => item.role === myRole)
  const canConfirm = detail?.status === 'proposed' && myParty && !myParty.confirmed
  const canActivate = identity === 'space_operator' && detail?.status === 'signed'
  const matrix = detail?.policy_convergence.matrix || []
  const finalPolicy = detail?.policy_convergence.final || {}
  const securityValidation = detail?.security_validation
  const confirmationSecurityReady = Boolean(securityValidation) && securityValidation?.overall !== 'BLOCKER'
  const activationSecurityReady = securityValidation?.overall === 'PASS'
  useEffect(() => {
    if (!detail || detail.status !== 'active') {
      setCommercialOrders([])
      setCommercialLoading(false)
      setCommercialError('')
      return
    }
    const controller = new AbortController()
    setCommercialLoading(true)
    setCommercialError('')
    listCommercialOrders(identity, controller.signal)
      .then((result) => setCommercialOrders(result.items || []))
      .catch((reason) => {
        if ((reason as Error).name !== 'AbortError') {
          setCommercialError(reason instanceof Error ? reason.message : '结算状态核验失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setCommercialLoading(false)
      })
    return () => controller.abort()
  }, [detail?.contract_id, detail?.status, identity])
  const contractOrder = detail ? commercialOrders.find((order) => order.source_type === 'contract'
    && order.source_id === detail.contract_id) : undefined
  const executionOrder = contractOrder?.status === 'paid'
    && contractOrder.fulfillments.some((item) => item.kind === 'execution_entitlement')
    ? contractOrder
    : undefined
  const command = async (action: 'confirm' | 'activate') => {
    if (!detail) return
    await guard.run(async () => {
      setBusy(action)
      try {
        const path = `/digital-contracts/${detail.contract_id}/${action}`
        const body = action === 'confirm' ? {
          contract_revision_id: detail.revision_id,
          content_digest: detail.content_digest,
          declaration_accepted: accepted,
        } : undefined
        await platformCommand(path, identity, `phase5.4-ui:${action}:${secureUuid()}`, body)
        api.success(action === 'confirm' ? '本方确认已记录' : '数字合约已生效')
        setAccepted(false)
        state.refresh()
        audit.refresh()
      } catch (reason) {
        api.error(reason instanceof Error ? reason.message : '操作失败')
      } finally {
        setBusy('')
      }
    })
  }
  const checkout = async () => {
    if (!detail || identity !== 'data_requester') return
    await guard.run(async () => {
      setBusy('checkout')
      try {
        const order = await createOrderFromContract(
          detail.contract_id,
          identity,
          `commerce-contract-order-${secureUuid()}`,
        )
        navigate(`/commercial-checkout/${order.order_id}`)
      } catch (reason) {
        api.error(reason instanceof Error ? reason.message : '结算订单创建失败')
      } finally {
        setBusy('')
      }
    })
  }
  return <div className="page-stack">
    {holder}
    <Flex justify="space-between" align="center" wrap gap={12}>
      <div><Title level={2}>{detail?.name || '数字合约'}</Title><Paragraph type="secondary">{detail?.contract_number} · 内部结构化确认</Paragraph></div>
      <Space wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/contracts')}>返回列表</Button>
        {detail?.status === 'active' && identity === 'data_requester' && (contractOrder
          ? <Button type="primary" icon={<ShoppingCartOutlined />} onClick={() => navigate(`/commercial-checkout/${contractOrder.order_id}`)}>{contractOrder.status === 'paid' ? '查看结算与履约' : '继续结算'}</Button>
          : activationSecurityReady
            ? <Button type="primary" icon={<ShoppingCartOutlined />} loading={busy === 'checkout'} onClick={checkout}>去结算</Button>
            : <Button icon={<ShoppingCartOutlined />} disabled title="安全合约验证全部通过后才能结算">当前不可结算</Button>)}
        {detail?.status === 'active' && identity !== 'data_requester' && (executionOrder
          ? <Button type="primary" icon={<CodeSandboxOutlined />} disabled={!activationSecurityReady} title={activationSecurityReady ? undefined : '安全合约验证全部通过后才能进入执行准备'} onClick={() => navigate(`/execution/${detail.contract_id}`)}>进入执行准备</Button>
          : <Button icon={<CodeSandboxOutlined />} disabled loading={commercialLoading}>{commercialError ? '结算状态核验失败' : '等待需求方完成结算'}</Button>)}
        <Button icon={<ReloadOutlined />} onClick={() => { state.refresh(); audit.refresh() }}>刷新</Button>
      </Space>
    </Flex>
    {state.error && <Alert type="error" showIcon title="无法读取合约" description={state.error} />}
    <Spin spinning={state.loading}>
      {detail && <>
        <Alert
          type={detail.status === 'active' && activationSecurityReady ? 'success' : 'warning'}
          showIcon
          title={detail.status === 'active'
            ? activationSecurityReady ? '合约已生效' : '合约当前不可履约'
            : '待完成平台内部结构化确认'}
          description={detail.status === 'active'
            ? activationSecurityReady
              ? '当前版本已经冻结，完成结算后进入受控执行准备。'
              : '当前安全验证存在阻断，请续签或修复合约后再结算。'
            : '确认方式为平台内部结构化确认；每次确认均绑定当前版本与内容摘要。'}
        />
        <Card>
          <Descriptions bordered column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="合约编号">{detail.contract_number}</Descriptions.Item>
            <Descriptions.Item label="状态">{statusTag(detail.status)}</Descriptions.Item>
            <Descriptions.Item label="当前版本">v{detail.revision_no}</Descriptions.Item>
            <Descriptions.Item label="关联申请">{detail.application_number}</Descriptions.Item>
            <Descriptions.Item label="固定数据版本">{detail.data_object?.name}</Descriptions.Item>
            <Descriptions.Item label="固定模型版本">{detail.model_object?.name}</Descriptions.Item>
          </Descriptions>
        </Card>
        <SecurityValidationCard validation={detail.security_validation || null} />
        {(detail.policy_convergence.blockers || []).length > 0 && <Alert type="error" showIcon title="策略存在 BLOCKER" description={detail.policy_convergence.blockers?.map((item) => item.message).join('；')} />}
        <details className="phase54-technical-evidence">
          <summary>查看技术证据</summary>
          <div className="phase54-technical-evidence__body">
            <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
              <Descriptions.Item label="合约内容摘要" span={{ xs: 1, md: 2 }}>
                <Text code copyable={{ icon: <CopyOutlined /> }}>{detail.content_digest}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="运行次数">{finalPolicy.run_count}</Descriptions.Item>
              <Descriptions.Item label="有效期">{finalPolicy.valid_days} 天</Descriptions.Item>
              <Descriptions.Item label="外网">{finalPolicy.network_allowed ? '允许' : '禁止'}</Descriptions.Item>
              <Descriptions.Item label="允许输出">{(finalPolicy.allowed_outputs || []).join('、')}</Descriptions.Item>
              <Descriptions.Item label="永久禁止" span={{ xs: 1, md: 2 }}>{(finalPolicy.forbidden_outputs || []).join('、')}</Descriptions.Item>
            </Descriptions>
            <Title level={5}>策略形成依据</Title>
            <div className="phase54-policy-scroll">
              <Table
                pagination={false}
                rowKey="constraint"
                dataSource={matrix}
                scroll={{ x: 860 }}
                columns={[
                  { title: '约束项', dataIndex: 'constraint' },
                  { title: '申请请求', dataIndex: 'request', render: (value) => JSON.stringify(value) },
                  { title: '数据方批准', dataIndex: 'data_provider', render: (value) => JSON.stringify(value) },
                  { title: '模型方批准', dataIndex: 'model_provider', render: (value) => JSON.stringify(value) },
                  { title: '平台规则', dataIndex: 'platform' },
                  { title: '最终合约', dataIndex: 'final', render: (value) => JSON.stringify(value) },
                ]}
              />
            </div>
          </div>
        </details>
        <Card title="四方确认">
          <Progress percent={Math.round(detail.confirmation_progress.completed / detail.confirmation_progress.required * 100)} />
          <div className="phase54-party-grid">
            {detail.parties.map((party) => <Card key={party.party_id} size="small">
              <Flex gap={10} align="start">
                <SafetyCertificateOutlined />
                <div><strong>{party.organization_name}</strong><div>{party.role}</div><Tag color={party.confirmed ? 'green' : 'gold'}>{party.confirmed ? '已确认' : '待确认'}</Tag>{party.confirmed_at && <div><Text type="secondary">{new Date(party.confirmed_at).toLocaleString()} · v{detail.revision_no} · {party.confirmed_digest?.slice(0, 19)}</Text></div>}</div>
              </Flex>
            </Card>)}
          </div>
          {canConfirm && <Flex vertical gap={12} style={{ marginTop: 16 }}>
            <Checkbox checked={accepted} onChange={(event) => setAccepted(event.target.checked)}>我已核对当前版本和完整摘要，并确认本次操作将记录为平台内部结构化确认。</Checkbox>
            <Button type="primary" icon={<CheckCircleOutlined />} disabled={!accepted || !confirmationSecurityReady} loading={busy === 'confirm'} onClick={() => command('confirm')}>确认当前版本</Button>
          </Flex>}
          {canActivate && <Button type="primary" icon={<FileProtectOutlined />} disabled={!activationSecurityReady} title={activationSecurityReady ? undefined : '八项安全检查全部通过后才能激活'} loading={busy === 'activate'} onClick={() => command('activate')} style={{ marginTop: 16 }}>激活数字合约</Button>}
        </Card>
        <Card title="合约审计证据">
          <Timeline items={(audit.data?.items || []).map((event) => ({
            color: event.result === 'success' ? 'green' : 'red',
            content: <div><strong>{event.event_type}</strong><div><Text type="secondary">{new Date(event.occurred_at).toLocaleString()} · {event.event_id}</Text></div></div>,
          }))} />
        </Card>
      </>}
    </Spin>
  </div>
}
