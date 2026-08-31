import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  EditOutlined,
  EyeOutlined,
  FileSearchOutlined,
  PlusOutlined,
  ReloadOutlined,
  SaveOutlined,
  SearchOutlined,
  SendOutlined,
} from '@ant-design/icons'
import { secureUuid } from '../lib/secureUuid'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Pagination,
  Popover,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Steps,
  Switch,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { platformCommand, platformGet } from './api'
import { DatasetModelEvidenceSummary } from './DatasetModelEvidencePages'
import {
  buildPathmnistModelDraft,
  validateModelBoundary,
  type ModelAsset,
  type ModelProductDraft,
} from './modelProductForm'
import { createSingleFlight, startAbortableLoad } from './requestLifecycle'
import { useRoadshow } from './RoadshowContext'
import {
  LifecycleActions,
  LifecycleTimeline,
  type LifecycleRequest,
} from './ProductLifecycleGovernance'
import { ServiceAuthorizationModal } from './ServiceAccessRequests'
import { CommercialOfferPreview } from './CommercialCheckoutPage'
import {
  availableOfferings,
  offeringLabel,
  offeringTagColor,
  serviceModeLabels,
  type ServiceOffering,
} from './serviceAccess'

const { Paragraph, Text, Title } = Typography

type ModelDetail = {
  product_id: string
  version_id: string
  product_code: string
  name: string
  description: string
  domain: string
  provider: string
  status: string
  version_status: string
  version_label: string
  entrypoint_id: string
  model_digest: string
  manifest_digest: string
  registry_digest: string
  runtime: string
  input_schema_version: string
  output_schema_version: string
  compatibility: Record<string, unknown>
  license: Record<string, unknown>
  policy: Record<string, unknown>
  snapshot_digest: string | null
  created_at: string
  updated_at: string
  submitted_at: string | null
  approved_at: string | null
  published_at: string | null
  unpublished_at: string | null
  deleted_at: string | null
  current_lifecycle_request: LifecycleRequest | null
  offerings?: ServiceOffering[]
  latest_return: null | {
    review_opinion: string
    requested_materials: string
    technical_risk: string
    license_risk: string
  }
  allowed_actions: string[]
  external_source: null | {
    source_kind: string
    catalog_version: string
    source_record_digest: string
    governance_snapshot_digest: string
    upstream_provider: string | null
    upstream_official_url: string
    materialization_status: string
    executor_registered: boolean
    execution_readiness: string
    platform_validation: string
    application_eligibility: boolean
    compute_eligibility: boolean
    review_count: number
  }
}

type ModelCatalogItem = {
  product_id: string
  version_id: string
  product_code: string
  name: string
  provider: string
  description: string
  disease_domain: string
  task_type: string
  modality: string
  version: string
  license_summary: { allowed_purposes?: string[] }
  non_clinical: boolean
  source_kind: string
  upstream_provider: string | null
  materialization_status: string
  executor_registered: boolean
  execution_readiness: string
  platform_validation: string
  application_eligibility: boolean
  compute_eligibility: boolean
  offerings?: ServiceOffering[]
}

type ModelMarketplaceMode = 'controlled_compute' | 'model_artifact_license'

type PartnerModelShowcase = {
  key: string
  name: string
  provider: string
  summary: string
  focus: string
  plannedModes: ModelMarketplaceMode[]
}

type ModelMarketplaceSourceFilter = 'all' | 'public' | 'provider' | 'partner'

type PublicModelCatalogItem = {
  id: string
  external_model_id: string
  canonical_name: string
  display_name_cn: string | null
  display_name_en: string | null
  model_categories: string[]
  modalities: string[]
  task_types: string[]
  disease_areas: string[]
  organs: string[]
  framework: string | null
  license_name: string | null
  license_status: string
  access_status: string
  weights_status: string
  execution_status: string
  published_product_version_id?: string | null
}

type PublicModelCatalogDetail = PublicModelCatalogItem & {
  source_catalog: string
  paper_title: string | null
  paper_url: string | null
  code_repository_url: string | null
  model_card_url: string | null
  upstream_provider: string | null
  architecture: string | null
  input_schema: string | null
  output_schema: string | null
  intended_use_summary: string | null
  limitations_summary: string | null
}

const PUBLIC_MODEL_PAGE_SIZE = 12
const MODEL_CATALOG_SEARCH_DEBOUNCE_MS = 275

function useDebouncedModelCatalogText(value: string) {
  const [debouncedValue, setDebouncedValue] = useState(value)
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedValue(value), MODEL_CATALOG_SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timeout)
  }, [value])
  return debouncedValue
}

function publicModelName(item: PublicModelCatalogItem) {
  return item.display_name_cn || item.display_name_en || item.canonical_name
}

function publicModelSummary(item: PublicModelCatalogItem) {
  const scope = [...item.disease_areas, ...item.organs, ...item.task_types].filter(Boolean).slice(0, 3).join('、')
  return scope || '公共医疗 AI 模型目录元数据，等待完成治理与服务接入。'
}

function PublicModelCatalogCard({
  item,
  onOpen,
}: {
  item: PublicModelCatalogItem
  onOpen: (item: PublicModelCatalogItem) => void
}) {
  return <Card
    className="phase51-catalog-item marketplace-card"
    tabIndex={0}
    title={<div className="marketplace-card-title"><span>{publicModelName(item)}</span><Tag color="blue">目录资源</Tag></div>}
  >
    <div className="marketplace-card-summary">
      <Text type="secondary" className="marketplace-card-provider">公共模型目录</Text>
      <Paragraph ellipsis={{ rows: 2 }}>{publicModelSummary(item)}</Paragraph>
    </div>
    <div className="commerce-offer-list is-compact">
      <div className="commerce-offer-head"><Text type="secondary">服务状态</Text><Text strong>待治理接入</Text></div>
    </div>
    <div className="marketplace-card-details" aria-label="悬停查看公共模型目录信息">
      <Space wrap className="phase51-catalog-offerings">
        {(item.model_categories.length ? item.model_categories : ['类别待补充']).slice(0, 2).map((value) => <Tag key={value}>{value}</Tag>)}
        {(item.modalities.length ? item.modalities : ['模态待补充']).slice(0, 1).map((value) => <Tag key={value}>{value}</Tag>)}
        <Tag color={item.license_status === 'unknown' ? 'gold' : 'default'}>{item.license_name || item.license_status}</Tag>
      </Space>
    </div>
    <Divider />
    <div className="phase51-card-actions">
      <Button icon={<FileSearchOutlined />} onClick={() => onOpen(item)}>查看目录详情</Button>
    </div>
  </Card>
}

function modelMarketplaceSource(item: ModelCatalogItem) {
  const isPublic = item.source_kind === 'external_public_model'
  return {
    kind: isPublic ? 'public' as const : 'provider' as const,
    label: isPublic ? '公共开源' : '机构模型',
    color: isPublic ? 'blue' : 'cyan',
    name: item.upstream_provider || item.provider,
  }
}

const partnerModelShowcases: PartnerModelShowcase[] = [
  {
    key: 'pathowish',
    name: 'PathoWish',
    provider: '罗小罗科技（北京）有限公司',
    summary: '病理智能模型合作展示位，面向后续产品接入。',
    focus: '数字病理',
    plannedModes: ['controlled_compute', 'model_artifact_license'],
  },
  {
    key: 'muguang-matrix',
    name: '沐光矩阵',
    provider: '沐光矩阵',
    summary: '医疗模型合作展示位，面向后续产品接入。',
    focus: '医疗人工智能',
    plannedModes: ['controlled_compute', 'model_artifact_license'],
  },
]

type AuditItem = {
  event_id: string
  event_type: string
  result: string
  occurred_at: string
  actor: string
  organization: string
  subject_id: string
  state_before: string | null
  state_after: string | null
  correlation_id: string
  previous_hash: string | null
  current_hash: string
  evidence_digest: string
  outbox: Array<{ destination: string; status: string }>
}

const stateLabels: Record<string, string> = {
  draft: '草稿',
  under_review: '上架审核中',
  approved: '已批准',
  published: '已发布',
  active: '已发布',
  unpublished: '已下架',
  archived: '已归档',
}

const eventLabels: Record<string, string> = {
  'model_product.version.created': '模型产品草稿创建',
  'model_product.version.updated': '模型产品草稿更新',
  'model_product.version.submitted': '提交模型上架审核',
  'model_product.version.returned': '模型上架审核退回',
  'model_product.version.approved': '模型上架审核批准',
  'model_product.version.published': '模型产品正式发布',
}

function statusTag(status: string) {
  const color = status === 'published' || status === 'active'
    ? 'green'
    : status === 'under_review'
      ? 'gold'
      : 'default'
  return <Tag color={color}>{stateLabels[status] || status}</Tag>
}

function formatEvidenceTime(value: string | null | undefined) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { hour12: false })
}

const purposeLabels: Record<string, string> = {
  catalog_discovery: '目录检索',
  governance_revalidation: '治理复核',
  research_analysis: '科研分析',
  model_validation: '模型验证',
  external_performance_validation: '外部性能验证',
  teaching_demo: '教学演示',
  model_training: '模型训练',
}

function materializationStatus(value: string) {
  if (value === 'materialized') return { label: '已物化', color: 'green' }
  if (value === 'metadata_only') return { label: '仅元数据', color: 'gold' }
  return { label: '未物化', color: 'default' }
}

function modelServiceStatus(eligible: boolean, executionReadiness: string, executorRegistered = true) {
  if (!eligible && !executorRegistered) return { label: '不可申请 · Executor 未注册', color: 'default' }
  if (!eligible) return { label: '不可申请', color: 'default' }
  if (executionReadiness === 'ready') return { label: '可申请 · 执行就绪', color: 'cyan' }
  return { label: '可申请 · 待执行就绪', color: 'gold' }
}

function modelEvidenceStatus(platformValidation: string | undefined, snapshotDigest?: string | null) {
  if (snapshotDigest) return { label: '版本证据已固化', color: 'green' }
  if (platformValidation === 'validated') return { label: '平台已验证', color: 'green' }
  if (platformValidation) return { label: '平台验证未完成', color: 'default' }
  return { label: '证据未标注', color: 'default' }
}

function modelValidationLabel(value: string | undefined) {
  if (value === 'validated') return '已记录平台验证证据'
  if (value === 'asset_integrity_verified') return '资产完整性已核验'
  if (value === 'image_level_technical_validation') return '图像级技术验证'
  if (!value || value === 'not_validated') return '未验证'
  return value
}

function executionReadinessLabel(value: string) {
  if (value === 'ready') return '执行就绪'
  if (value === 'validation_ready') return '仅验证准备'
  if (value === 'not_ready') return '未就绪'
  return value
}

function FourAxisStatus({
  lifecycle,
  materialization,
  service,
  evidence,
}: {
  lifecycle: string
  materialization: { label: string; color: string }
  service: { label: string; color: string }
  evidence: { label: string; color: string }
}) {
  return <Descriptions title="产品状态" column={2} size="small">
    <Descriptions.Item label="生命周期">{statusTag(lifecycle)}</Descriptions.Item>
    <Descriptions.Item label="资产就绪"><Tag color={materialization.color}>{materialization.label}</Tag></Descriptions.Item>
    <Descriptions.Item label="服务/执行"><Tag color={service.color}>{service.label}</Tag></Descriptions.Item>
    <Descriptions.Item label="可信证据"><Tag color={evidence.color}>{evidence.label}</Tag></Descriptions.Item>
  </Descriptions>
}

function ModelTrustPassport({
  source,
  version,
  officialSourceUrl,
  purposes,
  validation,
  evidence,
  updatedAt,
}: {
  source: string
  version: string
  officialSourceUrl?: string | null
  purposes: string[]
  validation?: string
  evidence?: string
  updatedAt?: string | null
}) {
  const updatedLabel = formatEvidenceTime(updatedAt)
  return <Descriptions title="可信护照" column={2} size="small">
    <Descriptions.Item label="来源">{source}</Descriptions.Item>
    <Descriptions.Item label="版本">{version}</Descriptions.Item>
    {purposes.length > 0 && <Descriptions.Item label="许可/用途" span={2}>{purposes.map((item) => purposeLabels[item] || item).join('、')}</Descriptions.Item>}
    {validation && <Descriptions.Item label="平台验证" span={evidence ? 1 : 2}>{validation}</Descriptions.Item>}
    {evidence && <Descriptions.Item label="证据" span={validation ? 1 : 2}>{evidence}</Descriptions.Item>}
    {updatedLabel && <Descriptions.Item label="更新时间" span={officialSourceUrl ? 1 : 2}>{updatedLabel}</Descriptions.Item>}
    {officialSourceUrl && <Descriptions.Item label="官方来源" span={updatedLabel ? 1 : 2}><a href={officialSourceUrl} target="_blank" rel="noreferrer">查看来源</a></Descriptions.Item>}
  </Descriptions>
}

function PartnerModelShowcaseCard({ item }: { item: PartnerModelShowcase }) {
  return <Card
    className="phase51-catalog-item marketplace-card marketplace-card--showcase"
    tabIndex={0}
    title={<div className="marketplace-card-title"><span>{item.name}</span><Tag color="geekblue">合作展示</Tag></div>}
  >
    <div className="marketplace-card-summary">
      <Text type="secondary" className="marketplace-card-provider">{item.provider}</Text>
      <Paragraph ellipsis={{ rows: 2 }}>{item.summary}</Paragraph>
    </div>
    <div className="marketplace-card-details" aria-label={`${item.name} 接入方案`}>
      <Space wrap>
        <Tag>{item.focus}</Tag>
        <Tag>许可与调用方案</Tag>
        <Tag>待接入</Tag>
        {item.plannedModes.map((mode) => <Tag key={mode}>{serviceModeLabels[mode]} · 接入后开放</Tag>)}
      </Space>
    </div>
    <Divider />
    <div className="phase51-card-actions">
      <Popover
        trigger="click"
        placement="topRight"
        content={<Text>支持模型许可与受控调用方案，完成资产登记、审核和执行接入后开放。</Text>}
      >
        <Button icon={<FileSearchOutlined />}>了解方案</Button>
      </Popover>
      <Button type="primary" disabled>待接入</Button>
    </div>
  </Card>
}

function PageLoad({ loading, error, hasContent = false, children }: { loading: boolean; error: string; hasContent?: boolean; children: ReactNode }) {
  if (loading) return <Flex className="phase51-loading" justify="center" align="center"><Spin size="large" /></Flex>
  if (error && !hasContent) return <Alert type="error" showIcon title="页面加载失败" description={error} />
  return <>
    {error && <Alert type="error" showIcon title="刷新失败，已保留当前内容" description={error} />}
    {children}
  </>
}

function PageTitle({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return <div className="phase51-heading">
    <div><Title level={2}>{title}</Title>{description && <Paragraph>{description}</Paragraph>}</div>
    {actions && <Space wrap>{actions}</Space>}
  </div>
}

function useLoad<T>(path: string | null) {
  const { identity } = useRoadshow()
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(Boolean(path))
  const [initialized, setInitialized] = useState(false)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  const identityRef = useRef(identity)
  useEffect(() => {
    if (!path) return
    const identityChanged = identityRef.current !== identity
    const needLoading = !initialized || identityChanged
    if (identityChanged) setData(null)
    if (needLoading) setLoading(true)
    setError('')
    return startAbortableLoad(
      (signal) => platformGet<T>(path, identity, signal),
      {
        onSuccess: (value) => { setData(value); setError('') },
        onError: (reason) => setError(reason instanceof Error ? reason.message : '请求失败'),
        onSettled: () => {
          setLoading(false)
          setInitialized(true)
          identityRef.current = identity
        },
      },
    )
  }, [identity, path, nonce])
  return { data, loading, error, refresh: () => setNonce((value) => value + 1) }
}

export function ModelProductManagementPage() {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const operator = identity === 'space_operator'
  const provider = identity === 'model_provider'
  const state = useLoad<{ items: ModelDetail[] }>(
    operator ? '/model-product-review-queue' : '/model-product-management',
  )
  return <div className="page-stack">
    <PageTitle
      title={provider ? '我的模型产品' : operator ? '模型产品上架审核' : '模型产品目录'}
      description={provider
        ? '管理本机构的模型产品与上架进度。'
        : operator
          ? '审核待上架的模型产品。'
          : '查看当前空间已发布的模型产品。'}
      actions={<>
        <Button icon={<ReloadOutlined />} onClick={state.refresh}>刷新</Button>
        {provider && <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/model-products/new')}>新建模型产品</Button>}
      </>}
    />
    <PageLoad loading={state.loading} error={state.error} hasContent={state.data !== null}>
      {state.data?.items.length ? <Table
        rowKey="version_id"
        dataSource={state.data.items}
        pagination={{ pageSize: 8 }}
        columns={[
          { title: '模型', dataIndex: 'name', render: (value, item) => <div><strong>{value}</strong><Text type="secondary" className="phase51-code">{item.product_code}</Text></div> },
          { title: '提供方', dataIndex: 'provider' },
          { title: '任务', render: (_, item) => String(item.compatibility.task_description || item.compatibility.task_type || '') },
          { title: '版本', dataIndex: 'version_label', width: 90 },
          { title: '状态', dataIndex: 'status', width: 130, render: statusTag },
          { title: '操作', width: 110, render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => navigate(`/model-products/${item.version_id}`)}>{operator ? '审核' : '查看'}</Button> },
        ]}
      /> : <Empty description={operator ? '当前没有待审核模型' : '尚未创建模型产品'} />}
    </PageLoad>
  </div>
}

function detailToDraft(detail: ModelDetail): ModelProductDraft {
  const compatibility = detail.compatibility
  const license = detail.license
  const policy = detail.policy
  return {
    basic: {
      name: detail.name,
      short_name: String(compatibility.short_name || ''),
      team: String(compatibility.team || ''),
      task_type: String(compatibility.task_type || 'image_classification'),
      task_description: String(compatibility.task_description || ''),
      disease_domain: detail.domain,
      modality: String(compatibility.modality || ''),
      description: detail.description,
      source_type: String(license.source_type || 'platform_allowlisted'),
      model_owner: String(license.owner || ''),
      contact_department: String(license.contact_department || ''),
      is_demo: true,
      clinical_use: false,
    },
    runtime: {
      version_label: detail.version_label,
      version_notes: String(compatibility.version_notes || '固定白名单模型产品版本。'),
      framework: String(compatibility.framework || 'PyTorch'),
      runtime: detail.runtime,
      model_digest: detail.model_digest,
      entrypoint_id: detail.entrypoint_id,
      input_schema_version: detail.input_schema_version,
      output_schema_version: detail.output_schema_version,
      device: 'cpu',
      cpu_limit: Number((compatibility.resource_limits as Record<string, unknown>)?.cpu || 1),
      memory_limit_mb: Number((compatibility.resource_limits as Record<string, unknown>)?.memory_mb || 2048),
      timeout_seconds: Number((compatibility.resource_limits as Record<string, unknown>)?.timeout_seconds || 120),
      network_access: false,
      input_read_only: true,
      dynamic_dependencies: false,
      arbitrary_code: false,
      model_ready: true,
      executor_type: 'local_builtin',
    },
    schema: {
      input_schema: (compatibility.input_schema as Record<string, unknown>) || {},
      output_schema: (compatibility.output_schema as Record<string, unknown>) || {},
      allowed_outputs: (compatibility.allowed_outputs as string[]) || [],
      prohibited_outputs: (compatibility.prohibited_outputs as string[]) || [],
    },
    policy: {
      service_modes: (policy.service_modes as ModelProductDraft['policy']['service_modes'])?.length
        ? policy.service_modes as ModelProductDraft['policy']['service_modes']
        : ['controlled_compute'],
      allowed_purposes: (license.allowed_purposes as string[]) || [],
      prohibited_purposes: (license.prohibited_purposes as string[]) || [],
      max_runs: Number(policy.max_runs || 1),
      valid_days: Number(policy.valid_days || 1),
      multi_center_validation: Boolean(license.multi_center_validation),
      commercial_validation: Boolean(license.commercial_validation),
      research_publication: Boolean(license.research_publication),
      provider_result_confirmation: Boolean(license.provider_result_confirmation),
      model_download: false,
      reverse_engineering: false,
      redistribution: false,
      dynamic_script_execution: false,
      unauthorized_network: false,
    },
  }
}

const stepFields: Array<Array<Array<string>>> = [
  [['basic', 'name'], ['basic', 'team'], ['basic', 'task_type'], ['basic', 'task_description'], ['basic', 'disease_domain'], ['basic', 'modality'], ['basic', 'description'], ['basic', 'source_type'], ['basic', 'model_owner'], ['basic', 'contact_department']],
  [['runtime', 'version_label'], ['runtime', 'version_notes'], ['runtime', 'model_digest'], ['runtime', 'entrypoint_id'], ['runtime', 'model_ready']],
  [['schema', 'allowed_outputs'], ['policy', 'service_modes'], ['policy', 'allowed_purposes'], ['policy', 'max_runs'], ['policy', 'valid_days']],
  [['runtime', 'model_ready']],
]

export function ModelProductFormPage() {
  const { identity } = useRoadshow()
  const { versionId } = useParams()
  const navigate = useNavigate()
  const [form] = Form.useForm<ModelProductDraft>()
  const [step, setStep] = useState(0)
  const [assets, setAssets] = useState<ModelAsset[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [api, holder] = message.useMessage()
  const guard = useRef(createSingleFlight()).current
  const edit = useLoad<ModelDetail>(versionId ? `/model-product-versions/${versionId}` : null)

  useEffect(() => {
    if (identity !== 'model_provider') {
      setLoadError('只有模型提供方可以创建或编辑模型产品')
      setLoading(false)
      return
    }
    return startAbortableLoad(
      (signal) => platformGet<{ items: ModelAsset[] }>('/model-assets', identity, signal),
      {
        onSuccess: (value) => setAssets(value.items),
        onError: (reason) => setLoadError(reason instanceof Error ? reason.message : '固定模型资产加载失败'),
        onSettled: () => setLoading(false),
      },
    )
  }, [identity])

  useEffect(() => {
    if (loading) return
    if (edit.data) {
      if (edit.data.version_status !== 'draft') setLoadError('只有草稿状态可以编辑')
      form.setFieldsValue(detailToDraft(edit.data))
    } else if (!versionId) {
      form.setFieldValue(['policy', 'service_modes'], ['controlled_compute', 'model_artifact_license'])
    }
  }, [edit.data, form, loading, versionId])

  const fillSample = () => {
    if (!assets[0]) return
    form.setFieldsValue(buildPathmnistModelDraft(assets[0]))
  }

  const save = async (submitAfter: boolean) => {
    await guard.run(async () => {
      setSaving(true); setActionError('')
      try {
        await form.validateFields()
        const values = form.getFieldsValue(true) as ModelProductDraft
        const errors = validateModelBoundary(values)
        if (errors.length) throw new Error(errors.join('；'))
        const result = versionId
          ? await platformCommand<{ version_id: string; product_code: string }>(
            `/model-product-versions/${versionId}`, identity, `phase52-update-${secureUuid()}`, values, 'PATCH',
          )
          : await platformCommand<{ version_id: string; product_code: string }>(
            '/model-products', identity, `phase52-create-${secureUuid()}`, values,
          )
        if (submitAfter) {
          await platformCommand(`/model-product-versions/${result.version_id}/submit`, identity, `phase52-submit-${secureUuid()}`)
          api.success(`模型产品 ${result.product_code} 已提交上架审核`)
        } else {
          api.success(`模型产品草稿已保存，模型编号：${result.product_code}`)
        }
        navigate(`/model-products/${result.version_id}`)
      } catch (reason) {
        setActionError(reason instanceof Error ? reason.message : '保存失败')
      } finally { setSaving(false) }
    })
  }

  const next = async () => {
    try {
      await form.validateFields(stepFields[step])
      setStep((value) => Math.min(value + 1, 3))
    } catch { /* Field messages are rendered by Ant Form. */ }
  }

  const confirmSubmit = async () => {
    try {
      await form.validateFields()
      const modes = form.getFieldValue(['policy', 'service_modes']) as ModelProductDraft['policy']['service_modes']
      Modal.confirm({
        title: '确认模型产品的授权方式',
        content: <div>
          <Paragraph>本次上架将开放：</Paragraph>
          <Space wrap>{modes.map((mode) => <Tag color="blue" key={mode}>{serviceModeLabels[mode]}</Tag>)}</Space>
          <Paragraph type="secondary" className="phase51-form-alert">模型使用许可需单独审核与合同确认。</Paragraph>
        </div>,
        okText: '确认并提交审核',
        cancelText: '返回修改',
        onOk: () => save(true),
      })
    } catch { /* Ant Form displays field errors. */ }
  }

  return <div className="page-stack">
    {holder}
    <PageTitle
      title={versionId ? '编辑模型产品草稿' : '新建模型产品'}
      actions={<Button icon={<ArrowLeftOutlined />} onClick={() => navigate(versionId ? `/model-products/${versionId}` : '/model-products')}>返回</Button>}
    />
    <PageLoad loading={loading || edit.loading} error={loadError || edit.error}>
      <Steps current={step} items={[
        { title: '基本信息' }, { title: '版本与运行' }, { title: 'Schema 与许可' }, { title: '执行确认' },
      ]} />
      {actionError && <Alert type="error" showIcon title="操作未完成" description={actionError} />}
      <Form form={form} layout="vertical" className="phase51-form" requiredMark="optional">
        {step === 0 && <section className="phase51-section">
          <div className="phase51-section-head"><Title level={4}>模型基本信息</Title><Button onClick={fillSample}>填充 PathMNIST 模型样例</Button></div>
          <Row gutter={18}>
            <Col xs={24} md={16}><Form.Item name={['basic', 'name']} label="模型名称" rules={[{ required: true, min: 2 }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['basic', 'short_name']} label="模型简称"><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['basic', 'team']} label="所属团队" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['basic', 'task_type']} label="模型任务类型" rules={[{ required: true }]}><Select options={[
              ['image_classification', '图像分类'], ['lesion_detection', '病灶检测'], ['image_segmentation', '图像分割'], ['risk_prediction', '风险预测'], ['prognosis_prediction', '预后预测'], ['quality_control', '质量控制'], ['other', '其他'],
            ].map(([value, label]) => ({ value, label }))} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['basic', 'task_description']} label="模型任务" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['basic', 'disease_domain']} label="疾病领域" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['basic', 'modality']} label="适用模态" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['basic', 'source_type']} label="模型来源" rules={[{ required: true }]}><Select options={[
              { value: 'platform_allowlisted', label: '平台固定白名单模型' },
              { value: 'partner_preregistered', label: '合作方预登记模型' },
              { value: 'public_research', label: '公开研究模型' },
              { value: 'other', label: '其他' },
            ]} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['basic', 'model_owner']} label="模型负责人" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['basic', 'contact_department']} label="联系部门" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24}><Form.Item name={['basic', 'description']} label="模型简介" rules={[{ required: true, min: 20 }]}><Input.TextArea rows={4} /></Form.Item></Col>
          </Row>
        </section>}
        {step === 1 && <section className="phase51-section">
          <Title level={4}>模型版本与运行信息</Title>
          <Row gutter={18}>
            <Col xs={24} md={8}><Form.Item name={['runtime', 'version_label']} label="版本号" rules={[{ required: true, pattern: /^v[0-9]+(?:\.[0-9]+){0,2}$/ }]}><Input /></Form.Item></Col>
            <Col xs={24} md={16}><Form.Item name={['runtime', 'version_notes']} label="版本说明" rules={[{ required: true, min: 10 }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['runtime', 'framework']} label="模型框架" rules={[{ required: true }]}><Input readOnly /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['runtime', 'runtime']} label="运行时" rules={[{ required: true }]}><Input readOnly /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['runtime', 'device']} label="计算设备"><Input readOnly /></Form.Item></Col>
            <Col xs={24}><Form.Item name={['runtime', 'model_digest']} label="模型 digest" rules={[{ required: true, pattern: /^sha256:[0-9a-f]{64}$/ }]}><Input readOnly /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['runtime', 'entrypoint_id']} label="固定 entrypoint" rules={[{ required: true }]}><Input readOnly /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['runtime', 'executor_type']} label="执行器类型"><Input readOnly /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['runtime', 'cpu_limit']} label="CPU"><InputNumber readOnly className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['runtime', 'memory_limit_mb']} label="内存 MB"><InputNumber readOnly className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['runtime', 'timeout_seconds']} label="最长运行秒数"><InputNumber readOnly className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={6}><Form.Item name={['runtime', 'network_access']} label="允许网络" valuePropName="checked"><Switch disabled /></Form.Item></Col>
            <Col xs={24} md={6}><Form.Item name={['runtime', 'input_read_only']} label="输入只读" valuePropName="checked"><Switch disabled /></Form.Item></Col>
            <Col xs={24} md={6}><Form.Item name={['runtime', 'dynamic_dependencies']} label="动态依赖" valuePropName="checked"><Switch disabled /></Form.Item></Col>
            <Col xs={24} md={6}><Form.Item name={['runtime', 'model_ready']} label="模型资产 ready" valuePropName="checked"><Switch disabled /></Form.Item></Col>
          </Row>
        </section>}
        {step === 2 && <section className="phase51-section">
          <Title level={4}>输入输出 Schema 与许可策略</Title>
          <Form.Item
            name={['policy', 'service_modes']}
            label="可申请的模型服务"
            extra="可同时开放两种方式；每次申请仍需按策略审核。"
            rules={[{ required: true, type: 'array', min: 1, message: '至少选择一种模型服务' }]}
          >
            <Checkbox.Group options={[
              { value: 'controlled_compute', label: '同意模型参与受控调用计算' },
              { value: 'model_artifact_license', label: '同意接受模型使用许可申请' },
            ]} />
          </Form.Item>
          <Divider titlePlacement="start">受控计算约束</Divider>
          <Row gutter={18}>
            <Col xs={24} md={8}><Form.Item name={['schema', 'input_schema', 'width']} label="图像宽度"><InputNumber readOnly className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['schema', 'input_schema', 'height']} label="图像高度"><InputNumber readOnly className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['schema', 'input_schema', 'channels']} label="通道数"><InputNumber readOnly className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['runtime', 'input_schema_version']} label="输入 Schema 版本"><Input readOnly /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['runtime', 'output_schema_version']} label="输出 Schema 版本"><Input readOnly /></Form.Item></Col>
          </Row>
          <Form.Item name={['schema', 'allowed_outputs']} label="允许输出" rules={[{ required: true }]}>
            <Checkbox.Group options={[
              { value: 'aggregate_metrics', label: '聚合性能指标' },
              { value: 'confusion_matrix', label: '混淆矩阵' },
              { value: 'execution_summary', label: '执行摘要' },
            ]} />
          </Form.Item>
          <div className="phase51-deny-list"><strong>受控计算默认禁止输出</strong><Space wrap>{['模型权重', '中间特征', '原始输入图像', '任意脚本', '未批准样本级预测', '运行环境凭据'].map((item) => <Tag color="red" key={item}>{item}</Tag>)}</Space></div>
          <Form.Item name={['policy', 'allowed_purposes']} label="允许用途" rules={[{ required: true }]}><Checkbox.Group options={['科研验证', '外部性能验证', '教学演示']} /></Form.Item>
          <Row gutter={18}>
            <Col xs={24} md={8}><Form.Item name={['policy', 'max_runs']} label="最大运行次数" rules={[{ required: true }]}><InputNumber min={1} className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['policy', 'valid_days']} label="有效期（天）" rules={[{ required: true }]}><InputNumber min={1} className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['policy', 'provider_result_confirmation']} label="模型方结果确认" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['policy', 'multi_center_validation']} label="多中心验证" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['policy', 'commercial_validation']} label="商业验证" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['policy', 'research_publication']} label="研究发表" valuePropName="checked"><Switch /></Form.Item></Col>
          </Row>
        </section>}
        {step === 3 && <section className="phase51-section">
          <Title level={4}>执行能力绑定与提交确认</Title>
          <Descriptions bordered column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="执行器">平台本地固定白名单执行器</Descriptions.Item>
            <Descriptions.Item label="状态"><Tag color="green">ready</Tag></Descriptions.Item>
            <Descriptions.Item label="运行时">{form.getFieldValue(['runtime', 'runtime'])}</Descriptions.Item>
            <Descriptions.Item label="entrypoint">{form.getFieldValue(['runtime', 'entrypoint_id'])}</Descriptions.Item>
            <Descriptions.Item label="digest" span={2}>{form.getFieldValue(['runtime', 'model_digest'])}</Descriptions.Item>
          </Descriptions>
          <Divider />
        </section>}
      </Form>
      <div className="phase51-actions">
        <Button disabled={step === 0} onClick={() => setStep((value) => Math.max(0, value - 1))}>上一步</Button>
        <div />
        <Button icon={<SaveOutlined />} loading={saving} onClick={() => save(false)}>保存草稿</Button>
        {step < 3 ? <Button type="primary" onClick={next}>保存并继续</Button> : <Button type="primary" icon={<SendOutlined />} loading={saving} onClick={confirmSubmit}>提交上架审核</Button>}
      </div>
    </PageLoad>
  </div>
}

function EvidencePanel({ versionId }: { versionId: string }) {
  const state = useLoad<{ items: AuditItem[]; audit_chain_valid: boolean }>(
    `/model-product-versions/${versionId}/audit-events`,
  )
  const [selected, setSelected] = useState<AuditItem | null>(null)
  return <Card title="操作证据" extra={state.data && <Tag color={state.data.audit_chain_valid ? 'green' : 'red'}>{state.data.audit_chain_valid ? '审计链验证有效' : '审计链验证失败'}</Tag>}>
    <PageLoad loading={state.loading} error={state.error} hasContent={state.data !== null}>
      <Timeline items={(state.data?.items.slice(0, 5) || []).map((item) => ({
        color: item.event_type.endsWith('returned') ? 'red' : 'green',
        content: <button className="phase51-event-button" onClick={() => setSelected(item)}>
          <strong>{eventLabels[item.event_type] || item.event_type}</strong>
          <span>{new Date(item.occurred_at).toLocaleString()} · {item.actor}</span>
          <small>{item.state_before || '无'} → {item.state_after || '无'} · {item.event_id.slice(0, 8)}…</small>
        </button>,
      }))} />
      {!state.data?.items.length && <Empty description="暂无审计事件" />}
      <Link to={`/audit?subjectType=model_version&subjectId=${versionId}`}>查看完整审计链</Link>
    </PageLoad>
    <Drawer title="技术证据" size={560} open={Boolean(selected)} onClose={() => setSelected(null)}>
      {selected && <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="Event ID">{selected.event_id}</Descriptions.Item>
        <Descriptions.Item label="Object ID">{selected.subject_id}</Descriptions.Item>
        <Descriptions.Item label="执行主体">{selected.actor} · {selected.organization}</Descriptions.Item>
        <Descriptions.Item label="状态变化">{selected.state_before || '无'} → {selected.state_after || '无'}</Descriptions.Item>
        <Descriptions.Item label="Previous hash">{selected.previous_hash || 'Genesis'}</Descriptions.Item>
        <Descriptions.Item label="Current hash">{selected.current_hash}</Descriptions.Item>
        <Descriptions.Item label="Evidence digest">{selected.evidence_digest}</Descriptions.Item>
        <Descriptions.Item label="Correlation ID">{selected.correlation_id}</Descriptions.Item>
        <Descriptions.Item label="Outbox">{selected.outbox.map((item) => `${item.destination}: ${item.status}`).join('；') || '无'}</Descriptions.Item>
      </Descriptions>}
    </Drawer>
  </Card>
}

function OperatorReview({ detail, onChanged }: { detail: ModelDetail; onChanged: () => void }) {
  const { identity } = useRoadshow()
  const [form] = Form.useForm()
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [api, holder] = message.useMessage()
  const guard = useRef(createSingleFlight()).current
  if (identity !== 'space_operator' || detail.version_status !== 'under_review') return null
  const decide = async (action: 'approve' | 'return') => {
    await guard.run(async () => {
      setBusy(action); setError('')
      try {
        const values = await form.validateFields()
        await platformCommand(
          `/model-product-versions/${detail.version_id}/${action}`,
          identity,
          `phase52-review-${secureUuid()}`,
          { ...values, allow_catalog: action === 'approve' },
        )
        api.success(action === 'approve' ? '模型产品已批准并正式发布' : '模型产品已退回补充')
        onChanged()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '审核失败')
      } finally { setBusy('') }
    })
  }
  return <Card title="运营方模型上架审核">
    {holder}{error && <Alert type="error" showIcon title="审核未完成" description={error} />}
    <Form form={form} layout="vertical" initialValues={{ technical_risk: 'low', license_risk: 'low' }}>
      <Form.Item name="review_opinion" label="审核意见" rules={[{ required: true, min: 5 }]}><Input.TextArea rows={3} /></Form.Item>
      <Row gutter={16}>
        <Col xs={24} md={12}><Form.Item name="technical_risk" label="技术风险" rules={[{ required: true }]}><Radio.Group options={['low', 'medium', 'high'].map((value) => ({ value, label: value === 'low' ? '低' : value === 'medium' ? '中' : '高' }))} /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="license_risk" label="许可风险" rules={[{ required: true }]}><Radio.Group options={['low', 'medium', 'high'].map((value) => ({ value, label: value === 'low' ? '低' : value === 'medium' ? '中' : '高' }))} /></Form.Item></Col>
      </Row>
      <Form.Item name="additional_conditions" label="附加条件"><Input /></Form.Item>
      <Form.Item name="requested_materials" label="要求补充材料"><Input /></Form.Item>
      <Space>
        <Button danger loading={busy === 'return'} disabled={Boolean(busy)} onClick={() => decide('return')}>退回补充</Button>
        <Button type="primary" icon={<CheckCircleOutlined />} loading={busy === 'approve'} disabled={Boolean(busy)} onClick={() => decide('approve')}>批准并发布</Button>
      </Space>
    </Form>
  </Card>
}

export function ModelProductDetailPage() {
  const { identity } = useRoadshow()
  const { versionId = '' } = useParams()
  const navigate = useNavigate()
  const state = useLoad<ModelDetail>(versionId ? `/model-product-versions/${versionId}` : null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [authorizationOffering, setAuthorizationOffering] = useState<ServiceOffering | null>(null)
  const [api, holder] = message.useMessage()
  const guard = useRef(createSingleFlight()).current
  const detail = state.data
  const offerings = detail
    ? availableOfferings(detail.offerings, 'model', detail.external_source?.application_eligibility !== false)
    : []
  const submit = async () => {
    if (!detail) return
    await guard.run(async () => {
      setBusy(true); setError('')
      try {
        await platformCommand(`/model-product-versions/${detail.version_id}/submit`, identity, `phase52-submit-${secureUuid()}`)
        api.success(`模型 ${detail.product_code} 已提交上架审核`)
        state.refresh()
      } catch (reason) { setError(reason instanceof Error ? reason.message : '提交失败') }
      finally { setBusy(false) }
    })
  }
  const confirmSubmit = () => {
    const modes = ((detail?.policy.service_modes as ModelProductDraft['policy']['service_modes']) || ['controlled_compute'])
    Modal.confirm({
      title: '确认模型产品的授权方式',
      content: <Space wrap>{modes.map((mode) => <Tag color="blue" key={mode}>{serviceModeLabels[mode]}</Tag>)}</Space>,
      okText: '确认并提交审核',
      cancelText: '返回',
      onOk: submit,
    })
  }
  return <div className="page-stack">
    {holder}
    <PageLoad loading={state.loading} error={state.error} hasContent={state.data !== null}>
      {detail && <>
        <PageTitle
          title={detail.name}
          description={`${detail.product_code} · ${detail.provider} · ${detail.version_label} · ${detail.model_digest.slice(0, 18)}…`}
          actions={<>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(identity === 'data_requester' ? '/model-catalog' : '/model-products')}>返回列表</Button>
            {detail.allowed_actions.includes('edit') && <Button icon={<EditOutlined />} onClick={() => navigate(`/model-products/${detail.version_id}/edit`)}>编辑草稿</Button>}
            {detail.allowed_actions.includes('submit') && <Button type="primary" icon={<SendOutlined />} loading={busy} onClick={confirmSubmit}>提交上架审核</Button>}
            {identity === 'data_requester' && detail.status === 'published' && offerings.filter((offering) => offering.requestable).map((offering) => offering.mode === 'controlled_compute'
              ? <Button key={offering.mode} type="primary" icon={<SendOutlined />} onClick={() => navigate('/applications/new', {
                state: { productSelection: { modelVersionId: detail.version_id } },
              })}>申请调用</Button>
              : <Button key={offering.mode} type="primary" ghost icon={<SendOutlined />} onClick={() => setAuthorizationOffering(offering)}>申请授权</Button>)}
            <LifecycleActions
              targetType="model_product"
              productId={detail.product_id}
              allowedActions={detail.allowed_actions}
              current={detail.current_lifecycle_request}
              onChanged={state.refresh}
            />
          </>}
        />
        <DatasetModelEvidenceSummary productId={detail.product_id} direction="model-to-data" />
        {error && <Alert type="error" showIcon title="操作未完成" description={error} />}
        {detail.latest_return && detail.version_status === 'draft' && <Alert type="warning" showIcon title="运营方已退回补充" description={`${detail.latest_return.review_opinion} ${detail.latest_return.requested_materials || ''}`} />}
        <div className="phase51-detail-grid">
          <div className="phase51-detail-main">
            <section className="phase51-section">
              <Descriptions title="模型基本信息" bordered column={{ xs: 1, md: 2 }}>
                <Descriptions.Item label="当前状态">{statusTag(detail.status)}</Descriptions.Item>
                <Descriptions.Item label="模型任务">{String(detail.compatibility.task_description || '')}</Descriptions.Item>
                <Descriptions.Item label="疾病领域">{detail.domain}</Descriptions.Item>
                <Descriptions.Item label="适用模态">{String(detail.compatibility.modality || '')}</Descriptions.Item>
                <Descriptions.Item label="运行时">{detail.runtime}</Descriptions.Item>
                <Descriptions.Item label="模型简介" span={2}>{detail.description}</Descriptions.Item>
              </Descriptions>
            </section>
            <section className="phase51-section">
              {detail.external_source && <Descriptions title="外部来源与治理状态" bordered column={{ xs: 1, md: 2 }}>
                <Descriptions.Item label="上游提供方">{detail.external_source.upstream_provider || '上游未提供'}</Descriptions.Item>
                <Descriptions.Item label="治理审核">{detail.external_source.review_count}/12</Descriptions.Item>
                <Descriptions.Item label="权重状态">{materializationStatus(detail.external_source.materialization_status).label}</Descriptions.Item>
                <Descriptions.Item label="Executor">{detail.external_source.executor_registered ? '已注册' : '未注册'}</Descriptions.Item>
                <Descriptions.Item label="执行就绪">{executionReadinessLabel(detail.external_source.execution_readiness)}</Descriptions.Item>
                <Descriptions.Item label="平台验证">{modelValidationLabel(detail.external_source.platform_validation)}</Descriptions.Item>
                <Descriptions.Item label="申请资格">{detail.external_source.application_eligibility ? '可申请' : '不可申请'}</Descriptions.Item>
                <Descriptions.Item label="计算资格">{detail.external_source.compute_eligibility ? '可执行' : '不可执行'}</Descriptions.Item>
              </Descriptions>}
            </section>
            <section className="phase51-section">
              <LifecycleTimeline
                createdAt={detail.created_at}
                updatedAt={detail.updated_at}
                submittedAt={detail.submitted_at}
                approvedAt={detail.approved_at}
                publishedAt={detail.published_at}
                unpublishedAt={detail.unpublished_at}
                deletedAt={detail.deleted_at}
              />
            </section>
            <section className="phase51-section">
              <Descriptions title="固定版本与运行信息" bordered column={{ xs: 1, md: 2 }}>
                <Descriptions.Item label="entrypoint">{detail.entrypoint_id}</Descriptions.Item>
                <Descriptions.Item label="执行器">{String(detail.compatibility.executor_type || '')}</Descriptions.Item>
                <Descriptions.Item label="输入 Schema">{detail.input_schema_version}</Descriptions.Item>
                <Descriptions.Item label="输出 Schema">{detail.output_schema_version}</Descriptions.Item>
                <Descriptions.Item label="模型 digest" span={2}>{detail.model_digest}</Descriptions.Item>
              </Descriptions>
            </section>
            <section className="phase51-section">
              <Title level={4}>许可与输出</Title>
              <Space wrap className="phase51-catalog-offerings">
                {offerings.map((offering) => <Tag color={offeringTagColor(offering)} key={offering.mode}>{offeringLabel(offering)}</Tag>)}
              </Space>
              <CommercialOfferPreview productKind="model" versionId={detail.version_id} compact />
              <Space wrap>{((detail.license.allowed_purposes as string[]) || []).map((item) => <Tag color="purple" key={item}>{item}</Tag>)}</Space>
            </section>
            <OperatorReview detail={detail} onChanged={state.refresh} />
          </div>
          <aside className="phase51-detail-side">
            <Card>
              <FourAxisStatus
                lifecycle={detail.status}
                materialization={materializationStatus(detail.external_source?.materialization_status || 'materialized')}
                service={modelServiceStatus(
                  detail.external_source?.application_eligibility !== false,
                  detail.external_source?.execution_readiness || 'ready',
                  detail.external_source?.executor_registered !== false,
                )}
                evidence={modelEvidenceStatus(detail.external_source?.platform_validation, detail.snapshot_digest)}
              />
            </Card>
            <Card>
              <ModelTrustPassport
                source={detail.external_source?.upstream_provider || detail.provider}
                version={detail.version_label}
                officialSourceUrl={detail.external_source?.upstream_official_url}
                purposes={(detail.license.allowed_purposes as string[]) || []}
                validation={detail.external_source ? modelValidationLabel(detail.external_source.platform_validation) : undefined}
                evidence={detail.external_source?.governance_snapshot_digest ? '治理快照已固化' : detail.snapshot_digest ? '版本快照已固化' : undefined}
                updatedAt={detail.updated_at}
              />
            </Card>
            <EvidencePanel versionId={detail.version_id} />
          </aside>
        </div>
      </>}
    </PageLoad>
    <ServiceAuthorizationModal
      open={Boolean(authorizationOffering)}
      productKind="model"
      productName={detail?.name || ''}
      versionId={detail?.version_id || ''}
      offering={authorizationOffering}
      onCancel={() => setAuthorizationOffering(null)}
    />
  </div>
}

export function PublishedModelCatalogPage() {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const state = useLoad<{ items: ModelCatalogItem[] }>('/model-product-catalog')
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<string>()
  const [publicPage, setPublicPage] = useState(1)
  const [modeFilter, setModeFilter] = useState<'all' | ModelMarketplaceMode>('all')
  const [sourceFilter, setSourceFilter] = useState<ModelMarketplaceSourceFilter>('all')
  const [authorizationTarget, setAuthorizationTarget] = useState<{ item: ModelCatalogItem; offering: ServiceOffering } | null>(null)
  const [publicDetailTarget, setPublicDetailTarget] = useState<PublicModelCatalogItem | null>(null)
  const [publicDetail, setPublicDetail] = useState<PublicModelCatalogDetail | null>(null)
  const [publicDetailLoading, setPublicDetailLoading] = useState(false)
  const publicDetailRequestId = useRef(0)
  const debouncedQuery = useDebouncedModelCatalogText(query)
  const publicCatalogEnabled = (sourceFilter === 'all' || sourceFilter === 'public') && modeFilter === 'all'
  const publicCatalogPath = useMemo(() => {
    if (!publicCatalogEnabled) return null
    const parameters = new URLSearchParams({
      offset: String((publicPage - 1) * PUBLIC_MODEL_PAGE_SIZE),
      limit: String(PUBLIC_MODEL_PAGE_SIZE),
    })
    if (debouncedQuery.trim()) parameters.set('q', debouncedQuery.trim())
    if (category) parameters.set('category', category)
    return `/external-model-catalog/models?${parameters}`
  }, [category, debouncedQuery, publicCatalogEnabled, publicPage])
  const publicState = useLoad<{ items: PublicModelCatalogItem[]; total: number }>(publicCatalogPath)
  const items = (state.data?.items || []).filter((item) => {
    if (sourceFilter !== 'all' && modelMarketplaceSource(item).kind !== sourceFilter) return false
    if (modeFilter !== 'all' && !availableOfferings(item.offerings, 'model', item.application_eligibility).some((offering) => offering.mode === modeFilter)) return false
    const searchable = [item.name, item.product_code, item.provider, item.description, item.disease_domain, item.task_type, item.modality].join(' ').toLocaleLowerCase()
    if (debouncedQuery.trim() && !searchable.includes(debouncedQuery.trim().toLocaleLowerCase())) return false
    if (category && ![item.task_type, item.disease_domain, item.modality].some((value) => String(value || '').toLocaleLowerCase().includes(category.toLocaleLowerCase()))) return false
    return true
  })
  const showcases = partnerModelShowcases.filter((item) => (
    (sourceFilter === 'all' || sourceFilter === 'partner')
    && (modeFilter === 'all' || item.plannedModes.includes(modeFilter))
    && (!debouncedQuery.trim() || [item.name, item.provider, item.summary, item.focus].join(' ').toLocaleLowerCase().includes(debouncedQuery.trim().toLocaleLowerCase()))
    && !category
  ))
  const publicItems = publicCatalogEnabled
    ? (publicState.data?.items || []).filter((item) => !item.published_product_version_id)
    : []
  const filtersActive = modeFilter !== 'all' || sourceFilter !== 'all' || Boolean(query.trim() || category)
  const openPublicDetail = async (item: PublicModelCatalogItem) => {
    const requestId = ++publicDetailRequestId.current
    setPublicDetailTarget(item)
    setPublicDetail(null)
    setPublicDetailLoading(true)
    try {
      const detail = await platformGet<PublicModelCatalogDetail>(`/external-model-catalog/models/${item.id}`, identity)
      if (requestId === publicDetailRequestId.current) setPublicDetail(detail)
    } catch (reason) {
      if (requestId === publicDetailRequestId.current) message.error(reason instanceof Error ? reason.message : '目录详情加载失败')
    } finally {
      if (requestId === publicDetailRequestId.current) setPublicDetailLoading(false)
    }
  }
  const closePublicDetail = () => {
    publicDetailRequestId.current += 1
    setPublicDetailTarget(null)
    setPublicDetail(null)
    setPublicDetailLoading(false)
  }
  return <div className="page-stack">
    <PageTitle title="模型商城" description="已发布服务、公共模型目录与合作展示统一呈现；未接入资源仅供检索。" actions={<>
      <Button icon={<ReloadOutlined />} onClick={() => { state.refresh(); publicState.refresh() }}>刷新</Button>
    </>} />
    <Card size="small">
      <Space size={[8, 8]} wrap>
        <Tag color="green">正式产品 {state.data?.items.length ?? '—'} 项</Tag>
        <Tag color="blue">公共目录 {publicState.data?.total?.toLocaleString() ?? '—'} 项</Tag>
      </Space>
      <Divider />
      <Space wrap>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          aria-label="搜索模型名称"
          placeholder="搜索模型、任务或疾病"
          value={query}
          onChange={(event) => { setPublicPage(1); setQuery(event.target.value) }}
          className="phase51-catalog-filter"
        />
        <Select
          allowClear
          aria-label="按模型类别筛选"
          placeholder="全部模型类别"
          value={category}
          onChange={(value) => { setPublicPage(1); setCategory(value) }}
          options={['pathology_foundation', 'vision_language', 'spatial_transcriptomics', 'cell_segmentation', 'medical_segmentation'].map((value) => ({ value }))}
          className="phase51-catalog-filter"
        />
      <Select
        aria-label="按模型来源筛选"
        value={sourceFilter}
        onChange={(value) => { setPublicPage(1); setSourceFilter(value) }}
        options={[
          { value: 'all', label: '全部来源' },
          { value: 'public', label: '公共开源' },
          { value: 'provider', label: '机构模型' },
          { value: 'partner', label: '合作展示' },
        ]}
        className="phase51-catalog-filter"
      />
      <Select
        aria-label="按模型服务方式筛选"
        value={modeFilter}
        onChange={(value) => { setPublicPage(1); setModeFilter(value) }}
        options={[
          { value: 'all', label: '全部授权方式' },
          { value: 'controlled_compute', label: serviceModeLabels.controlled_compute },
          { value: 'model_artifact_license', label: serviceModeLabels.model_artifact_license },
        ]}
        className="phase51-catalog-filter"
      />
      </Space>
    </Card>
    <PageLoad loading={state.loading} error={state.error} hasContent={state.data !== null}>
      {publicState.error && publicCatalogEnabled && <Alert type="error" showIcon title="公共目录加载失败，已保留已发布模型" description={publicState.error} />}
      {publicCatalogEnabled && publicState.loading && publicState.data === null && <Flex className="phase51-loading" justify="center" align="center"><Spin /></Flex>}
      {items.length || showcases.length || publicItems.length ? <Row gutter={[16, 16]}>
        {items.map((item) => {
          const offerings = availableOfferings(item.offerings, 'model', item.application_eligibility)
          const source = modelMarketplaceSource(item)
          return <Col xs={24} lg={12} key={item.version_id}>
          <Card
            className="phase51-catalog-item marketplace-card"
            tabIndex={0}
            title={<div className="marketplace-card-title"><span>{item.name}</span><Tag color={source.color}>{source.label}</Tag></div>}
          >
            <div className="marketplace-card-summary">
              <Text type="secondary" className="marketplace-card-provider">{source.name}</Text>
              <Paragraph ellipsis={{ rows: 2 }}>{item.description}</Paragraph>
            </div>
            <CommercialOfferPreview productKind="model" versionId={item.version_id} compact />
            <div className="marketplace-card-details" aria-label="悬停查看模型产品详情">
              <Space wrap className="phase51-catalog-offerings">
                <Tag>{item.disease_domain || '通用医学'}</Tag>
                <Tag>{item.modality || '多模态'}</Tag>
                {offerings.map((offering) => <Tag color={offeringTagColor(offering)} key={offering.mode}>
                  {offeringLabel(offering)}{offering.requestable ? '' : '·暂不可申请'}
                </Tag>)}
              </Space>
            </div>
            <Divider />
            <div className="phase51-card-actions">
              <Link to={`/model-products/${item.version_id}`}><Button icon={<FileSearchOutlined />}>查看详情</Button></Link>
              {identity === 'data_requester' && offerings.filter((offering) => offering.requestable).map((offering) => offering.mode === 'controlled_compute'
                ? <Button key={offering.mode} type="primary" onClick={() => navigate('/applications/new', {
                  state: { productSelection: { modelVersionId: item.version_id } },
                })}>申请调用</Button>
                : <Button key={offering.mode} type="primary" ghost onClick={() => setAuthorizationTarget({ item, offering })}>申请授权</Button>)}
            </div>
          </Card>
        </Col>})}
        {showcases.map((item) => <Col xs={24} lg={12} key={item.key}>
          <PartnerModelShowcaseCard item={item} />
        </Col>)}
        {publicItems.map((item) => <Col xs={24} lg={12} key={item.id}>
          <PublicModelCatalogCard item={item} onOpen={(target) => void openPublicDetail(target)} />
        </Col>)}
      </Row> : <Empty description={filtersActive ? '当前没有匹配筛选条件的模型产品' : '当前没有已发布模型'}>
        {filtersActive && <Button onClick={() => { setQuery(''); setCategory(undefined); setModeFilter('all'); setSourceFilter('all'); setPublicPage(1) }}>清除筛选</Button>}
      </Empty>}
      {publicCatalogEnabled && publicState.data && publicState.data.total > PUBLIC_MODEL_PAGE_SIZE && <Flex justify="center">
        <Pagination
          current={publicPage}
          pageSize={PUBLIC_MODEL_PAGE_SIZE}
          total={publicState.data.total}
          showSizeChanger={false}
          showTotal={(total) => `公共目录共 ${total.toLocaleString()} 条`}
          onChange={setPublicPage}
        />
      </Flex>}
    </PageLoad>
    <ServiceAuthorizationModal
      open={Boolean(authorizationTarget)}
      productKind="model"
      productName={authorizationTarget?.item.name || ''}
      versionId={authorizationTarget?.item.version_id || ''}
      offering={authorizationTarget?.offering || null}
      onCancel={() => setAuthorizationTarget(null)}
    />
    <Drawer
      size="large"
      title={publicDetailTarget ? publicModelName(publicDetailTarget) : '公共模型目录详情'}
      open={Boolean(publicDetailTarget)}
      onClose={closePublicDetail}
    >
      {publicDetailLoading && <Flex className="phase51-loading" justify="center" align="center"><Spin /></Flex>}
      {publicDetail && <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="资源状态"><Space wrap><Tag color="blue">目录资源</Tag><Tag>待治理接入</Tag></Space></Descriptions.Item>
        <Descriptions.Item label="外部 ID">{publicDetail.external_model_id}</Descriptions.Item>
        <Descriptions.Item label="来源目录">{publicDetail.source_catalog}</Descriptions.Item>
        <Descriptions.Item label="上游提供方">{publicDetail.upstream_provider || '待补充'}</Descriptions.Item>
        <Descriptions.Item label="类别">{publicDetail.model_categories.join('、') || '待补充'}</Descriptions.Item>
        <Descriptions.Item label="模态 / 任务">{[...publicDetail.modalities, ...publicDetail.task_types].join('、') || '待补充'}</Descriptions.Item>
        <Descriptions.Item label="疾病 / 器官">{[...publicDetail.disease_areas, ...publicDetail.organs].join('、') || '待补充'}</Descriptions.Item>
        <Descriptions.Item label="框架 / 架构">{[publicDetail.framework, publicDetail.architecture].filter(Boolean).join(' · ') || '待补充'}</Descriptions.Item>
        <Descriptions.Item label="许可 / 权重">{publicDetail.license_name || publicDetail.license_status} · {publicDetail.weights_status}</Descriptions.Item>
        <Descriptions.Item label="用途">{publicDetail.intended_use_summary || '待补充'}</Descriptions.Item>
        <Descriptions.Item label="限制">{publicDetail.limitations_summary || '待补充'}</Descriptions.Item>
        <Descriptions.Item label="来源链接"><Space wrap>
          {publicDetail.paper_url && <a href={publicDetail.paper_url} target="_blank" rel="noreferrer noopener">论文</a>}
          {publicDetail.code_repository_url && <a href={publicDetail.code_repository_url} target="_blank" rel="noreferrer noopener">代码仓库</a>}
          {publicDetail.model_card_url && <a href={publicDetail.model_card_url} target="_blank" rel="noreferrer noopener">模型卡</a>}
          {!publicDetail.paper_url && !publicDetail.code_repository_url && !publicDetail.model_card_url && '待补充'}
        </Space></Descriptions.Item>
      </Descriptions>}
    </Drawer>
  </div>
}
