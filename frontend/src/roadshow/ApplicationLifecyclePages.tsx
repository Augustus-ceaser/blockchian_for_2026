import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  EditOutlined,
  EyeOutlined,
  FileAddOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  SaveOutlined,
  SendOutlined,
  SyncOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { secureUuid } from '../lib/secureUuid'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  message,
  Progress,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Steps,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { platformCommand, platformGet } from './api'
import { createSingleFlight, startAbortableLoad } from './requestLifecycle'
import { roleProfiles, useRoadshow } from './RoadshowContext'
import {
  selectVisibleRecommendations,
  type DemandAssistantHandoff,
} from './demandAssistant'
import { ServiceAccessRequestsPanel } from './ServiceAccessRequests'
import {
  CommercialComputeQuotePreview,
  CommercialOrdersPanel,
  CommercialProviderSettlementPanel,
} from './CommercialCheckoutPage'

const { Paragraph, Text, Title } = Typography

type OptionItem = {
  product_id: string
  version_id: string
  product_code: string
  name: string
  provider: string
  provider_organization_id: string
  disease_domain: string
  modality: string
  version: string
  is_demo: boolean
  policy: Record<string, unknown>
  scale?: Record<string, unknown>
  quality?: Record<string, unknown>
  task_type?: string
  input_schema?: Record<string, unknown>
  output_schema?: Record<string, unknown>
  license?: Record<string, unknown>
  non_clinical?: boolean
}

type ApplicationOptions = {
  data_products: OptionItem[]
  model_products: OptionItem[]
  sample: { data_version_id: string | null; model_version_id: string | null }
}

type CompatibilityCheck = {
  code: string
  name: string
  result: 'PASS' | 'WARNING' | 'BLOCKER'
  data_requirement: unknown
  model_requirement: unknown
  request_value: unknown
  explanation: string
  remediation: string
}

type CompatibilityReport = {
  schema_version: string
  ruleset_version: string
  checked_at: string
  input_digest: string
  overall: 'PASS' | 'WARNING' | 'BLOCKER'
  counts: { pass: number; warning: number; blocker: number }
  blockers: string[]
  warnings: string[]
  checks: CompatibilityCheck[]
  disclaimer: string
  event_id?: string
}

type ReviewItem = {
  task_id: string
  review_type: string
  sequence_no: number
  status: string
  organization: string
  decision: null | {
    decision: string
    reason_code: string | null
    comment: string
    remediation: string | null
    evidence: Record<string, unknown>
    decided_at: string
  }
}

type ApplicationDetail = {
  application_id: string
  application_number: string
  demand_name: string
  status: string
  row_version: number
  created_at: string
  updated_at: string
  submitted_at: string | null
  decided_at: string | null
  decision_summary: string | null
  is_demo: boolean
  applicant: { id: string; name: string }
  data_provider: { id: string; name: string }
  model_provider: { id: string; name: string }
  data_product: {
    product_id: string
    version_id: string
    name: string
    version: string
    snapshot_digest: string
    policy_digest: string
  }
  model_product: {
    product_id: string
    version_id: string
    name: string
    version: string
    snapshot_digest: string
    policy_digest: string
    registry_digest: string
  }
  request: ApplicationDraft
  client_selection_snapshot_receipt: null | {
    schema_version: string
    received_at: string
    snapshot_digest: string
    verification_status: 'not_platform_verified'
    authority: 'receipt_only'
    eligibility_authority: 'server_compatibility_report'
  }
  compatibility: CompatibilityReport | null
  warning_acknowledged: boolean
  snapshot: null | { id: string; digest: string; captured_at: string }
  reviews: ReviewItem[]
  review_progress: { completed: number; total: number; current: string | null }
  contract: null | { id: string; number: string }
  next_step: string
  allowed_actions: string[]
  capability: {
    hard_isolation: boolean
    raw_data_download: boolean
    model_download: boolean
    compute_job_creation: boolean
    clinical_use: boolean
  }
}

type AuditItem = {
  event_id: string
  sequence: number
  event_type: string
  result: string
  occurred_at: string
  actor: string
  organization: string | null
  subject_type: string
  subject_id: string
  state_before: string | null
  state_after: string | null
  review_task_id: string | null
  compatibility_input_digest: string | null
  correlation_id: string
  previous_hash: string | null
  current_hash: string
  evidence_digest: string
  outbox: Array<{ message_id: string; destination: string; status: string }>
}

type ApplicationDraft = {
  schema_version: 'phase5.3/application-request/v1'
  data_version_id: string
  model_version_id: string
  profile: {
    demand_name: string
    project_type: string
    project_summary: string
    project_lead: string
    contact: string
    is_demo: boolean
    purpose_code: string
    research_purpose: string
    use_background: string
    expected_value: string
    clinical_diagnosis: boolean
    research_publication: boolean
    commercial_validation: boolean
    ethics_or_approval_statement: string
    project_reference: string
    data_minimization: string
  }
  data_scope: {
    scope_type: string
    subset_description: string
    sample_count: number
    selection_criteria: string
  }
  execution: {
    run_count: number
    valid_days: number
    environment_requirements: string
    internet_required: boolean
    fixed_data_version: boolean
    fixed_model_version: boolean
    requested_outputs: string[]
  }
  review_requirements: {
    hospital_egress_review: boolean
    model_technical_confirmation: boolean
    result_review_notes: string
    output_recipient: string
  }
  declarations: {
    no_raw_data_download: boolean
    no_model_weight_download: boolean
    approved_purpose_only: boolean
    accept_multiparty_review: boolean
    accept_result_isolation: boolean
    accept_full_audit: boolean
  }
  recommendation_context?: ApplicationRecommendationContext | null
}

type ApplicationRecommendationContext = {
  schema_version?: 'phase5.14/client-selection-snapshot/v1'
  evidence_kind?: 'client_selection_snapshot'
  verification_status?: 'client_asserted_unverified'
  authority?: 'client_assertion_only'
  source: 'role_assistant'
  selected_by_user: true
  selected_pair_key: string
  data_version_id: string
  model_version_id: string
  rank: number
  score: number
  score_max: number
  ruleset_version: string
  pair_schema_version: string
  stage: 'catalog_only' | 'static_candidate' | 'application_candidate' | 'execution_ready' | 'verified_pair'
  hard_gate_status: 'pass' | 'hold' | 'fail'
  reasons: string[]
  limitations: string[]
}

type ApplicationNavigationState = {
  demandAssistant?: DemandAssistantHandoff
  productSelection?: {
    dataVersionId?: string
    modelVersionId?: string
  }
}

const statusLabels: Record<string, string> = {
  draft: '草稿',
  submitted: '已提交',
  prechecking: '平台预审',
  provider_review: '多方审核',
  approved: '已批准',
  rejected: '已退回或拒绝',
  withdrawn: '已撤回',
}

const reviewLabels: Record<string, string> = {
  application_precheck: '平台预审',
  data_provider_review: '医院数据使用审核',
  model_provider_review: '模型使用审核',
}

const eventLabels: Record<string, string> = {
  'application.created': '计算需求草稿创建',
  'application.updated': '计算需求草稿更新',
  'application.compatibility.checked': '服务端兼容性检查完成',
  'application.submitted': '计算需求提交',
  'application.review.decided': '多方审核决定',
  'application.returned': '计算需求退回补充',
  'application.rejected': '计算需求拒绝',
  'application.approved': '计算需求最终批准',
}

function statusTag(status: string) {
  const color = status === 'approved'
    ? 'green'
    : status === 'rejected'
      ? 'red'
      : status === 'draft'
        ? 'default'
        : 'gold'
  return <Tag color={color}>{statusLabels[status] || status}</Tag>
}

function checkTag(result: CompatibilityCheck['result']) {
  return <Tag color={result === 'PASS' ? 'green' : result === 'WARNING' ? 'gold' : 'red'}>{result}</Tag>
}

function PageLoad({ loading, error, children }: { loading: boolean; error: string; children: ReactNode }) {
  if (loading) return <Flex className="phase51-loading" justify="center" align="center"><Spin size="large" /></Flex>
  if (error) return <Alert type="error" showIcon title="页面加载失败" description={error} />
  return <>{children}</>
}

function PageTitle({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return <div className="phase51-heading">
    <div><Title level={2}>{title}</Title><Paragraph>{description}</Paragraph></div>
    {actions && <Space wrap>{actions}</Space>}
  </div>
}

function useLoad<T>(path: string | null) {
  const { identity } = useRoadshow()
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(Boolean(path))
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    if (!path) return
    setLoading(true)
    setError('')
    return startAbortableLoad(
      (signal) => platformGet<T>(path, identity, signal),
      {
        onSuccess: (value) => { setData(value); setError('') },
        onError: (reason) => setError(reason instanceof Error ? reason.message : '请求失败'),
        onSettled: () => setLoading(false),
      },
    )
  }, [identity, path, nonce])
  return { data, loading, error, refresh: () => setNonce((value) => value + 1) }
}

function defaultDraft(): ApplicationDraft {
  return {
    schema_version: 'phase5.3/application-request/v1',
    data_version_id: '',
    model_version_id: '',
    profile: {
      demand_name: '',
      project_type: 'model_external_validation',
      project_summary: '',
      project_lead: '',
      contact: '',
      is_demo: true,
      purpose_code: 'model_validation',
      research_purpose: '',
      use_background: '',
      expected_value: '',
      clinical_diagnosis: false,
      research_publication: false,
      commercial_validation: false,
      ethics_or_approval_statement: '',
      project_reference: '',
      data_minimization: '',
    },
    data_scope: {
      scope_type: 'all_approved_demo_data',
      subset_description: '',
      sample_count: 20,
      selection_criteria: '',
    },
    execution: {
      run_count: 1,
      valid_days: 30,
      environment_requirements: '固定模型、固定数据版本、CPU 白名单执行器',
      internet_required: false,
      fixed_data_version: true,
      fixed_model_version: true,
      requested_outputs: ['aggregate_metrics', 'confusion_matrix', 'execution_summary'],
    },
    review_requirements: {
      hospital_egress_review: true,
      model_technical_confirmation: true,
      result_review_notes: '聚合结果在任何出域前接受医院和平台审核。',
      output_recipient: '',
    },
    declarations: {
      no_raw_data_download: true,
      no_model_weight_download: true,
      approved_purpose_only: true,
      accept_multiparty_review: true,
      accept_result_isolation: true,
      accept_full_audit: true,
    },
  }
}

function sampleDraft(options: ApplicationOptions): ApplicationDraft {
  const value = defaultDraft()
  value.data_version_id = options.sample.data_version_id || ''
  value.model_version_id = options.sample.model_version_id || ''
  value.profile = {
    ...value.profile,
    demand_name: `PathMNIST 外部性能验证 ${new Date().toLocaleDateString()}`,
    project_summary: '使用已发布 PathMNIST 公开演示数据和固定 ResNet-18 模型，验证受控计算申请与多方审批流程。',
    project_lead: '演示项目负责人',
    contact: '研发验证部门',
    research_purpose: '验证固定非临床模型在公开演示数据范围内的聚合分类性能，不用于临床诊断。',
    use_background: '用于数字病理可信数据空间工程路演和申请前技术可行性验证。',
    expected_value: '形成可审计的兼容性、审批和版本锁定证据，为后续数字合约提供输入。',
    ethics_or_approval_statement: '仅使用公开演示数据，不涉及患者级信息或真实医院数据。',
    project_reference: `DEMO-${Date.now().toString().slice(-8)}`,
    data_minimization: '仅使用当前获批的固定 20 张公开演示图像范围和聚合输出。',
  }
  value.data_scope = {
    scope_type: 'all_approved_demo_data',
    subset_description: '当前已批准的 PathMNIST 固定公开演示范围。',
    sample_count: 20,
    selection_criteria: '使用已发布版本中冻结的演示范围，不选择患者级记录。',
  }
  value.review_requirements.output_recipient = '研发验证部门'
  return value
}

export function ApplicationManagementPage() {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const reviewer = identity !== 'data_requester'
  const path = reviewer ? '/application-review-queue' : '/application-management'
  const state = useLoad<{ items: Array<ApplicationDetail | { task_id: string; actionable: boolean; application: ApplicationDetail }>; total: number }>(path)
  const rows = (state.data?.items || []).map((item) => 'application' in item ? item.application : item)
  return <div className="page-stack">
    <PageTitle
      title={identity === 'data_requester' ? '我的申请' : `${roleProfiles[identity].shortLabel}服务申请待办`}
      description={identity === 'data_requester'
        ? '统一查看受控计算申请与数据、模型授权申请。'
        : '处理当前机构可见的受控计算与授权审核待办。'}
      actions={<>
        <Button icon={<ReloadOutlined />} onClick={state.refresh}>刷新</Button>
        {identity === 'data_requester' && <Button type="primary" icon={<FileAddOutlined />} onClick={() => navigate('/applications/new')}>新建计算需求</Button>}
      </>}
    />
    <section className="phase51-section">
      <Title level={4}>受控计算申请</Title>
    <PageLoad loading={state.loading} error={state.error}>
      {rows.length ? <Table
        rowKey="application_id"
        dataSource={rows}
        pagination={{ pageSize: 8 }}
        columns={[
          { title: '需求名称', dataIndex: 'demand_name', render: (value, item) => <div><strong>{value || '未命名需求'}</strong><Text type="secondary" className="phase51-code">{item.application_number}</Text></div> },
          { title: '数据产品', render: (_, item) => <span>{item.data_product.name}<Text type="secondary" className="phase51-code">{item.data_product.version}</Text></span> },
          { title: '模型产品', render: (_, item) => <span>{item.model_product.name}<Text type="secondary" className="phase51-code">{item.model_product.version}</Text></span> },
          { title: '状态', dataIndex: 'status', width: 120, render: statusTag },
          { title: '审批进度', width: 150, render: (_, item) => `${item.review_progress.completed}/${item.review_progress.total || 3}` },
          { title: '下一步', dataIndex: 'next_step', render: (value) => value === 'digital_contract' ? '进入数字合约' : value === 'complete_and_submit' ? '完善并提交' : '等待或处理审核' },
          { title: '操作', width: 110, render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => navigate(`/applications/${item.application_id}`)}>{reviewer ? '审核' : '查看'}</Button> },
        ]}
      /> : <Empty description={reviewer ? '当前没有计算需求待办' : '尚未创建计算需求'} />}
    </PageLoad>
    </section>
    <ServiceAccessRequestsPanel />
    <CommercialOrdersPanel />
    <CommercialProviderSettlementPanel />
  </div>
}

const wizardFields: Array<Array<Array<string>>> = [
  [['data_version_id']],
  [['model_version_id']],
  [
    ['profile', 'demand_name'], ['profile', 'project_type'], ['profile', 'project_summary'],
    ['profile', 'project_lead'], ['profile', 'contact'], ['profile', 'research_purpose'],
    ['profile', 'use_background'], ['profile', 'expected_value'],
    ['profile', 'ethics_or_approval_statement'], ['profile', 'data_minimization'],
    ['data_scope', 'scope_type'], ['data_scope', 'sample_count'],
    ['review_requirements', 'result_review_notes'], ['review_requirements', 'output_recipient'],
  ],
  [
    ['profile', 'purpose_code'],
    ['execution', 'run_count'],
    ['execution', 'valid_days'],
    ['execution', 'requested_outputs'],
  ],
]

function SelectionGrid({
  items,
  selected,
  onSelect,
  kind,
}: {
  items: OptionItem[]
  selected: string
  onSelect: (versionId: string) => void
  kind: 'data' | 'model'
}) {
  return <Radio.Group value={selected} onChange={(event) => onSelect(event.target.value)} className="phase53-selection">
    {items.map((item) => <Radio key={item.version_id} value={item.version_id} className="phase53-option">
      <div className="phase53-option-head"><strong>{item.name}</strong><Tag color="green">已发布 {item.version}</Tag></div>
      <Text type="secondary">{item.provider} · {item.disease_domain}</Text>
      <Descriptions column={2} size="small">
        <Descriptions.Item label="模态">{item.modality || '未登记'}</Descriptions.Item>
        <Descriptions.Item label={kind === 'data' ? '数据范围' : '模型任务'}>{kind === 'data' ? `${Number(item.scale?.image_count || 0)} 图像` : item.task_type || '图像分类'}</Descriptions.Item>
        <Descriptions.Item label="演示边界">{item.is_demo ? '公开工程演示' : '已登记产品'}</Descriptions.Item>
        <Descriptions.Item label="临床用途">{kind === 'model' && item.non_clinical ? '禁止' : '按策略审核'}</Descriptions.Item>
      </Descriptions>
    </Radio>)}
  </Radio.Group>
}

function CompatibilityPanel({ report }: { report: CompatibilityReport | null }) {
  if (!report) return <Empty description="尚未执行服务端兼容性检查" />
  return <div className="phase53-compatibility">
    <Alert
      type={report.overall === 'BLOCKER' ? 'error' : report.overall === 'WARNING' ? 'warning' : 'success'}
      showIcon
      title={`总体结果：${report.overall}`}
      description={`${report.counts.pass} PASS / ${report.counts.warning} WARNING / ${report.counts.blocker} BLOCKER`}
    />
    <Table
      rowKey="code"
      dataSource={report.checks}
      pagination={false}
      size="small"
      scroll={{ x: 640 }}
      columns={[
        { title: '检查项', dataIndex: 'name', width: 190 },
        { title: '结果', dataIndex: 'result', width: 100, render: checkTag },
        { title: '说明', dataIndex: 'explanation' },
        { title: '修复建议', dataIndex: 'remediation', render: (value) => value || '无需处理' },
      ]}
    />
  </div>
}

export function ApplicationWizardPage() {
  const { identity } = useRoadshow()
  const { applicationId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const [form] = Form.useForm<ApplicationDraft>()
  const options = useLoad<ApplicationOptions>('/application-options')
  const detail = useLoad<ApplicationDetail>(applicationId ? `/applications/${applicationId}` : null)
  const [step, setStep] = useState(0)
  const [draftId, setDraftId] = useState(applicationId || '')
  const [rowVersion, setRowVersion] = useState(1)
  const [report, setReport] = useState<CompatibilityReport | null>(null)
  const [warningsAccepted, setWarningsAccepted] = useState(false)
  const [recommendationContext, setRecommendationContext] = useState<ApplicationRecommendationContext | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [api, holder] = message.useMessage()
  const guard = useRef(createSingleFlight()).current
  const initialized = useRef(false)
  const navigationState = location.state as ApplicationNavigationState | null
  const demandAssistantHandoff = navigationState?.demandAssistant
  const productSelection = navigationState?.productSelection

  useEffect(() => {
    if (identity !== 'data_requester') return
    if (detail.data) {
      if (initialized.current) return
      initialized.current = true
      form.setFieldsValue(detail.data.request)
      setRecommendationContext(detail.data.request.recommendation_context || null)
      setDraftId(detail.data.application_id)
      setRowVersion(detail.data.row_version)
      setReport(detail.data.compatibility)
      return
    }
    if (applicationId || !options.data || initialized.current) return
    initialized.current = true
    const base = defaultDraft()
    const result = demandAssistantHandoff?.result
    if (result?.can_apply_draft && demandAssistantHandoff?.selectedPairKey) {
      const selected = selectVisibleRecommendations(
        result,
        options.data.data_products,
        options.data.model_products,
        demandAssistantHandoff.selectedPairKey,
      )
      const pair = result.pair_candidates.find(
        (item) => item.pair_key === demandAssistantHandoff.selectedPairKey,
      )
      const snapshotMatches = Boolean(pair
        && demandAssistantHandoff.selectedPair.pair_key === pair.pair_key
        && demandAssistantHandoff.selectedPair.data_version_id === pair.data_version_id
        && demandAssistantHandoff.selectedPair.model_version_id === pair.model_version_id)
      const rank = pair ? result.pair_candidates.indexOf(pair) + 1 : 0
      if (selected.canApplyPair && pair && snapshotMatches && rank > 0) {
        const context: ApplicationRecommendationContext = {
          source: 'role_assistant',
          selected_by_user: true,
          selected_pair_key: pair.pair_key,
          data_version_id: pair.data_version_id,
          model_version_id: pair.model_version_id,
          rank,
          score: pair.score.total,
          score_max: pair.score.max_total,
          ruleset_version: pair.score.ruleset_version,
          pair_schema_version: result.pair_candidates_schema_version,
          stage: pair.stage,
          hard_gate_status: pair.hard_gate.status,
          reasons: pair.reasons.slice(0, 8),
          limitations: pair.limitations.slice(0, 8),
        }
        form.setFieldsValue({
          ...base,
          data_version_id: selected.dataVersionId,
          model_version_id: selected.modelVersionId,
          profile: { ...base.profile, ...(result.draft_patch.profile || {}) },
          data_scope: { ...base.data_scope, ...(result.draft_patch.data_scope || {}) },
        })
        setRecommendationContext(context)
        setStep(2)
        setReport(null)
        setWarningsAccepted(false)
        api.success('已带入你确认的数据—模型组合和需求字段；尚未保存或提交')
      } else {
        form.setFieldsValue({
          ...base,
          profile: { ...base.profile, ...(result.draft_patch.profile || {}) },
          data_scope: { ...base.data_scope, ...(result.draft_patch.data_scope || {}) },
        })
        api.warning('所选组合已不可申请，仅带入研究定义；请重新选择产品')
      }
      navigate(location.pathname, { replace: true, state: null })
      return
    }
    if (result?.can_apply_draft) {
      form.setFieldsValue({
        ...base,
        profile: { ...base.profile, ...(result.draft_patch.profile || {}) },
        data_scope: { ...base.data_scope, ...(result.draft_patch.data_scope || {}) },
      })
      api.info('已带入研究定义，但没有用户确认的产品组合，请手动选择')
      navigate(location.pathname, { replace: true, state: null })
      return
    }
    if (productSelection?.dataVersionId || productSelection?.modelVersionId) {
      const dataVersionId = options.data.data_products.some((item) => item.version_id === productSelection.dataVersionId)
        ? productSelection.dataVersionId
        : undefined
      const modelVersionId = options.data.model_products.some((item) => item.version_id === productSelection.modelVersionId)
        ? productSelection.modelVersionId
        : undefined
      form.setFieldsValue({
        ...base,
        ...(dataVersionId ? { data_version_id: dataVersionId } : {}),
        ...(modelVersionId ? { model_version_id: modelVersionId } : {}),
      })
      if (dataVersionId && modelVersionId) setStep(2)
      else if (dataVersionId) setStep(1)
      else setStep(0)
      if (dataVersionId || modelVersionId) api.success('已预选当前产品，请继续选择组合并完善需求')
      else api.warning('当前产品已不在可申请目录中，请重新选择')
      navigate(location.pathname, { replace: true, state: null })
      return
    }
    form.setFieldsValue(base)
  }, [api, applicationId, demandAssistantHandoff, detail.data, form, identity, location.pathname, navigate, options.data, productSelection])

  const fillSample = () => {
    if (!options.data?.sample.data_version_id || !options.data.sample.model_version_id) {
      setError('未找到唯一的已发布 PathMNIST 数据与 ResNet-18 模型，请手动选择。')
      return
    }
    form.setFieldsValue(sampleDraft(options.data))
    setRecommendationContext(null)
    setReport(null)
    setWarningsAccepted(false)
    api.success('PathMNIST 验证需求样例已填充，尚未保存或提交')
  }

  const saveDraft = async () => {
    const formValues = form.getFieldsValue(true) as ApplicationDraft
    const values: ApplicationDraft = {
      ...formValues,
      recommendation_context: recommendationContext || undefined,
    }
    const result = draftId
      ? await platformCommand<{ application_id: string; application_number: string; row_version: number }>(
        `/application-drafts/${draftId}`,
        identity,
        `phase53-update-${secureUuid()}`,
        { ...values, expected_row_version: rowVersion },
        'PATCH',
      )
      : await platformCommand<{ application_id: string; application_number: string; row_version: number }>(
        '/application-drafts',
        identity,
        `phase53-create-${secureUuid()}`,
        values,
      )
    setDraftId(result.application_id)
    setRowVersion(result.row_version)
    if (draftId) {
      setReport(null)
      setWarningsAccepted(false)
    }
    return result
  }

  const save = async () => {
    await guard.run(async () => {
      setBusy('save'); setError('')
      try {
        await form.validateFields()
        const result = await saveDraft()
        api.success(`计算需求草稿已保存，申请编号：${result.application_number}`)
        navigate(`/applications/${result.application_id}`)
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '保存失败')
      } finally { setBusy('') }
    })
  }

  const runCheck = async () => {
    await guard.run(async () => {
      setBusy('check'); setError('')
      try {
        await form.validateFields()
        const checked = await saveAndCheckDraft()
        api.success(`兼容性检查完成：${checked.overall}`)
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '兼容性检查失败')
      } finally { setBusy('') }
    })
  }

  const saveAndCheckDraft = async () => {
    const result = await saveDraft()
    const checked = await platformCommand<CompatibilityReport>(
      `/application-drafts/${result.application_id}/compatibility`,
      identity,
      `phase53-check-${secureUuid()}`,
    )
    setReport(checked)
    return checked
  }

  const submit = async () => {
    if (!draftId || !report) {
      setError('提交前必须保存草稿并完成最新兼容性检查。')
      return
    }
    if (report.blockers.length) {
      setError('当前存在 BLOCKER，不能提交。')
      return
    }
    if (report.warnings.length && !warningsAccepted) {
      setError('请确认兼容性 WARNING 后再提交。')
      return
    }
    await guard.run(async () => {
      setBusy('submit'); setError('')
      try {
        const result = await platformCommand<{ application_id: string; status: string }>(
          `/application-drafts/${draftId}/submit`,
          identity,
          `phase53-submit-${secureUuid()}`,
          { warnings_acknowledged: warningsAccepted },
        )
        api.success(`计算需求已提交，当前状态：${statusLabels[result.status] || result.status}`)
        navigate(`/applications/${result.application_id}`)
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '提交失败')
      } finally { setBusy('') }
    })
  }

  const next = async () => {
    await guard.run(async () => {
      try {
        if (step < 4) await form.validateFields(wizardFields[step] || [])
        if (step === 3) {
          setBusy('check')
          setError('')
          await saveAndCheckDraft()
        }
        setStep((value) => Math.min(4, value + 1))
      } catch (reason) {
        if (reason instanceof Error) setError(reason.message)
        // Field errors are rendered by Ant Design.
      } finally {
        setBusy('')
      }
    })
  }

  if (identity !== 'data_requester') {
    return <Alert type="error" showIcon title="无权创建计算需求" description="只有需求企业可以创建和编辑申请草稿。" />
  }

  const dataVersionId = Form.useWatch('data_version_id', { form, preserve: true })
  const modelVersionId = Form.useWatch('model_version_id', { form, preserve: true })
  const selectedData = options.data?.data_products.find((item) => item.version_id === dataVersionId)
  const selectedModel = options.data?.model_products.find((item) => item.version_id === modelVersionId)
  return <div className="page-stack">
    {holder}
    <PageTitle
      title={applicationId ? '编辑计算需求草稿' : '新建计算需求'}
      description="选择产品、完善需求、核验兼容性并提交审核。"
      actions={<>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(draftId ? `/applications/${draftId}` : '/applications')}>返回</Button>
        <Button onClick={fillSample}>填充 PathMNIST 验证需求样例</Button>
      </>}
    />
    <PageLoad loading={options.loading || detail.loading} error={options.error || detail.error}>
      <Steps current={step} items={[
        { title: '选择数据产品' },
        { title: '选择模型产品' },
        { title: '填写计算需求' },
        { title: '兼容性检查' },
        { title: '预览与提交' },
      ]} />
      {recommendationContext && <Alert
        type="info"
        showIcon
        title="已带入你确认的 Agent 推荐组合"
        description={<Space size={[8, 8]} wrap>
          <Tag color="blue">第 {recommendationContext.rank} 名</Tag>
          <Tag color="cyan">Agent 初筛 {recommendationContext.score}/{recommendationContext.score_max}</Tag>
          <Tag>{recommendationContext.hard_gate_status === 'pass' ? 'Agent 初筛通过' : 'Agent 提示条件待补'}</Tag>
          <Text type="secondary">资格以服务端兼容性检查为准</Text>
        </Space>}
      />}
      {error && <Alert type="error" showIcon title="操作未完成" description={error} />}
      <Form
        form={form}
        layout="vertical"
        className="phase51-form"
        initialValues={defaultDraft()}
        onValuesChange={(changedValues) => {
          if ('data_version_id' in changedValues || 'model_version_id' in changedValues) {
            setRecommendationContext(null)
          }
          setReport(null)
          setWarningsAccepted(false)
        }}
      >
        {step === 0 && <section className="phase51-section">
          <Title level={4}>选择已发布数据产品</Title>
          <Text type="secondary">仅展示当前空间中已批准且存在 active publication 的具体版本。</Text>
          <Form.Item name="data_version_id" rules={[{ required: true, message: '请选择数据产品版本' }]}>
            <SelectionGrid items={options.data?.data_products || []} selected={dataVersionId} onSelect={(value) => { form.setFieldValue('data_version_id', value); setRecommendationContext(null); setReport(null) }} kind="data" />
          </Form.Item>
        </section>}
        {step === 1 && <section className="phase51-section">
          <Title level={4}>选择已发布模型产品</Title>
          <Text type="secondary">模型必须绑定固定白名单资产；页面不显示模型权重、路径、凭据或任意执行入口。</Text>
          <Form.Item name="model_version_id" rules={[{ required: true, message: '请选择模型产品版本' }]}>
            <SelectionGrid items={options.data?.model_products || []} selected={modelVersionId} onSelect={(value) => { form.setFieldValue('model_version_id', value); setRecommendationContext(null); setReport(null) }} kind="model" />
          </Form.Item>
        </section>}
        {step === 3 && <section className="phase51-section">
          <div className="phase51-section-head">
            <div><Title level={4}>数据与模型兼容性检查</Title><Text type="secondary">检查由后端执行并持久化，不是前端静态判断。</Text></div>
            <Button type="primary" icon={<SyncOutlined />} loading={busy === 'check'} onClick={runCheck}>运行服务端检查</Button>
          </div>
          <Row gutter={16}>
            <Col xs={24} md={8}><Form.Item name={['profile', 'purpose_code']} label="申请用途" rules={[{ required: true }]}><Select options={[
              { value: 'model_validation', label: '模型验证' },
              { value: 'research_analysis', label: '科研分析' },
              { value: 'external_performance_validation', label: '外部性能验证' },
              { value: 'teaching_demo', label: '教学演示' },
              { value: 'commercial_validation', label: '商业验证' },
            ]} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['execution', 'run_count']} label="最大运行次数" rules={[{ required: true }]}><InputNumber min={1} className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['execution', 'valid_days']} label="有效期（天）" rules={[{ required: true }]}><InputNumber min={1} className="phase51-full" /></Form.Item></Col>
            <Col xs={24}><Form.Item name={['execution', 'requested_outputs']} label="请求输出" rules={[{ required: true }]}><Checkbox.Group options={[
              { value: 'aggregate_metrics', label: '聚合性能指标' },
              { value: 'confusion_matrix', label: '混淆矩阵' },
              { value: 'execution_summary', label: '执行摘要' },
            ]} /></Form.Item></Col>
          </Row>
          <CompatibilityPanel report={report} />
        </section>}
        {step === 2 && <section className="phase51-section">
          <Title level={4}>计算需求与数据范围</Title>
          <Row gutter={16}>
            <Col xs={24} md={16}><Form.Item name={['profile', 'demand_name']} label="需求名称" rules={[{ required: true, min: 4 }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['profile', 'project_type']} label="项目类型" rules={[{ required: true }]}><Select options={[
              { value: 'model_external_validation', label: '模型外部验证' },
              { value: 'research_analysis', label: '科研分析' },
              { value: 'multicenter_validation', label: '多中心验证' },
              { value: 'teaching_demo', label: '教学演示' },
              { value: 'algorithm_performance_evaluation', label: '算法性能评估' },
              { value: 'other', label: '其他' },
            ]} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['profile', 'project_lead']} label="项目负责人" rules={[{ required: true, min: 2, message: '项目负责人至少填写 2 个字符' }]}><Input /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['profile', 'contact']} label="联系方式或联系部门" rules={[{ required: true, min: 2, message: '联系方式或联系部门至少填写 2 个字符' }]}><Input /></Form.Item></Col>
            <Col xs={24}><Form.Item name={['profile', 'project_summary']} label="项目简介" rules={[{ required: true, min: 20 }]}><Input.TextArea rows={3} /></Form.Item></Col>
            <Col xs={24}><Form.Item name={['profile', 'research_purpose']} label="具体研究或验证目的" rules={[{ required: true, min: 20 }]}><Input.TextArea rows={3} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['profile', 'use_background']} label="使用背景" rules={[{ required: true, min: 10 }]}><Input.TextArea rows={3} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['profile', 'expected_value']} label="预期价值" rules={[{ required: true, min: 10 }]}><Input.TextArea rows={3} /></Form.Item></Col>
            <Col xs={24}><Form.Item name={['profile', 'ethics_or_approval_statement']} label="伦理或审批状态说明" rules={[{ required: true, min: 5, message: '伦理或审批状态说明至少填写 5 个字符' }]}><Input.TextArea rows={2} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['profile', 'project_reference']} label="项目或伦理编号"><Input /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['data_scope', 'sample_count']} label="样本/图像数量" rules={[{ required: true }]}><InputNumber min={1} className="phase51-full" /></Form.Item></Col>
            <Col xs={24}><Form.Item name={['profile', 'data_minimization']} label="数据最小化说明" rules={[{ required: true, min: 10 }]}><Input.TextArea rows={2} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['data_scope', 'scope_type']} label="数据范围" rules={[{ required: true }]}><Radio.Group options={[
              { value: 'all_approved_demo_data', label: '使用全部获批演示数据' },
              { value: 'described_subset', label: '使用描述的允许子集' },
            ]} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['data_scope', 'subset_description']} label="子集描述"><Input /></Form.Item></Col>
            <Col xs={24}><Form.Item name={['data_scope', 'selection_criteria']} label="筛选条件"><Input /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['review_requirements', 'result_review_notes']} label="结果审查说明" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['review_requirements', 'output_recipient']} label="输出接收负责人" rules={[{ required: true, min: 2, message: '输出接收负责人至少填写 2 个字符' }]}><Input /></Form.Item></Col>
          </Row>
        </section>}
        {step === 4 && <section className="phase51-section">
          <Title level={4}>预览、声明与提交确认</Title>
          <Descriptions bordered column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="需求企业">{roleProfiles.data_requester.organization}</Descriptions.Item>
            <Descriptions.Item label="空间运营方">{roleProfiles.space_operator.organization}</Descriptions.Item>
            <Descriptions.Item label="数据产品">{selectedData ? `${selectedData.name} ${selectedData.version}` : '未选择'}</Descriptions.Item>
            <Descriptions.Item label="医院数据方">{selectedData?.provider || '未选择'}</Descriptions.Item>
            <Descriptions.Item label="模型产品">{selectedModel ? `${selectedModel.name} ${selectedModel.version}` : '未选择'}</Descriptions.Item>
            <Descriptions.Item label="模型提供方">{selectedModel?.provider || '未选择'}</Descriptions.Item>
            <Descriptions.Item label="兼容性">{report ? `${report.overall} · ${report.counts.blocker} BLOCKER` : '需重新检查'}</Descriptions.Item>
            <Descriptions.Item label="下一步">平台预审 → 医院审核 → 模型方审核 → 数字合约 → 结算</Descriptions.Item>
          </Descriptions>
          {dataVersionId && modelVersionId && <CommercialComputeQuotePreview dataVersionId={dataVersionId} modelVersionId={modelVersionId} />}
          <CompatibilityPanel report={report} />
          <Form.Item name={['declarations', 'no_raw_data_download']} valuePropName="checked"><Checkbox>不申请原始医疗数据下载</Checkbox></Form.Item>
          <Form.Item name={['declarations', 'no_model_weight_download']} valuePropName="checked"><Checkbox>不申请模型权重下载</Checkbox></Form.Item>
          <Form.Item name={['declarations', 'approved_purpose_only']} valuePropName="checked"><Checkbox>不超出批准用途，不将工程演示结果用于临床诊断</Checkbox></Form.Item>
          <Form.Item name={['declarations', 'accept_multiparty_review']} valuePropName="checked"><Checkbox>接受平台、医院和模型方多方审核</Checkbox></Form.Item>
          <Form.Item name={['declarations', 'accept_result_isolation']} valuePropName="checked"><Checkbox>接受结果默认隔离和后续结果审核</Checkbox></Form.Item>
          <Form.Item name={['declarations', 'accept_full_audit']} valuePropName="checked"><Checkbox>接受全过程审计</Checkbox></Form.Item>
          {Boolean(report?.warnings.length) && <Checkbox checked={warningsAccepted} onChange={(event) => setWarningsAccepted(event.target.checked)}>我已阅读并接受兼容性 WARNING，不将其解释为生产级安全认证</Checkbox>}
          <div className="phase53-preview-actions">
            <Button icon={<SyncOutlined />} loading={busy === 'check'} onClick={runCheck}>重新检查</Button>
            <Button icon={<SaveOutlined />} loading={busy === 'save'} onClick={save}>保存草稿</Button>
            <Button type="primary" icon={<SendOutlined />} loading={busy === 'submit'} disabled={!report || Boolean(report.blockers.length) || Boolean(report.warnings.length && !warningsAccepted)} onClick={submit}>提交多方审核</Button>
          </div>
        </section>}
      </Form>
      <div className="phase51-actions">
        <Button disabled={step === 0} onClick={() => setStep((value) => Math.max(0, value - 1))}>上一步</Button>
        <div />
        {step >= 2 && <Button icon={<SaveOutlined />} loading={busy === 'save'} onClick={save}>保存草稿</Button>}
        {step < 4 && <Button type="primary" loading={step === 3 && busy === 'check'} onClick={next}>{step === 3 ? '保存并完成检查' : '下一步'}</Button>}
      </div>
    </PageLoad>
  </div>
}

function EvidencePanel({ applicationId }: { applicationId: string }) {
  const state = useLoad<{ items: AuditItem[]; audit_chain_valid: boolean; total: number }>(
    `/applications/${applicationId}/audit-events`,
  )
  const [selected, setSelected] = useState<AuditItem | null>(null)
  return <Card title="操作证据" extra={state.data && <Tag color={state.data.audit_chain_valid ? 'green' : 'red'}>{state.data.audit_chain_valid ? '审计链有效' : '审计链异常'}</Tag>}>
    <PageLoad loading={state.loading} error={state.error}>
      <Timeline items={(state.data?.items || []).slice(0, 8).map((item) => ({
        color: item.event_type.endsWith('rejected') || item.event_type.endsWith('returned') ? 'red' : 'green',
        content: <button className="phase51-event-button" onClick={() => setSelected(item)}>
          <strong>{eventLabels[item.event_type] || item.event_type}</strong>
          <span>{new Date(item.occurred_at).toLocaleString()} · {item.actor}</span>
          <small>{item.state_before || '无'} → {item.state_after || '无'} · {item.event_id.slice(0, 8)}…</small>
        </button>,
      }))} />
      {!state.data?.items.length && <Empty description="暂无审计事件" />}
    </PageLoad>
    <Drawer title="技术证据" size={600} open={Boolean(selected)} onClose={() => setSelected(null)}>
      {selected && <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="Event ID">{selected.event_id}</Descriptions.Item>
        <Descriptions.Item label="Application ID">{applicationId}</Descriptions.Item>
        <Descriptions.Item label="Subject">{selected.subject_type} · {selected.subject_id}</Descriptions.Item>
        <Descriptions.Item label="Actor">{selected.actor} · {selected.organization || '无'}</Descriptions.Item>
        <Descriptions.Item label="State">{selected.state_before || '无'} → {selected.state_after || '无'}</Descriptions.Item>
        <Descriptions.Item label="ReviewTask ID">{selected.review_task_id || '无'}</Descriptions.Item>
        <Descriptions.Item label="Compatibility digest">{selected.compatibility_input_digest || '无'}</Descriptions.Item>
        <Descriptions.Item label="Previous hash">{selected.previous_hash || 'Genesis'}</Descriptions.Item>
        <Descriptions.Item label="Current hash">{selected.current_hash}</Descriptions.Item>
        <Descriptions.Item label="Evidence digest">{selected.evidence_digest}</Descriptions.Item>
        <Descriptions.Item label="Correlation ID">{selected.correlation_id}</Descriptions.Item>
        <Descriptions.Item label="Outbox">{selected.outbox.map((item) => `${item.destination}: ${item.status}`).join('；') || '无'}</Descriptions.Item>
      </Descriptions>}
    </Drawer>
  </Card>
}

function ReviewForm({ detail, task, onChanged }: { detail: ApplicationDetail; task: ReviewItem; onChanged: () => void }) {
  const { identity } = useRoadshow()
  const [form] = Form.useForm()
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [api, holder] = message.useMessage()
  const guard = useRef(createSingleFlight()).current
  const decide = async (action: 'approve' | 'return' | 'reject') => {
    await guard.run(async () => {
      setBusy(action); setError('')
      try {
        const values = await form.validateFields()
        const result = await platformCommand<{ application_status: string; replacement_application_id: string | null; next_step: string }>(
          `/application-review-tasks/${task.task_id}/decide`,
          identity,
          `phase53-review-${secureUuid()}`,
          {
            action,
            reason_code: action === 'approve' ? null : values.reason_code || 'other',
            comment: values.comment,
            evidence: {
              completeness_check: values.completeness_check || '',
              compatibility_conclusion: values.compatibility_conclusion || '',
              purpose_assessment: values.purpose_assessment || '',
              output_risk: values.output_risk || '',
              risk_level: values.risk_level,
              approved_scope: values.approved_scope || '',
              max_runs: values.max_runs,
              valid_days: values.valid_days,
              allowed_outputs: values.allowed_outputs || [],
              prohibited_outputs: ['raw_images', 'patient_level_predictions', 'model_weights'],
              requires_egress_review: true,
              allowed_environment: values.allowed_environment || '',
              requires_technical_confirmation: true,
              additional_conditions: values.additional_conditions || '',
              requested_materials: values.requested_materials || '',
            },
          },
        )
        api.success(action === 'approve'
          ? `审核已批准，申请状态：${statusLabels[result.application_status] || result.application_status}`
          : action === 'return'
            ? '申请已退回，并生成可补充的关联草稿'
            : '申请已拒绝')
        onChanged()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '审核失败')
      } finally { setBusy('') }
    })
  }
  return <section className="phase51-section">
    {holder}
    <Title level={4}>{reviewLabels[task.review_type] || task.review_type}</Title>
    {error && <Alert type="error" showIcon title="审核未完成" description={error} />}
    <Form form={form} layout="vertical" initialValues={{
      risk_level: 'low',
      max_runs: detail.request.execution.run_count,
      valid_days: detail.request.execution.valid_days,
      allowed_outputs: detail.request.execution.requested_outputs,
      allowed_environment: detail.request.execution.environment_requirements,
    }}>
      <Row gutter={16}>
        <Col xs={24} md={12}><Form.Item name="completeness_check" label="完整性检查"><Input /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="compatibility_conclusion" label="兼容性结论"><Input /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="purpose_assessment" label="用途合理性"><Input /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="output_risk" label="输出风险"><Input /></Form.Item></Col>
        <Col xs={24} md={8}><Form.Item name="risk_level" label="风险等级" rules={[{ required: true }]}><Select options={[
          { value: 'low', label: '低' }, { value: 'medium', label: '中' }, { value: 'high', label: '高' },
        ]} /></Form.Item></Col>
        <Col xs={24} md={8}><Form.Item name="max_runs" label="最大运行次数"><InputNumber min={1} className="phase51-full" /></Form.Item></Col>
        <Col xs={24} md={8}><Form.Item name="valid_days" label="有效期限制"><InputNumber min={1} className="phase51-full" /></Form.Item></Col>
        <Col xs={24}><Form.Item name="allowed_outputs" label="允许输出"><Checkbox.Group options={[
          { value: 'aggregate_metrics', label: '聚合指标' },
          { value: 'confusion_matrix', label: '混淆矩阵' },
          { value: 'execution_summary', label: '执行摘要' },
        ]} /></Form.Item></Col>
        <Col xs={24}><Form.Item name="approved_scope" label="批准范围"><Input /></Form.Item></Col>
        <Col xs={24}><Form.Item name="allowed_environment" label="允许执行环境"><Input /></Form.Item></Col>
        <Col xs={24}><Form.Item name="additional_conditions" label="附加条件"><Input /></Form.Item></Col>
        <Col xs={24}><Form.Item name="requested_materials" label="要求补充材料"><Input /></Form.Item></Col>
        <Col xs={24}><Form.Item name="comment" label="审核意见" rules={[{ required: true, min: 5 }]}><Input.TextArea rows={3} /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="reason_code" label="退回/拒绝原因"><Select allowClear options={[
          { value: 'incomplete_materials', label: '材料不完整' },
          { value: 'policy_conflict', label: '策略冲突' },
          { value: 'purpose_not_justified', label: '用途不充分' },
          { value: 'other', label: '其他' },
        ]} /></Form.Item></Col>
      </Row>
      <Space wrap>
        <Button danger loading={busy === 'reject'} disabled={Boolean(busy)} onClick={() => decide('reject')}>拒绝</Button>
        <Button icon={<WarningOutlined />} loading={busy === 'return'} disabled={Boolean(busy)} onClick={() => decide('return')}>退回补充</Button>
        <Button type="primary" icon={<CheckCircleOutlined />} loading={busy === 'approve'} disabled={Boolean(busy)} onClick={() => decide('approve')}>批准</Button>
      </Space>
    </Form>
  </section>
}

export function ApplicationDetailPage() {
  const { identity } = useRoadshow()
  const { applicationId = '' } = useParams()
  const navigate = useNavigate()
  const [api, holder] = message.useMessage()
  const [contractBusy, setContractBusy] = useState(false)
  const contractGuard = useRef(createSingleFlight()).current
  const state = useLoad<ApplicationDetail>(applicationId ? `/applications/${applicationId}` : null)
  const detail = state.data
  const enterContract = async () => {
    if (!detail) return
    if (detail.contract) {
      navigate(`/contracts/${detail.contract.id}`)
      return
    }
    await contractGuard.run(async () => {
      setContractBusy(true)
      try {
        const contract = await platformCommand<{ contract_id: string }>(
          `/applications/${detail.application_id}/contract`,
          identity,
          `phase5.4-ui:generate:${secureUuid()}`,
        )
        api.success('数字合约草稿生成成功')
        navigate(`/contracts/${contract.contract_id}`)
      } catch (reason) {
        api.error(reason instanceof Error ? reason.message : '数字合约生成失败')
      } finally {
        setContractBusy(false)
      }
    })
  }
  const expectedReview = {
    space_operator: 'application_precheck',
    data_provider: 'data_provider_review',
    model_provider: 'model_provider_review',
    data_requester: '',
  }[identity]
  const reviewTask = detail?.reviews.find((item) => item.review_type === expectedReview && item.status === 'pending')
  const currentSequence = detail?.reviews.find((item) => item.status === 'pending')?.sequence_no
  const actionable = reviewTask && reviewTask.sequence_no === currentSequence
  const progress = detail?.status === 'approved' ? 100 : detail?.status === 'provider_review' ? 70 : detail?.status === 'prechecking' ? 45 : 20
  return <div className="page-stack">{holder}
    <PageLoad loading={state.loading} error={state.error}>
      {detail && <>
        <PageTitle
          title={detail.demand_name || '计算需求'}
          description={`${detail.application_number} · ${detail.applicant.name}`}
          actions={<>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/applications')}>返回列表</Button>
            {detail.allowed_actions.includes('edit') && <Button icon={<EditOutlined />} onClick={() => navigate(`/applications/${detail.application_id}/edit`)}>编辑草稿</Button>}
            {detail.status === 'approved' && detail.contract && <Button type="primary" icon={<FileSearchOutlined />} loading={contractBusy} onClick={enterContract}>进入数字合约</Button>}
            {detail.status === 'approved' && !detail.contract && identity === 'space_operator' && <Button type="primary" icon={<FileSearchOutlined />} loading={contractBusy} onClick={enterContract}>生成数字合约草稿</Button>}
            {detail.status === 'approved' && !detail.contract && identity !== 'space_operator' && <Button icon={<FileSearchOutlined />} disabled>等待平台生成合约</Button>}
          </>}
        />
        {detail.status === 'approved' && <Alert type="success" showIcon title="申请已获批" description="所有必需审核均已通过，下一步完成数字合约；合约生效后再结算。本阶段不能直接创建计算任务。" />}
        {detail.status === 'rejected' && <Alert type="error" showIcon title="申请已退回或拒绝" description={detail.decision_summary || '请查看审核意见和关联替代草稿。'} />}
        <div className="phase51-detail-grid">
          <div className="phase51-detail-main">
            <section className="phase51-section">
              <Descriptions title="参与方与锁定版本" bordered column={{ xs: 1, md: 2 }}>
                <Descriptions.Item label="当前状态">{statusTag(detail.status)}</Descriptions.Item>
                <Descriptions.Item label="申请编号">{detail.application_number}</Descriptions.Item>
                <Descriptions.Item label="需求企业">{detail.applicant.name}</Descriptions.Item>
                <Descriptions.Item label="空间运营方">{roleProfiles.space_operator.organization}</Descriptions.Item>
                <Descriptions.Item label="医院数据方">{detail.data_provider.name}</Descriptions.Item>
                <Descriptions.Item label="模型提供方">{detail.model_provider.name}</Descriptions.Item>
                <Descriptions.Item label="数据产品">{detail.data_product.name} · {detail.data_product.version}</Descriptions.Item>
                <Descriptions.Item label="模型产品">{detail.model_product.name} · {detail.model_product.version}</Descriptions.Item>
              </Descriptions>
            </section>
            <section className="phase51-section">
              <Descriptions title="申请范围" bordered column={{ xs: 1, md: 2 }}>
                <Descriptions.Item label="项目类型">{detail.request.profile.project_type}</Descriptions.Item>
                <Descriptions.Item label="用途">{detail.request.profile.purpose_code}</Descriptions.Item>
                <Descriptions.Item label="运行次数">{detail.request.execution.run_count}</Descriptions.Item>
                <Descriptions.Item label="有效期">{detail.request.execution.valid_days} 天</Descriptions.Item>
                <Descriptions.Item label="数据范围">{detail.request.data_scope.subset_description || detail.request.data_scope.scope_type}</Descriptions.Item>
                <Descriptions.Item label="请求输出">{detail.request.execution.requested_outputs.join('、')}</Descriptions.Item>
                <Descriptions.Item label="项目简介" span={{ xs: 1, md: 2 }}>{detail.request.profile.project_summary}</Descriptions.Item>
                <Descriptions.Item label="数据最小化" span={{ xs: 1, md: 2 }}>{detail.request.profile.data_minimization}</Descriptions.Item>
              </Descriptions>
            </section>
            {detail.request.recommendation_context && <section className="phase51-section">
              <Descriptions title="Agent 组合选择快照" bordered column={{ xs: 1, md: 2 }}>
                <Descriptions.Item label="用户确认">已明确选择第 {detail.request.recommendation_context.rank} 名组合</Descriptions.Item>
                <Descriptions.Item label="证据属性"><Tag>客户端选择陈述，未作平台验证</Tag></Descriptions.Item>
                <Descriptions.Item label="Agent 初筛得分">{detail.request.recommendation_context.score}/{detail.request.recommendation_context.score_max}</Descriptions.Item>
                <Descriptions.Item label="Agent 候选阶段">{detail.request.recommendation_context.stage}</Descriptions.Item>
                <Descriptions.Item label="Agent 初筛状态">{detail.request.recommendation_context.hard_gate_status}</Descriptions.Item>
                <Descriptions.Item label="Agent 评分规则" span={{ xs: 1, md: 2 }}>{detail.request.recommendation_context.ruleset_version}</Descriptions.Item>
                <Descriptions.Item label="推荐依据" span={{ xs: 1, md: 2 }}>{detail.request.recommendation_context.reasons.join('；') || '未提供'}</Descriptions.Item>
                <Descriptions.Item label="已知限制" span={{ xs: 1, md: 2 }}>{detail.request.recommendation_context.limitations.join('；') || '无额外限制'}</Descriptions.Item>
                {detail.client_selection_snapshot_receipt && <>
                  <Descriptions.Item label="平台接收时间">{new Date(detail.client_selection_snapshot_receipt.received_at).toLocaleString('zh-CN', { hour12: false })}</Descriptions.Item>
                  <Descriptions.Item label="平台回执">已接收并固化摘要，不代表平台验证</Descriptions.Item>
                  <Descriptions.Item label="选择快照摘要" span={{ xs: 1, md: 2 }}><Text code>{detail.client_selection_snapshot_receipt.snapshot_digest}</Text></Descriptions.Item>
                  <Descriptions.Item label="申请资格依据" span={{ xs: 1, md: 2 }}>服务端兼容性报告</Descriptions.Item>
                </>}
              </Descriptions>
            </section>}
            <section className="phase51-section">
              <Title level={4}>兼容性报告</Title>
              <CompatibilityPanel report={detail.compatibility} />
            </section>
            <section className="phase51-section">
              <Title level={4}>各方审核意见</Title>
              {detail.reviews.length ? <Timeline items={detail.reviews.map((item) => ({
                color: item.decision?.decision === 'approved' ? 'green' : item.status === 'cancelled' ? 'gray' : 'blue',
                content: <div><strong>{reviewLabels[item.review_type] || item.review_type}</strong><Paragraph>{item.decision?.comment || `${item.organization} 待处理`}</Paragraph>{item.decision && <Tag color={item.decision.decision === 'approved' ? 'green' : 'red'}>{item.decision.decision}</Tag>}</div>,
              }))} /> : <Empty description="草稿尚未生成审核任务" />}
            </section>
            {actionable && reviewTask && <ReviewForm detail={detail} task={reviewTask} onChanged={state.refresh} />}
            {reviewTask && !actionable && <Alert type="info" showIcon title="等待前序审核" description="当前 ReviewTask 已创建，但前序必需审核尚未批准，暂不可处理。" />}
          </div>
          <aside className="phase51-detail-side">
            <Card title="审批进度">
              <Progress percent={progress} />
              <Steps orientation="vertical" size="small" current={detail.status === 'approved' ? 4 : detail.review_progress.completed} items={[
                { title: '申请草稿' },
                { title: '平台预审' },
                { title: '医院数据审核' },
                { title: '模型使用审核' },
                { title: '待数字合约' },
              ]} />
            </Card>
            {detail.snapshot && <Card title="冻结快照"><Text className="phase51-code">{detail.snapshot.digest}</Text></Card>}
            {detail.contract && <Card title="关联合同"><FileSearchOutlined /> {detail.contract.number}</Card>}
            <EvidencePanel applicationId={detail.application_id} />
          </aside>
        </div>
      </>}
    </PageLoad>
  </div>
}
