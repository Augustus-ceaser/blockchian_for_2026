import {
  AuditOutlined, CheckCircleOutlined, CloudDownloadOutlined,
  CodeSandboxOutlined, FileProtectOutlined, LockOutlined,
  ReloadOutlined, SafetyCertificateOutlined, SendOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import {
  Alert, Button, Card, Col, Collapse, Descriptions, Empty, Flex, List, Progress, Row, Space,
  Spin, Steps, Table, Tag, Timeline, Typography, message,
} from 'antd'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { secureUuid } from '../lib/secureUuid'
import { platformGet, roadshowCommand, roadshowDownload, roadshowGet } from './api'
import { createSingleFlight, startAbortableLoad } from './requestLifecycle'
import { roleProfiles, useRoadshow } from './RoadshowContext'
import type { CatalogItem, DemoIdentity, RoadshowOverview, WorkflowState } from './types'

const { Text, Title, Paragraph } = Typography

const stateLabel: Record<string, string> = {
  draft: '草稿', under_review: '上架审核中', approved: '已批准', published: '已发布', active: '已生效',
  submitted: '已提交', prechecking: '平台预审', provider_review: '提供方审核', proposed: '待签署', signed: '已签署',
  prepared: '已准备', reserved: '已预留', dispatched: '已投递', running: '运行中', succeeded: '运行完成',
  quarantined: '隔离审核中', pending: '待处理', claimed: '处理中', released: '已发布', rejected: '已拒绝',
  available: '可下载', decided: '已处理', failed: '运行失败', interrupted: '已中断',
}
function statusTag(value: string | null | boolean) {
  if (typeof value === 'boolean') return <Tag color={value ? 'green' : 'default'}>{value ? '已就绪' : '未就绪'}</Tag>
  if (!value) return <Tag>尚未开始</Tag>
  const good = ['published', 'approved', 'active', 'signed', 'succeeded', 'released', 'available', 'decided'].includes(value)
  const progress = ['under_review', 'submitted', 'prechecking', 'provider_review', 'proposed', 'reserved', 'dispatched', 'running', 'quarantined', 'pending', 'claimed'].includes(value)
  const bad = ['rejected', 'failed', 'interrupted'].includes(value)
  return <Tag color={good ? 'green' : progress ? 'blue' : bad ? 'red' : 'default'}>{stateLabel[value] || value}</Tag>
}

function useRoadshowState(intervalMs = 0) {
  const { identity } = useRoadshow()
  const [overview, setOverview] = useState<RoadshowOverview | null>(null)
  const [workflow, setWorkflow] = useState<WorkflowState | null>(null)
  const [loading, setLoading] = useState(true)
  const [initialized, setInitialized] = useState(false)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  const identityRef = useRef(identity)
  const refresh = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    const needLoading = !initialized || identityRef.current !== identity
    if (needLoading) setLoading(true)
    setError('')
    return startAbortableLoad(
      (signal) => Promise.all([
        roadshowGet<RoadshowOverview>('/overview', identity, signal),
        roadshowGet<WorkflowState>('/workflow', identity, signal),
      ]),
      {
        onSuccess: ([nextOverview, nextWorkflow]) => {
          setOverview(nextOverview)
          setWorkflow(nextWorkflow)
          setError('')
        },
        onError: (reason) => {
          setError(reason instanceof Error ? reason.message : '加载失败')
        },
        onSettled: () => {
          setLoading(false)
          setInitialized(true)
          identityRef.current = identity
        },
      },
    )
  }, [identity, nonce])

  useEffect(() => {
    if (!intervalMs) return
    const timer = window.setInterval(refresh, intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs, refresh])

  return { overview, workflow, loading, error, refresh }
}

type RoleBusinessSummary = {
  applications: number
  contracts: number
  executions: number
  results: number
  approvalQueues?: {
    applications: number
    dataProducts: number
    modelProducts: number
  }
}

function useRoleBusinessSummary() {
  const { identity } = useRoadshow()
  const [summary, setSummary] = useState<RoleBusinessSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  const identityRef = useRef(identity)
  const refresh = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    if (identityRef.current !== identity) {
      setSummary(null)
      setLoading(true)
    }
    setError('')
    const applicationPath = identity === 'data_requester'
      ? '/application-management'
      : '/application-review-queue'
    return startAbortableLoad(
      (signal) => Promise.all([
        platformGet<{ total: number }>(applicationPath, identity, signal),
        platformGet<{ total: number }>('/digital-contracts', identity, signal),
        platformGet<{ total: number }>('/execution-readiness', identity, signal),
        platformGet<{ total: number }>('/result-artifacts', identity, signal),
        identity === 'space_operator'
          ? platformGet<{ items: unknown[] }>('/data-product-review-queue', identity, signal)
          : Promise.resolve({ items: [] }),
        identity === 'space_operator'
          ? platformGet<{ items: unknown[] }>('/model-product-review-queue', identity, signal)
          : Promise.resolve({ items: [] }),
      ]),
      {
        onSuccess: ([applications, contracts, executions, results, dataProductReviews, modelProductReviews]) => {
          setSummary({
            applications: applications.total,
            contracts: contracts.total,
            executions: executions.total,
            results: results.total,
            approvalQueues: identity === 'space_operator'
              ? {
                applications: applications.total,
                dataProducts: dataProductReviews.items.length,
                modelProducts: modelProductReviews.items.length,
              }
              : undefined,
          })
          setError('')
        },
        onError: (reason) => {
          setError(reason instanceof Error ? reason.message : '业务汇总加载失败')
        },
        onSettled: () => {
          setLoading(false)
          identityRef.current = identity
        },
      },
    )
  }, [identity, nonce])

  return { summary, loading, error, refresh }
}

function PageBoundary({ loading, error, children }: { loading: boolean; error: string; children: ReactNode }) {
  if (loading) return <Card className="content-card"><Flex justify="center" align="center" style={{ minHeight: 260 }}><Spin size="large" description="正在读取平台状态" /></Flex></Card>
  if (error) return <Alert type="error" showIcon title="无法读取平台状态" description={error} />
  return <>{children}</>
}

function Hero({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <div className="phase4-page-hero"><div><span>{eyebrow}</span><Title level={2}>{title}</Title><Paragraph>{description}</Paragraph></div>{action}</div>
}

type ActionSpec = { key: string; label: string; endpoint?: string; navigateTo?: string; role: DemoIdentity; tone?: 'primary' | 'default' }

function signedFor(workflow: WorkflowState, identity: DemoIdentity) {
  const role = identity === 'space_operator' ? 'operator_witness' : identity
  return workflow.signatures.some((item) => item.party_role === role)
}

function readyFor(workflow: WorkflowState, type: string) {
  return workflow.readiness.some((item) => item.type === type)
}

function pendingReview(workflow: WorkflowState, type: string) {
  return workflow.reviews.some((item) => item.type === type && ['pending', 'claimed'].includes(item.status))
}

function pendingArtifactReview(workflow: WorkflowState, type: string) {
  return workflow.artifact_reviews.some((item) => item.type === type && ['pending', 'claimed'].includes(item.status))
}

function nextActions(identity: DemoIdentity, overview: RoadshowOverview, workflow: WorkflowState): ActionSpec[] {
  const actions: ActionSpec[] = []
  if (identity === 'data_provider') {
    if (overview.data_listing === 'draft') actions.push({ key: 'data-submit', label: '提交数据产品上架', endpoint: '/data-listing/submit', role: identity, tone: 'primary' })
    if (pendingReview(workflow, 'data_provider_review')) actions.push({ key: 'demand-data-approve', label: '批准本次数据使用', endpoint: '/reviews/data-provider/approve', role: identity, tone: 'primary' })
    if (workflow.contract?.status === 'proposed' && !signedFor(workflow, identity)) actions.push({ key: 'sign-data', label: '核对并确认数据许可条款', navigateTo: `/contracts/${workflow.contract.id}`, role: identity, tone: 'primary' })
    if (workflow.contract?.status === 'active' && !readyFor(workflow, 'data_ready')) actions.push({ key: 'data-ready', label: '进入执行准备', navigateTo: `/execution/${workflow.contract.id}`, role: identity, tone: 'primary' })
    if (pendingArtifactReview(workflow, 'data_provider_egress_review')) actions.push({ key: 'artifact-data-approve', label: '批准结果数据出域', endpoint: '/artifact-reviews/data-provider/approve', role: identity, tone: 'primary' })
  }
  if (identity === 'model_provider') {
    if (overview.model_listing === 'draft') actions.push({ key: 'model-submit', label: '提交模型产品上架', endpoint: '/model-listing/submit', role: identity, tone: 'primary' })
    if (pendingReview(workflow, 'model_provider_review')) actions.push({ key: 'demand-model-approve', label: '批准本次模型使用', endpoint: '/reviews/model-provider/approve', role: identity, tone: 'primary' })
    if (workflow.contract?.status === 'proposed' && !signedFor(workflow, identity)) actions.push({ key: 'sign-model', label: '核对并确认模型许可条款', navigateTo: `/contracts/${workflow.contract.id}`, role: identity, tone: 'primary' })
    if (workflow.contract?.status === 'active' && !readyFor(workflow, 'model_ready')) actions.push({ key: 'model-ready', label: '进入执行准备', navigateTo: `/execution/${workflow.contract.id}`, role: identity, tone: 'primary' })
    if (pendingArtifactReview(workflow, 'model_provider_quality_review')) actions.push({ key: 'artifact-model-approve', label: '完成模型技术质量确认', endpoint: '/artifact-reviews/model-provider/approve', role: identity })
  }
  if (identity === 'space_operator') {
    if (overview.data_listing === 'under_review') actions.push({ key: 'data-approve', label: '批准数据产品上架', endpoint: '/data-listing/approve', role: identity, tone: 'primary' })
    if (overview.model_listing === 'under_review') actions.push({ key: 'model-approve', label: '批准模型产品上架', endpoint: '/model-listing/approve', role: identity, tone: 'primary' })
    if (pendingReview(workflow, 'application_precheck')) actions.push({ key: 'demand-precheck', label: '通过计算需求预审', endpoint: '/reviews/platform-precheck/approve', role: identity, tone: 'primary' })
    if (overview.application === 'approved' && !workflow.contract && workflow.application) actions.push({ key: 'contract-create', label: '进入合约编排', navigateTo: `/applications/${workflow.application.id}`, role: identity, tone: 'primary' })
    if (workflow.contract?.status === 'proposed' && !signedFor(workflow, identity)) actions.push({ key: 'sign-operator', label: '核对并完成平台见证', navigateTo: `/contracts/${workflow.contract.id}`, role: identity, tone: 'primary' })
    if (workflow.contract?.status === 'signed') actions.push({ key: 'contract-activate', label: '核验并激活数字合约', navigateTo: `/contracts/${workflow.contract.id}`, role: identity, tone: 'primary' })
    if (workflow.contract?.status === 'active' && !readyFor(workflow, 'platform_ready')) actions.push({ key: 'platform-ready', label: '进入执行准备', navigateTo: `/execution/${workflow.contract.id}`, role: identity, tone: 'primary' })
    if (workflow.artifact && workflow.artifact_reviews.length === 0) actions.push({ key: 'artifact-plan', label: '生成多方结果审核计划', endpoint: '/artifacts/review-plan', role: identity, tone: 'primary' })
    if (pendingArtifactReview(workflow, 'platform_compliance_review')) actions.push({ key: 'artifact-platform-approve', label: '通过平台合规审核', endpoint: '/artifact-reviews/platform/approve', role: identity, tone: 'primary' })
    const requiredDone = workflow.artifact_reviews.filter((item) => item.required).length > 0 && workflow.artifact_reviews.filter((item) => item.required).every((item) => item.status === 'decided')
    if (requiredDone && !workflow.result_package) actions.push({ key: 'result-package', label: '生成已审批结果包', endpoint: '/result-packages', role: identity, tone: 'primary' })
  }
  if (identity === 'data_requester') {
    if (overview.data_listing === 'published' && overview.model_listing === 'published' && !workflow.application) actions.push({ key: 'demand-submit', label: '提交计算需求', endpoint: '/demands/submit', role: identity, tone: 'primary' })
    if (workflow.contract?.status === 'proposed' && !signedFor(workflow, identity)) actions.push({ key: 'sign-requester', label: '核对并确认数据使用条款', navigateTo: `/contracts/${workflow.contract.id}`, role: identity, tone: 'primary' })
    if (overview.execution_ready && !workflow.run && workflow.contract) actions.push({ key: 'compute-run', label: '进入执行准备', navigateTo: `/execution/${workflow.contract.id}`, role: identity, tone: 'primary' })
  }
  return actions
}

function ActionPanel({ overview, workflow, onChanged }: { overview: RoadshowOverview; workflow: WorkflowState; onChanged: () => void }) {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [api, holder] = message.useMessage()
  const writeGuard = useRef(createSingleFlight()).current
  const actions = nextActions(identity, overview, workflow)
  const execute = async (action: ActionSpec) => {
    if (action.navigateTo) {
      navigate(action.navigateTo)
      return
    }
    if (!action.endpoint) return
    await writeGuard.run(async () => {
      setBusy(action.key); setError('')
      try {
        await roadshowCommand(action.endpoint, identity, `phase4-ui:${action.key}:v1`)
        api.success(`${action.label}已完成`)
        onChanged()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '命令执行失败')
      } finally { setBusy('') }
    })
  }
  return <Card className="content-card phase4-action-card" title="当前协作链下一步" extra={<Tag color={actions.length ? 'blue' : 'green'}>{actions.length ? `${actions.length} 个下一步` : '暂无下一步'}</Tag>}>
    {holder}{error && <Alert type="error" showIcon title="操作未完成" description={error} closable onClose={() => setError('')} />}
    {actions.length ? <Space wrap>{actions.map((action) => <Button key={action.key} type={action.tone || 'default'} icon={<SendOutlined />} loading={busy === action.key} disabled={Boolean(busy)} onClick={() => execute(action)}>{action.label}</Button>)}</Space> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有需要你处理的事项" />}
  </Card>
}

const roleCopy: Record<DemoIdentity, { title: string; lead: string; applicationTitle: string }> = {
  space_operator: { title: '空间运营工作台', lead: '集中处理平台审核、合约、执行与结果事项。', applicationTitle: '待办审核' },
  data_provider: { title: '医院工作台', lead: '查看数据使用申请、合约确认、执行准备与结果审核。', applicationTitle: '数据使用审批' },
  model_provider: { title: '模型服务工作台', lead: '查看模型使用申请、合约确认、执行准备与质量审核。', applicationTitle: '模型使用审批' },
  data_requester: { title: '研究需求工作台', lead: '从需求申请开始，跟进合约、执行与结果交付。', applicationTitle: '我的计算需求' },
}

export function RoadshowOverviewPage() {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const state = useRoadshowState()
  const business = useRoleBusinessSummary()
  const copy = roleCopy[identity]
  const refresh = useCallback(() => {
    state.refresh()
    business.refresh()
  }, [business.refresh, state.refresh])
  return <div className="page-stack">
    <Hero eyebrow={roleProfiles[identity].label} title={copy.title} description={copy.lead} action={<Button icon={<ReloadOutlined />} onClick={refresh}>刷新状态</Button>} />
    <PageBoundary loading={state.loading} error={state.error}>{state.overview && state.workflow && (() => {
      const overview = state.overview
      const workflow = state.workflow
      const approvalQueues = business.summary?.approvalQueues
      const approvalTotal = approvalQueues
        ? approvalQueues.applications + approvalQueues.dataProducts + approvalQueues.modelProducts
        : undefined
      const approvalActions = identity === 'space_operator' ? [
        { key: 'applications', label: '服务申请', total: approvalQueues?.applications, path: '/applications' },
        { key: 'data-products', label: '数据上架', total: approvalQueues?.dataProducts, path: '/data-products' },
        { key: 'model-products', label: '模型上架', total: approvalQueues?.modelProducts, path: '/model-products' },
      ] : undefined
      const entries: Array<{
        key: string
        title: string
        icon: ReactNode
        detail: string
        total: number | undefined
        totalLabel: string
        path: string
        reviewActions?: Array<{ key: string; label: string; total: number | undefined; path: string }>
      }> = [
        {
          key: 'application', title: copy.applicationTitle, icon: <TeamOutlined />,
          detail: identity === 'data_requester'
            ? '管理需求、产品组合与审核进度'
            : identity === 'space_operator'
              ? '集中处理服务申请与数据、模型产品上架'
              : '进入待办列表处理当前审核',
          total: identity === 'space_operator' ? approvalTotal : business.summary?.applications,
          totalLabel: identity === 'data_requester' ? '项需求' : '项待处理',
          path: '/applications',
          reviewActions: approvalActions,
        },
        {
          key: 'contract', title: '数字合约', icon: <FileProtectOutlined />,
          detail: '查看本机构参与的合约与多方确认状态',
          total: business.summary?.contracts,
          totalLabel: '份合约',
          path: '/contracts',
        },
        {
          key: 'execution', title: identity === 'data_requester' ? '执行进度' : '执行准备', icon: <CodeSandboxOutlined />,
          detail: '跟进已生效合约的准备、任务与运行状态',
          total: business.summary?.executions,
          totalLabel: '项执行任务',
          path: '/execution',
        },
        {
          key: 'result', title: identity === 'data_requester' ? '结果下载' : '结果审核', icon: <SafetyCertificateOutlined />,
          detail: identity === 'data_requester' ? '查看已审批结果包与下载记录' : '处理隔离区结果与出域审核',
          total: business.summary?.results,
          totalLabel: identity === 'data_requester' ? '项结果' : '项待处理结果',
          path: '/results',
        },
      ]
      return <>
        <Title level={3} style={{ margin: 0 }}>业务概览</Title>
        <Row className="role-workbench-grid" gutter={[16, 16]}>
          {entries.map((entry) => <Col xs={24} md={12} xl={6} key={entry.key}>
            <Card className="content-card role-workbench-card" size="small" title={<Space>{entry.icon}<span>{entry.title}</span></Space>}>
              <Title level={3} style={{ margin: '0 0 8px' }}>
                {entry.total === undefined
                  ? business.loading ? '读取中' : business.error ? '—' : '0'
                  : entry.total}
                {entry.total !== undefined && <Text type="secondary" style={{ fontSize: 14, marginLeft: 6 }}>{entry.totalLabel}</Text>}
              </Title>
              <Paragraph ellipsis={{ rows: 2 }} style={{ minHeight: 44 }}>{entry.detail}</Paragraph>
              {entry.reviewActions && <Flex gap={8} wrap style={{ marginBottom: 10 }}>
                {entry.reviewActions.map((action) => <Button
                  key={action.key}
                  size="small"
                  onClick={() => navigate(action.path)}
                >{action.label} {action.total === undefined ? '—' : action.total}</Button>)}
              </Flex>}
              <Flex justify="space-between" align="center" gap={12}>
                <Text type="secondary">{entry.reviewActions ? '按类型进入对应审核队列' : '来自当前账号可见业务'}</Text>
                {!entry.reviewActions && <Button type="link" onClick={() => navigate(entry.path)}>打开</Button>}
              </Flex>
            </Card>
          </Col>)}
        </Row>
        <div className="role-workbench-next-action">
          <ActionPanel overview={overview} workflow={workflow} onChanged={refresh} />
        </div>
        <Collapse className="role-workbench-chain-details" ghost items={[{
          key: 'current-chain',
          label: '查看当前协作链详情',
          children: <WorkflowSummary workflow={workflow} />,
        }]} />
      </>
    })()}</PageBoundary>
  </div>
}

function WorkflowSummary({ workflow }: { workflow: WorkflowState }) {
  const steps = [
    { title: '产品上架', done: Boolean(workflow.application) },
    { title: '需求审核', done: workflow.application?.status === 'approved' },
    { title: '合同生效', done: workflow.contract?.status === 'active' },
    { title: '资产就绪', done: workflow.readiness.length >= 3 },
    { title: '受控运行', done: workflow.run?.status === 'succeeded' },
    { title: '结果审核', done: workflow.artifact_reviews.filter((item) => item.required).length >= 2 && workflow.artifact_reviews.filter((item) => item.required).every((item) => item.status === 'decided') },
    { title: '安全结果包', done: workflow.result_package?.status === 'available' || workflow.result_package?.status === 'approved' },
  ]
  const completed = steps.filter((item) => item.done).length
  return <Card className="content-card" size="small"><Progress percent={Math.round(completed / steps.length * 100)} showInfo={false} /><Steps size="small" current={Math.min(completed, steps.length - 1)} items={steps.map((item) => ({ title: item.title, status: item.done ? 'finish' : 'wait' }))} /></Card>
}

function CatalogPage({ kind }: { kind: 'data' | 'models' }) {
  const { identity } = useRoadshow()
  const [items, setItems] = useState<CatalogItem[]>([])
  const [loading, setLoading] = useState(true)
  const [initialized, setInitialized] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    setLoading(true)
    setError('')
    return startAbortableLoad(
      (signal) => roadshowGet<{ items: CatalogItem[] }>(`/catalog/${kind}`, identity, signal),
      {
        onSuccess: (value) => {
          setItems(value.items)
          setError('')
          setInitialized(true)
        },
        onError: (reason) => {
          setError(reason instanceof Error ? reason.message : '目录加载失败')
        },
        onSettled: () => setLoading(false),
      },
    )
  }, [identity, kind])
  const data = kind === 'data'
  return <div className="page-stack"><Hero eyebrow="可信目录只公开能力与元数据" title={data ? '数据产品目录' : '模型产品目录'} description={data ? '数据产品是可申请、可约束、可审计的服务单元，不是原始数据库或文件下载入口。' : '模型目录用于发现与申请；实际执行只认固定 registry entrypoint 与 digest。'} />
    <PageBoundary loading={loading} error={error}>{items.length ? <Row gutter={[18, 18]}>{items.map((item) => <Col xs={24} xl={12} key={item.id}><Card className="phase4-catalog-card" title={item.name} extra={item.published ? <Tag color="green">可申请</Tag> : statusTag(item.status)}>
      <Paragraph>{item.description}</Paragraph><Descriptions column={2} size="small">
        <Descriptions.Item label="版本">{item.version}</Descriptions.Item><Descriptions.Item label="状态">{statusTag(item.status)}</Descriptions.Item>
        {data ? <><Descriptions.Item label="数据模态">数字病理图像</Descriptions.Item><Descriptions.Item label="使用模式">受控计算</Descriptions.Item><Descriptions.Item label="公开范围">元数据、Schema、质量摘要</Descriptions.Item><Descriptions.Item label="来源">公开 PathMNIST 验证资产</Descriptions.Item></> : <><Descriptions.Item label="任务">9 分类病理图像推理</Descriptions.Item><Descriptions.Item label="运行环境">CPU · 固定入口</Descriptions.Item><Descriptions.Item label="输入">28×28 RGB</Descriptions.Item><Descriptions.Item label="输出">聚合推理结果制品</Descriptions.Item></>}
      </Descriptions><div className="phase4-restrictions"><LockOutlined /> {item.restrictions?.map((value) => <Tag key={value}>{value}</Tag>)}</div>
    </Card></Col>)}</Row> : <Empty description="目录尚无已登记产品" />}</PageBoundary>
  </div>
}

export const RoadshowDataCatalogPage = () => <CatalogPage kind="data" />
export const RoadshowModelCatalogPage = () => <CatalogPage kind="models" />

const flowLabels: Array<[string, string]> = [
  ['医院登录并查看数据产品', 'data'], ['提交数据产品上架', 'data'], ['平台批准数据产品', 'data'],
  ['模型企业登录并查看模型产品', 'model'], ['提交模型产品上架', 'model'], ['平台批准模型产品', 'model'],
  ['需求企业选择数据与模型', 'demand'], ['提交计算需求', 'demand'], ['平台需求预审', 'review'],
  ['医院审批数据使用', 'review'], ['模型企业审批模型使用', 'review'], ['生成数字合约', 'contract'],
  ['数据提供方签署', 'contract'], ['模型提供方签署', 'contract'], ['需求企业签署', 'contract'],
  ['平台见证签署并激活', 'contract'], ['医院确认数据就绪', 'ready'], ['模型企业确认模型就绪', 'ready'],
  ['平台确认执行条件', 'ready'], ['需求企业创建任务', 'run'], ['任务 reserved → dispatched → running → succeeded', 'run'],
  ['Artifact 进入 quarantined', 'artifact'], ['医院结果出域审核', 'result'], ['模型企业技术质量确认', 'result'],
  ['平台合规审核', 'result'], ['生成已审批结果包', 'package'], ['需求企业受控下载', 'download'],
  ['展示完整 Audit 时间线', 'audit'], ['确认无原始数据或模型权重入口', 'audit'],
]

function phaseDone(key: string, overview: RoadshowOverview, workflow: WorkflowState) {
  if (key === 'data') return overview.data_listing === 'published'
  if (key === 'model') return overview.model_listing === 'published'
  if (key === 'demand') return Boolean(workflow.application)
  if (key === 'review') return workflow.application?.status === 'approved'
  if (key === 'contract') return workflow.contract?.status === 'active'
  if (key === 'ready') return workflow.readiness.length >= 3
  if (key === 'run') return workflow.run?.status === 'succeeded'
  if (key === 'artifact') return Boolean(workflow.artifact)
  if (key === 'result') return workflow.artifact_reviews.filter((item) => item.required).length >= 2 && workflow.artifact_reviews.filter((item) => item.required).every((item) => item.status === 'decided')
  if (key === 'package') return Boolean(workflow.result_package)
  if (key === 'download') return false
  if (key === 'audit') return workflow.audit.length > 0
  return false
}

export function RoadshowWorkflowPage() {
  const state = useRoadshowState()
  return <div className="page-stack"><Hero eyebrow="全流程状态协同" title="多主体协作流程" description="每项操作按当前角色提交，流程状态由平台统一记录。" action={<Button icon={<ReloadOutlined />} onClick={state.refresh}>刷新</Button>} />
    <PageBoundary loading={state.loading} error={state.error}>{state.overview && state.workflow && <>
      <ActionPanel overview={state.overview} workflow={state.workflow} onChanged={state.refresh} />
      <Card className="content-card" title="协作全流程"><div className="phase4-roadmap">{flowLabels.map(([label, key], index) => {
        const done = phaseDone(key, state.overview!, state.workflow!)
        return <div key={`${label}-${index}`} className={done ? 'is-done' : ''}><span>{done ? <CheckCircleOutlined /> : index + 1}</span><strong>{label}</strong></div>
      })}</div></Card>
      <Row gutter={[16, 16]}><Col xs={24} xl={12}><Card className="content-card" title="申请审核"><List dataSource={state.workflow.reviews} locale={{ emptyText: '计算需求尚未提交' }} renderItem={(item) => <List.Item extra={statusTag(item.status)}><span>{item.type}{item.mine && <Tag color="blue" style={{ marginLeft: 8 }}>我的待办</Tag>}</span></List.Item>} /></Card></Col>
      <Col xs={24} xl={12}><Card className="content-card" title="执行资产就绪"><List dataSource={['data_ready', 'model_ready', 'platform_ready']} renderItem={(item) => <List.Item extra={statusTag(readyFor(state.workflow!, item))}>{item === 'data_ready' ? '医院数据范围已在提供方节点锁定' : item === 'model_ready' ? '固定模型版本已在执行注册中心就绪' : '平台 Connector、能力与摘要检查'}</List.Item>} /></Card></Col></Row>
    </>}</PageBoundary>
  </div>
}

export function RoadshowContractPage() {
  const state = useRoadshowState()
  return <div className="page-stack"><Hero eyebrow="申请范围只能收窄，不能扩大" title="机器可执行数字合约" description="合同固定四方主体、数据版本、模型版本、动作、输出、次数、环境与多方结果审核计划。" />
    <PageBoundary loading={state.loading} error={state.error}>{state.workflow && (state.workflow.contract ? <>
      <Card className="content-card"><Descriptions bordered column={2}><Descriptions.Item label="合同编号">{state.workflow.contract.number}</Descriptions.Item><Descriptions.Item label="Revision状态">{statusTag(state.workflow.contract.status)}</Descriptions.Item><Descriptions.Item label="内容摘要" span={2}><Text copyable code>{state.workflow.contract.content_digest}</Text></Descriptions.Item><Descriptions.Item label="固定数据">PathMNIST 数据产品 v1.0</Descriptions.Item><Descriptions.Item label="固定模型">PathMNIST ResNet-18 v1.0</Descriptions.Item><Descriptions.Item label="允许操作">model_validation · 1次 · 30天</Descriptions.Item><Descriptions.Item label="执行环境">指定受控计算节点</Descriptions.Item><Descriptions.Item label="允许结果">聚合指标、混淆矩阵、执行摘要、批准报告</Descriptions.Item><Descriptions.Item label="明确禁止">原始图像、患者级结果、特征、模型权重</Descriptions.Item></Descriptions></Card>
      <Card className="content-card" title="四方签署证据"><Row gutter={[12, 12]}>{(['data_provider', 'model_provider', 'data_requester', 'operator_witness'] as const).map((role) => { const found = state.workflow!.signatures.find((item) => item.party_role === role); return <Col xs={24} md={12} key={role}><div className={`phase4-signature ${found ? 'is-signed' : ''}`}><SafetyCertificateOutlined /><div><strong>{role === 'data_provider' ? '医院数据提供方' : role === 'model_provider' ? 'AI模型提供方' : role === 'data_requester' ? '需求企业' : '空间运营见证方'}</strong><span>{found ? `已签署 · ${new Date(found.signed_at).toLocaleString()}` : '待签署'}</span></div></div></Col> })}</Row></Card>
    </> : <Empty description="需求审核全部通过后，才允许生成合约草案" />)}</PageBoundary>
  </div>
}

export function RoadshowExecutionPage() {
  const state = useRoadshowState()
  const run = state.workflow?.run
  const step = run ? ({ reserved: 0, dispatched: 1, running: 2, succeeded: 3, failed: 3, interrupted: 3 }[run.status] ?? 0) : 0
  return <div className="page-stack"><Hero eyebrow="合约约束下的受控计算" title="受控执行中心" description="平台仅执行已生效数字合约中确定的数据、模型、规则与输出。" action={<Button icon={<ReloadOutlined />} onClick={state.refresh}>刷新运行状态</Button>} />
    <PageBoundary loading={state.loading} error={state.error}>{state.overview && state.workflow && <>
      <ActionPanel overview={state.overview} workflow={state.workflow} onChanged={state.refresh} />
      <Card className="content-card" title="执行状态">{run ? <><Steps current={step} status={['failed', 'interrupted'].includes(run.status) ? 'error' : 'process'} items={[{ title: '次数原子预留' }, { title: 'Coordinator投递' }, { title: '固定模型CPU推理' }, { title: '隔离制品' }]} /><Descriptions size="small" column={3} style={{ marginTop: 28 }}><Descriptions.Item label="Run">{run.id}</Descriptions.Item><Descriptions.Item label="状态">{statusTag(run.status)}</Descriptions.Item><Descriptions.Item label="运行序号">{run.ordinal} / 1</Descriptions.Item></Descriptions></> : <Empty description={state.overview.execution_ready ? '执行条件已满足，需求企业可以创建任务' : '合同和三方就绪尚未完成'} />}</Card>
    </>}</PageBoundary>
  </div>
}

export function RoadshowResultsPage() {
  const { identity } = useRoadshow()
  const state = useRoadshowState()
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')
  const [api, holder] = message.useMessage()
  const downloadGuard = useRef(createSingleFlight()).current
  const download = async () => {
    if (!state.workflow?.result_package) return
    await downloadGuard.run(async () => {
      setDownloading(true); setError('')
      try {
        const grant = await roadshowCommand<{ token: string }>(`/result-packages/${state.workflow!.result_package!.id}/download-grants`, identity, `phase4-ui:grant:${secureUuid()}`)
        const blob = await roadshowDownload(identity, grant.token, `phase4-ui:download:${secureUuid()}`)
        const href = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = href; anchor.download = 'medtrust-approved-result.zip'; anchor.click(); URL.revokeObjectURL(href)
        api.success('已完成一次受控下载，授权令牌已消耗')
        state.refresh()
      } catch (reason) { setError(reason instanceof Error ? reason.message : '下载失败') } finally { setDownloading(false) }
    })
  }
  return <div className="page-stack">{holder}<Hero eyebrow="审核通过不等于自动发布" title="多方结果审核与安全结果包" description="医院数据出域审核和平台合规审核为强制环节；模型方技术确认不能替代二者。" />
    <PageBoundary loading={state.loading} error={state.error}>{state.overview && state.workflow && <>
      <ActionPanel overview={state.overview} workflow={state.workflow} onChanged={state.refresh} />
      {error && <Alert type="error" showIcon title="受控下载未完成" description={error} />}
      <Row gutter={[16, 16]}><Col xs={24} xl={14}><Card className="content-card" title="隔离制品与审核">
        {state.workflow.artifact ? <><Alert type="warning" showIcon title={`Artifact：${stateLabel[state.workflow.artifact.status] || state.workflow.artifact.status}`} description="需求企业在强制审核完成前看不到结果内容或下载按钮。" /><List dataSource={state.workflow.artifact_reviews} renderItem={(item) => <List.Item extra={statusTag(item.status)}><div><strong>{item.type === 'data_provider_egress_review' ? '医院数据出域审核' : item.type === 'platform_compliance_review' ? '平台合规审核' : '模型技术质量确认'}</strong><div><Text type="secondary">{item.required ? '强制审核' : '条件性技术确认'}{item.mine ? ' · 当前身份负责' : ''}</Text></div></div></List.Item>} /></> : <Empty description="受控运行完成后才会产生隔离制品" />}
      </Card></Col><Col xs={24} xl={10}><Card className="content-card" title="已审批结果包">
        {state.workflow.result_package ? <><Alert type="success" showIcon title="结果包已生成" description="只包含合约白名单中的处理后文件。" /><List size="small" dataSource={state.workflow.result_package.files} renderItem={(item) => <List.Item><FileProtectOutlined /> {typeof item === 'string' ? item : item.name || item.path || 'approved file'}</List.Item>} />{identity === 'data_requester' && <Button block type="primary" icon={<CloudDownloadOutlined />} loading={downloading} onClick={download}>下载已审批结果包</Button>}</> : <Empty description="强制审核全部通过后由平台生成" />}
      </Card></Col></Row>
      <Alert type="info" showIcon title="结果包永不包含" description="原始图像、患者级结果、原始特征、模型权重、执行脚本、内部路径、Connector凭据或访问令牌。" />
    </>}</PageBoundary>
  </div>
}

export function RoadshowAuditPage() {
  const { identity } = useRoadshow()
  const state = useRoadshowState()
  const [searchParams] = useSearchParams()
  const subjectType = searchParams.get('subjectType')
  const subjectId = searchParams.get('subjectId')
  const productFilterActive = ['data_product_version', 'model_version'].includes(subjectType || '') && Boolean(subjectId)
  const [filteredAudit, setFilteredAudit] = useState<{
    items: Array<{
      event_id: string
      event_type: string
      result: string
      occurred_at: string
      actor: string
      state_before: string | null
      state_after: string | null
    }>
    audit_chain_valid: boolean
    total: number
  } | null>(null)
  const [filterLoading, setFilterLoading] = useState(false)
  const [filterError, setFilterError] = useState('')
  const [infra, setInfra] = useState<Record<string, unknown> | null>(null)
  useEffect(() => {
    if (!productFilterActive || !subjectId) {
      setFilteredAudit(null)
      setFilterError('')
      setFilterLoading(false)
      return
    }
    setFilterLoading(true)
    setFilterError('')
    return startAbortableLoad(
      (signal) => platformGet<NonNullable<typeof filteredAudit>>(
        `${subjectType === 'model_version' ? '/model-product-versions' : '/data-product-versions'}/${encodeURIComponent(subjectId)}/audit-events?limit=100`,
        identity,
        signal,
      ),
      {
        onSuccess: (value) => setFilteredAudit(value),
        onError: (reason) => setFilterError(reason instanceof Error ? reason.message : '审计过滤失败'),
        onSettled: () => setFilterLoading(false),
      },
    )
  }, [identity, productFilterActive, subjectId])
  useEffect(() => {
    if (identity !== 'space_operator') { setInfra(null); return }
    roadshowGet<Record<string, unknown>>('/infrastructure', identity).then(setInfra).catch(() => setInfra(null))
  }, [identity])
  const timelineItems = productFilterActive
    ? (filteredAudit?.items || []).map((event) => ({
      color: event.result === 'success' ? 'green' : 'red',
      content: <div>
        <strong>{event.event_type}</strong>
        <div><Text type="secondary">{new Date(event.occurred_at).toLocaleString()} · {event.actor} · {event.state_before || '无'} → {event.state_after || '无'}</Text></div>
      </div>,
    }))
    : (state.workflow?.audit || []).map((event) => ({
      color: event.result === 'success' ? 'green' : 'red',
      content: <div><strong>#{event.sequence} · {event.type}</strong><div><Text type="secondary">{new Date(event.occurred_at).toLocaleString()} · {event.result}</Text></div></div>,
    }))
  return <div className="page-stack"><Hero eyebrow="业务事实与投递机制分离" title="审计与可靠消息基础设施" description="AuditEvent 是不可变业务证据；Outbox、Consumer Inbox 与 Callback Inbox 保证至少一次投递与幂等消费。" />
    <PageBoundary loading={state.loading || filterLoading} error={state.error || filterError}>{state.workflow && <>
      {productFilterActive && <Alert type={filteredAudit?.audit_chain_valid ? 'success' : 'warning'} showIcon title={`已按${subjectType === 'model_version' ? '模型' : '数据'}产品版本过滤，共 ${filteredAudit?.total || 0} 条事件`} description={`对象 ID：${subjectId}；空间审计链${filteredAudit?.audit_chain_valid ? '验证有效' : '验证失败或尚未完成验证'}。`} />}
      <Row gutter={[16, 16]}><Col xs={24} xl={16}><Card className="content-card" title={productFilterActive ? '当前产品完整审计链' : '最近审计时间线'}>{timelineItems.length ? <Timeline items={timelineItems} /> : <Empty description="没有匹配的审计事件" />}</Card></Col>
      <Col xs={24} xl={8}><Card className="content-card" title="基础设施健康">{infra ? <Descriptions column={1} bordered size="small">{Object.entries((infra.counts || {}) as Record<string, number>).map(([key, value]) => <Descriptions.Item key={key} label={key}>{value}</Descriptions.Item>)}<Descriptions.Item label="投递语义">至少一次 + 幂等消费者</Descriptions.Item></Descriptions> : <Alert type="info" showIcon title="仅空间运营方可查看基础设施计数" />}</Card></Col></Row>
      <Alert type="warning" showIcon title="哈希链能力边界" description="数据库内哈希链提供篡改检测线索，不等同于第三方可信存证或法律意义上的绝对不可篡改。" />
    </>}</PageBoundary>
  </div>
}
