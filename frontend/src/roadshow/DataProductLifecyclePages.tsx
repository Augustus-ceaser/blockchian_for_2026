import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  EditOutlined,
  EyeOutlined,
  FileSearchOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
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
import { buildPublicDemoDraft, validateDraftBoundary, type DataProductDraft } from './dataProductForm'
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

type ConnectorItem = {
  id: string
  name: string
  organization: string
  runtime_status: string
  verification_status: string
  last_heartbeat_at: string
  capabilities: string[]
}

type DataServiceCapability = {
  service_mode: string
  service_mode_label: string
  requestability: string
  requestability_label: string
  runtime_availability: string
  runtime_availability_label: string
  evidence_at: string | null
  evaluated_at: string
}

type ProductDetail = {
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
  content_summary: string
  scope: Record<string, unknown>
  linkage: Record<string, unknown>
  quality: Record<string, unknown>
  policy: Record<string, unknown>
  provenance: Record<string, unknown>
  snapshot_digest: string
  created_at: string
  updated_at: string
  submitted_at: string | null
  approved_at: string | null
  published_at: string | null
  unpublished_at: string | null
  deleted_at: string | null
  service_capability: DataServiceCapability
  offerings?: ServiceOffering[]
  current_lifecycle_request: LifecycleRequest | null
  resource: null | {
    resource_identifier: string
    name: string
    modality: string
    format: string
    schema: Record<string, unknown>
    scope: Record<string, unknown>
    quality: Record<string, unknown>
    resource_digest: string
    connector: null | ConnectorItem
  }
  latest_return: null | {
    event_id: string
    review_opinion: string
    requested_materials: string
    risk_level: string
    occurred_at: string
  }
  allowed_actions: string[]
  capability: {
    hard_isolation: boolean
    raw_data_download: boolean
    clinical_use: boolean
  }
  external_metadata: null | {
    external_id: string
    catalog_version: string
    official_source_url: string
    upstream_rights_holder: string | null
    catalog_steward: string
    curator: string
    source_record_digest: string
    governance_snapshot_digest: string
    materialization_status: string
    data_holder_status: string
    redistribution_status: string
    execution_readiness: string
    application_eligibility: false
    record: null | {
      canonical_name: string
      modalities: string[]
      disease_areas: string[]
      organs: string[]
      sample_count: number | null
      patient_count: number | null
      file_count: number | null
      approximate_size_bytes: number | null
      data_formats: string[]
    }
    license_review: null | { decision: string; details: Record<string, unknown>; evidence_reference: string | null }
    access_review: null | { decision: string; details: Record<string, unknown>; evidence_reference: string | null }
  }
}

type DataCatalogItem = {
  product_id: string
  version_id: string
  version: string
  product_code: string
  name: string
  provider: string
  description: string
  disease_domain: string
  modality: string
  data_scale: Record<string, unknown>
  quality_summary: Record<string, unknown>
  allowed_purposes: string[]
  source_kind: string
  upstream_rights_holder: string | null
  materialization_status: string
  execution_readiness: string
  application_eligibility: boolean
  service_capability: DataServiceCapability
  official_source_url: string | null
  offerings?: ServiceOffering[]
}

type MarketplaceSourceFilter = 'all' | 'public' | 'provider'

type PublicDatasetCatalogItem = {
  id: string
  external_id: string
  canonical_name: string
  display_name_cn: string | null
  display_name_en: string | null
  source_catalog: string
  modalities: string[]
  disease_areas: string[]
  organs: string[]
  sample_count: number | null
  patient_count: number | null
  approximate_size_bytes: number | null
  license_name: string | null
  license_status: string
  access_level: string
  quality_flags: string[]
  published_product_version_id?: string | null
}

type PublicDatasetCatalogDetail = PublicDatasetCatalogItem & {
  official_source_name: string | null
  official_source_url: string | null
  catalog_source_url: string | null
  task_types: string[]
  species: string | null
  file_count: number | null
  data_formats: string[]
  registration_required: boolean | null
  dataset_version: string | null
}

const PUBLIC_DATA_PAGE_SIZE = 12
const CATALOG_SEARCH_DEBOUNCE_MS = 275

function useDebouncedText(value: string) {
  const [debouncedValue, setDebouncedValue] = useState(value)
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedValue(value), CATALOG_SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timeout)
  }, [value])
  return debouncedValue
}

function publicDatasetName(item: PublicDatasetCatalogItem) {
  return item.display_name_cn || item.display_name_en || item.canonical_name
}

function publicDatasetSummary(item: PublicDatasetCatalogItem) {
  const scope = [...item.disease_areas, ...item.organs].filter(Boolean).slice(0, 3).join('、')
  return scope || '公共医学数据目录元数据，等待完成治理与服务接入。'
}

function PublicDatasetCatalogCard({
  item,
  onOpen,
}: {
  item: PublicDatasetCatalogItem
  onOpen: (item: PublicDatasetCatalogItem) => void
}) {
  return <Card
    className="phase51-catalog-item marketplace-card"
    tabIndex={0}
    title={<div className="marketplace-card-title"><span>{publicDatasetName(item)}</span><Tag color="blue">目录资源</Tag></div>}
  >
    <div className="marketplace-card-summary">
      <Text type="secondary" className="marketplace-card-provider">{item.source_catalog || '公共数据目录'}</Text>
      <Paragraph ellipsis={{ rows: 2 }}>{publicDatasetSummary(item)}</Paragraph>
    </div>
    <div className="commerce-offer-list is-compact">
      <div className="commerce-offer-head"><Text type="secondary">服务状态</Text><Text strong>待治理接入</Text></div>
    </div>
    <div className="marketplace-card-details" aria-label="悬停查看公共数据目录信息">
      <Space wrap className="phase51-catalog-offerings">
        {(item.modalities.length ? item.modalities : ['模态待补充']).slice(0, 2).map((value) => <Tag key={value}>{value}</Tag>)}
        <Tag>{item.sample_count === null ? '样本数待补充' : `${item.sample_count.toLocaleString()} 样本`}</Tag>
        <Tag color={item.license_status === 'unknown' ? 'gold' : 'default'}>{item.license_name || item.license_status}</Tag>
      </Space>
    </div>
    <Divider />
    <div className="phase51-card-actions">
      <Button icon={<FileSearchOutlined />} onClick={() => onOpen(item)}>查看目录详情</Button>
    </div>
  </Card>
}

function dataMarketplaceSource(item: DataCatalogItem) {
  const isPublic = item.source_kind === 'external_public_metadata'
  return {
    kind: isPublic ? 'public' as const : 'provider' as const,
    label: isPublic ? '公共来源' : '机构自有',
    color: isPublic ? 'blue' : 'cyan',
    name: trustSourceLabel(item.upstream_rights_holder, item.provider, item.official_source_url),
  }
}

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
  evidence: Record<string, unknown>
  outbox: Array<{ message_id: string; destination: string; status: string }>
}

const stateLabels: Record<string, string> = {
  draft: '草稿',
  under_review: '上架审核中',
  approved: '已批准',
  published: '已发布',
  active: '已发布',
  unpublished: '已下架',
  archived: '已归档',
  returned: '已退回',
}

const eventLabels: Record<string, string> = {
  'data_product.version.created': '数据产品草稿创建',
  'data_product.version.updated': '数据产品草稿更新',
  'data_product.version.submitted': '提交上架审核',
  'data_product.version.returned': '上架审核退回',
  'data_product.version.approved': '上架审核批准',
  'data_product.version.published': '数据产品正式发布',
}

function statusTag(status: string) {
  const color = status === 'published' || status === 'active'
    ? 'green'
    : status === 'under_review'
      ? 'gold'
      : status === 'returned'
        ? 'red'
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

function trustSourceLabel(rightsHolder: string | null | undefined, provider: string, officialSourceUrl?: string | null) {
  if (rightsHolder && rightsHolder !== provider) return rightsHolder
  if (officialSourceUrl) {
    try {
      return new URL(officialSourceUrl).hostname.replace(/^www\./, '')
    } catch {
      // Keep the registered provider when the legacy source URL is malformed.
    }
  }
  return rightsHolder || provider
}

function materializationStatus(value: string) {
  if (value === 'materialized') return { label: '已物化', color: 'green' }
  if (value === 'metadata_only') return { label: '仅元数据', color: 'gold' }
  return { label: '未物化', color: 'default' }
}

function dataServiceStatus(capability: Partial<DataServiceCapability>, eligible: boolean) {
  if (!eligible || capability.requestability !== 'eligible') return { label: '不可申请', color: 'default' }
  if (capability.runtime_availability === 'ready') return { label: '可申请 · 执行就绪', color: 'cyan' }
  return { label: `可申请 · ${capability.runtime_availability_label || '待执行就绪'}`, color: 'gold' }
}

function dataEvidenceStatus(quality: Record<string, unknown>, capability: Partial<DataServiceCapability>, snapshotDigest?: string) {
  if (snapshotDigest) return { label: '版本证据已固化', color: 'green' }
  if (capability.evidence_at) return { label: '服务证据已记录', color: 'green' }
  const qualityStatus = String(quality.quality_status || '')
  if (qualityStatus === 'passed') return { label: '质量已通过', color: 'green' }
  if (qualityStatus === 'conditional') return { label: '质量有条件通过', color: 'gold' }
  return { label: '证据未标注', color: 'default' }
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

function DataTrustPassport({
  source,
  version,
  officialSourceUrl,
  purposes,
  quality,
  evidence,
  updatedAt,
}: {
  source: string
  version?: string
  officialSourceUrl?: string | null
  purposes: string[]
  quality: Record<string, unknown>
  evidence?: string
  updatedAt?: string | null
}) {
  const qualityStatus = String(quality.quality_status || '')
  const completeness = quality.completeness_rate
  const qualityLabel = [qualityStatus, completeness !== undefined && completeness !== null ? `完整率 ${Number(completeness)}%` : '']
    .filter(Boolean)
    .join(' · ')
  const updatedLabel = formatEvidenceTime(updatedAt)
  return <Descriptions title="可信护照" column={2} size="small">
    <Descriptions.Item label="来源">{source}</Descriptions.Item>
    <Descriptions.Item label="版本">{version || '—'}</Descriptions.Item>
    {purposes.length > 0 && <Descriptions.Item label="许可/用途" span={2}>{purposes.map((item) => purposeLabels[item] || item).join('、')}</Descriptions.Item>}
    <Descriptions.Item label="质量">{qualityLabel || '未登记'}</Descriptions.Item>
    <Descriptions.Item label="证据">{evidence || '未登记'}</Descriptions.Item>
    <Descriptions.Item label="更新时间">{updatedLabel || '—'}</Descriptions.Item>
    <Descriptions.Item label="官方来源">{officialSourceUrl ? <a href={officialSourceUrl} target="_blank" rel="noreferrer">查看来源</a> : '—'}</Descriptions.Item>
  </Descriptions>
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

export function DataProductManagementPage() {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const path = identity === 'space_operator' ? '/data-product-review-queue' : '/data-product-management'
  const state = useLoad<{ items: ProductDetail[]; total?: number }>(path)
  const hospital = identity === 'data_provider'
  const operator = identity === 'space_operator'
  return <div className="page-stack">
    <PageTitle
      title={hospital ? '医院数据产品管理' : operator ? '数据产品上架审核' : '数据产品目录'}
      description={hospital
        ? '管理本机构的数据产品与上架进度。'
        : operator
          ? '审核待上架的数据产品。'
          : '查看当前空间已发布的数据产品。'}
      actions={<>
        <Button icon={<ReloadOutlined />} onClick={state.refresh}>刷新</Button>
        {hospital && <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/data-products/new')}>新建数据产品</Button>}
      </>}
    />
    <PageLoad loading={state.loading} error={state.error} hasContent={state.data !== null}>
      {state.data?.items.length ? <Table
        rowKey="version_id"
        dataSource={state.data.items}
        pagination={{ pageSize: 8 }}
        columns={[
          { title: '产品', dataIndex: 'name', render: (value, item) => <div><strong>{value}</strong><Text type="secondary" className="phase51-code">{item.product_code}</Text></div> },
          { title: '提供机构', dataIndex: 'provider' },
          { title: '版本', dataIndex: 'version_label', width: 100 },
          { title: '状态', dataIndex: 'status', width: 130, render: statusTag },
          { title: '数据规模', render: (_, item) => `${Number(item.scope.image_count || 0)} 图像 / ${Number(item.scope.case_count || 0)} 病例` },
          { title: '操作', width: 120, render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => navigate(`/data-products/${item.version_id}`)}>{operator ? '审核' : '查看'}</Button> },
        ]}
      /> : <Empty description={operator ? '当前没有待审核的数据产品' : '尚未创建数据产品'} />}
    </PageLoad>
  </div>
}

const stepFields: Array<Array<Array<string>>> = [
  [
    ['basic', 'name'], ['basic', 'department'], ['basic', 'disease_domain'],
    ['basic', 'modality'], ['basic', 'source_type'], ['basic', 'description'],
    ['basic', 'data_owner'], ['basic', 'contact_department'],
  ],
  [
    ['composition', 'case_count'], ['composition', 'slide_count'], ['composition', 'image_count'],
    ['composition', 'data_format'], ['composition', 'image_specification'],
    ['composition', 'annotation_type'], ['composition', 'annotation_coverage'],
    ['composition', 'completeness_rate'], ['composition', 'quality_status'],
    ['composition', 'data_version'], ['composition', 'version_notes'],
    ['composition', 'resource_summary'],
  ],
  [
    ['policy', 'service_modes'], ['policy', 'allowed_purposes'], ['policy', 'max_runs'], ['policy', 'valid_days'],
    ['policy', 'allowed_outputs'],
  ],
  [
    ['binding', 'connector_id'], ['binding', 'resource_identifier'], ['binding', 'data_ready'],
  ],
]

function detailToDraft(detail: ProductDetail): DataProductDraft {
  const policy = detail.policy as DataProductDraft['policy']
  return {
    basic: {
      name: detail.name,
      short_name: String(detail.linkage.short_name || ''),
      department: String(detail.linkage.department || ''),
      disease_domain: detail.domain,
      modality: detail.resource?.modality || '',
      source_type: String(detail.linkage.source_type || 'public_demo_dataset') as DataProductDraft['basic']['source_type'],
      description: detail.description,
      data_owner: String(detail.linkage.data_owner || ''),
      contact_department: String(detail.linkage.contact_department || ''),
      is_demo: true,
    },
    composition: {
      case_count: Number(detail.scope.case_count || 0),
      slide_count: Number(detail.scope.slide_count || 0),
      image_count: Number(detail.scope.image_count || 0),
      data_format: detail.resource?.format || '',
      image_specification: String(detail.resource?.schema.image_specification || ''),
      annotation_type: String(detail.resource?.schema.annotation_type || ''),
      annotation_coverage: Number(detail.scope.annotation_coverage || 0),
      completeness_rate: Number(detail.quality.completeness_rate || 0),
      quality_status: String(detail.quality.quality_status || 'pending') as DataProductDraft['composition']['quality_status'],
      data_version: detail.version_label,
      version_notes: detail.content_summary,
      resource_summary: String(detail.quality.resource_summary || detail.resource?.name || ''),
    },
    policy: {
      service_modes: policy.service_modes?.length ? policy.service_modes : ['controlled_compute'],
      allowed_purposes: policy.allowed_purposes || [],
      prohibited_purposes: policy.prohibited_purposes || [],
      max_runs: Number(policy.max_runs || 1),
      valid_days: Number(policy.valid_days || 1),
      fixed_model_version: Boolean(policy.fixed_model_version),
      requires_egress_review: Boolean(policy.requires_egress_review),
      internet_allowed: Boolean(policy.internet_allowed),
      input_read_only: Boolean(policy.input_read_only),
      allowed_outputs: policy.allowed_outputs || [],
      prohibited_outputs: policy.prohibited_outputs || [],
      hard_isolation: false,
    },
    binding: {
      connector_id: String(detail.linkage.connector_id || ''),
      resource_identifier: String(detail.linkage.resource_identifier || ''),
      data_ready: Boolean(detail.linkage.data_ready),
    },
  }
}

export function DataProductFormPage() {
  const { identity } = useRoadshow()
  const { versionId } = useParams()
  const navigate = useNavigate()
  const [form] = Form.useForm<DataProductDraft>()
  const [step, setStep] = useState(0)
  const [connectors, setConnectors] = useState<ConnectorItem[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [api, holder] = message.useMessage()
  const writeGuard = useRef(createSingleFlight()).current
  const editState = useLoad<ProductDetail>(versionId ? `/data-product-versions/${versionId}` : null)

  useEffect(() => {
    if (identity !== 'data_provider') {
      setLoadError('只有医院数据方可以创建或编辑数据产品')
      setLoading(false)
      return
    }
    return startAbortableLoad(
      (signal) => platformGet<{ items: ConnectorItem[] }>('/data-product-connectors', identity, signal),
      {
        onSuccess: (value) => {
          setConnectors(value.items)
        },
        onError: (reason) => setLoadError(reason instanceof Error ? reason.message : 'Connector 加载失败'),
        onSettled: () => setLoading(false),
      },
    )
  }, [form, identity, versionId])

  useEffect(() => {
    if (loading) return
    if (editState.data) {
      if (editState.data.version_status !== 'draft') setLoadError('只有草稿状态可以编辑')
      form.setFieldsValue(detailToDraft(editState.data))
    } else if (!versionId) {
      form.setFieldsValue({
        basic: { is_demo: true },
        policy: {
          service_modes: ['controlled_compute', 'deidentified_data_delivery'],
          prohibited_purposes: ['临床诊断', '未授权模型训练', '二次分发', '患者识别', '超出合同范围使用'],
          prohibited_outputs: ['原始图像', '样本级预测', '原始特征', '模型权重', '执行脚本', 'Connector 凭据'],
          fixed_model_version: true,
          requires_egress_review: true,
          internet_allowed: false,
          input_read_only: true,
          hard_isolation: false,
        },
        binding: {
          connector_id: connectors[0]?.id || '',
          data_ready: false,
        },
      } as DataProductDraft)
    }
  }, [connectors, editState.data, form, loading, versionId])

  const fillDemo = () => form.setFieldsValue(buildPublicDemoDraft(
    form.getFieldValue(['binding', 'connector_id']) || connectors[0]?.id || '',
  ))

  const save = async (submitAfter: boolean) => {
    await writeGuard.run(async () => {
      setSaving(true); setActionError('')
      try {
        await form.validateFields()
        const values = form.getFieldsValue(true) as DataProductDraft
        const boundaryErrors = validateDraftBoundary(values)
        if (boundaryErrors.length) throw new Error(boundaryErrors.join('；'))
        const key = `phase51-ui-${secureUuid()}`
        const result = versionId
          ? await platformCommand<{ version_id: string; product_code: string; status: string }>(
            `/data-product-versions/${versionId}`, identity, key, values, 'PATCH',
          )
          : await platformCommand<{ version_id: string; product_code: string; status: string }>(
            '/data-products', identity, key, values,
          )
        if (submitAfter) {
          await platformCommand(
            `/data-product-versions/${result.version_id}/submit`,
            identity,
            `phase51-ui-submit-${secureUuid()}`,
          )
          api.success(`数据产品 ${result.product_code} 已提交上架审核`)
        } else {
          api.success(`数据产品草稿已保存，产品编号：${result.product_code}`)
        }
        navigate(`/data-products/${result.version_id}`)
      } catch (reason) {
        setActionError(reason instanceof Error ? reason.message : '保存失败')
      } finally {
        setSaving(false)
      }
    })
  }

  const next = async () => {
    try {
      await form.validateFields(stepFields[step])
      setStep((value) => Math.min(value + 1, 3))
    } catch { /* Ant Form displays field errors. */ }
  }

  const confirmSubmit = async () => {
    try {
      await form.validateFields()
      const modes = form.getFieldValue(['policy', 'service_modes']) as DataProductDraft['policy']['service_modes']
      Modal.confirm({
        title: '确认数据产品的授权方式',
        content: <div>
          <Paragraph>本次上架将开放：</Paragraph>
          <Space wrap>{modes.map((mode) => <Tag color="blue" key={mode}>{serviceModeLabels[mode]}</Tag>)}</Space>
          <Paragraph type="secondary" className="phase51-form-alert">匿名化数据授权交付需另行审核与合同确认。</Paragraph>
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
      title={versionId ? '编辑数据产品草稿' : '新建数据产品'}
      actions={<Button icon={<ArrowLeftOutlined />} onClick={() => navigate(versionId ? `/data-products/${versionId}` : '/data-products')}>返回</Button>}
    />
    <PageLoad loading={loading || editState.loading} error={loadError || editState.error}>
      <Steps current={step} items={[
        { title: '基本信息' }, { title: '构成与质量' }, { title: '策略与输出' }, { title: '节点与确认' },
      ]} />
      {actionError && <Alert type="error" showIcon title="操作未完成" description={actionError} />}
      <Form form={form} layout="vertical" className="phase51-form" requiredMark="optional">
        {step === 0 && <section className="phase51-section">
          <div className="phase51-section-head"><Title level={4}>基本信息</Title><Button onClick={fillDemo}>填充示例</Button></div>
          <Row gutter={18}>
            <Col xs={24} md={16}><Form.Item name={['basic', 'name']} label="产品名称" rules={[{ required: true, min: 2 }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['basic', 'short_name']} label="产品简称"><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['basic', 'department']} label="所属部门" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['basic', 'disease_domain']} label="疾病领域" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['basic', 'modality']} label="数据模态" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['basic', 'source_type']} label="数据来源类型" rules={[{ required: true }]}><Select options={[
              { value: 'public_demo_dataset', label: '公开数据集' },
              { value: 'hospital_research_data', label: '医院科研数据（仅元数据登记）' },
              { value: 'multicenter_collaboration', label: '多中心合作数据（仅元数据登记）' },
              { value: 'other', label: '其他' },
            ]} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['basic', 'data_owner']} label="数据负责人" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['basic', 'contact_department']} label="联系部门" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24}><Form.Item name={['basic', 'description']} label="产品简介" rules={[{ required: true, min: 20 }]}><Input.TextArea rows={4} /></Form.Item></Col>
          </Row>
        </section>}
        {step === 1 && <section className="phase51-section">
          <Title level={4}>数据构成与质量</Title>
          <Row gutter={18}>
            <Col xs={24} md={8}><Form.Item name={['composition', 'case_count']} label="病例数量" rules={[{ required: true }]}><InputNumber min={0} className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['composition', 'slide_count']} label="切片数量" rules={[{ required: true }]}><InputNumber min={0} className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['composition', 'image_count']} label="图像数量" rules={[{ required: true }]}><InputNumber min={0} className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['composition', 'data_format']} label="数据格式" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['composition', 'image_specification']} label="图像尺寸/分辨率" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['composition', 'annotation_type']} label="标注类型" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['composition', 'annotation_coverage']} label="标注覆盖率 (%)" rules={[{ required: true }]}><InputNumber min={0} max={100} className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['composition', 'completeness_rate']} label="数据完整率 (%)" rules={[{ required: true }]}><InputNumber min={0} max={100} className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['composition', 'quality_status']} label="质量检查状态" rules={[{ required: true }]}><Select options={[
              { value: 'pending', label: '待检查' }, { value: 'passed', label: '已通过' }, { value: 'conditional', label: '有条件通过' },
            ]} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['composition', 'data_version']} label="数据版本号" rules={[{ required: true, pattern: /^v[0-9]+(?:\.[0-9]+){0,2}$/ }]}><Input /></Form.Item></Col>
            <Col xs={24} md={16}><Form.Item name={['composition', 'version_notes']} label="版本说明" rules={[{ required: true, min: 10 }]}><Input /></Form.Item></Col>
            <Col xs={24}><Form.Item name={['composition', 'resource_summary']} label="数据摘要/资源摘要" rules={[{ required: true, min: 10 }]}><Input.TextArea rows={3} /></Form.Item></Col>
          </Row>
        </section>}
        {step === 2 && <section className="phase51-section">
          <Title level={4}>使用策略与输出</Title>
          <Form.Item
            name={['policy', 'service_modes']}
            label="可申请的数据服务"
            extra="可同时开放两种方式；每次申请仍需按策略审核。"
            rules={[{ required: true, type: 'array', min: 1, message: '至少选择一种数据服务' }]}
          >
            <Checkbox.Group options={[
              { value: 'controlled_compute', label: '同意在受控环境中调用计算' },
              { value: 'deidentified_data_delivery', label: '同意接受匿名化数据授权交付申请' },
            ]} />
          </Form.Item>
          <Form.Item name={['policy', 'allowed_purposes']} label="允许用途" rules={[{ required: true }]}>
            <Checkbox.Group options={[
              { value: 'research_analysis', label: '科研分析' },
              { value: 'model_validation', label: '模型验证' },
              { value: 'external_performance_validation', label: '外部性能验证' },
              { value: 'teaching_demo', label: '教学演示' },
            ]} />
          </Form.Item>
          <div className="phase51-deny-list"><strong>禁止用途</strong><Space wrap>{['临床诊断', '未授权模型训练', '二次分发', '患者识别', '超出合同范围使用'].map((item) => <Tag color="red" key={item}>{item}</Tag>)}</Space></div>
          <Divider titlePlacement="start">受控计算约束</Divider>
          <Row gutter={18}>
            <Col xs={24} md={8}><Form.Item name={['policy', 'max_runs']} label="最大运行次数" rules={[{ required: true }]}><InputNumber min={1} className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['policy', 'valid_days']} label="有效期（天）" rules={[{ required: true }]}><InputNumber min={1} className="phase51-full" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['policy', 'fixed_model_version']} label="固定模型版本" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['policy', 'requires_egress_review']} label="医院结果出域审批" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['policy', 'internet_allowed']} label="允许外网" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name={['policy', 'input_read_only']} label="输入只读" valuePropName="checked"><Switch /></Form.Item></Col>
          </Row>
          <Form.Item name={['policy', 'allowed_outputs']} label="允许输出" rules={[{ required: true }]}>
            <Checkbox.Group options={[
              { value: 'aggregate_metrics', label: '聚合性能指标' },
              { value: 'confusion_matrix', label: '混淆矩阵' },
              { value: 'execution_summary', label: '执行摘要' },
            ]} />
          </Form.Item>
          <div className="phase51-deny-list"><strong>受控计算默认禁止输出</strong><Space wrap>{['原始图像', '模型权重', 'Connector 凭据', '样本级预测', '原始特征', '执行脚本'].map((item) => <Tag color="red" key={item}>{item}</Tag>)}</Space></div>
        </section>}
        {step === 3 && <section className="phase51-section">
          <Title level={4}>节点绑定与提交确认</Title>
          <Form.Item name={['binding', 'connector_id']} label="医院 Connector" rules={[{ required: true }]}>
            <Select options={connectors.map((item) => ({ value: item.id, label: `${item.name} · ${item.runtime_status}` }))} />
          </Form.Item>
          <Form.Item name={['binding', 'resource_identifier']} label="资源标识" extra="仅允许元数据标识，不接受文件路径、URL 或凭据。" rules={[
            { required: true },
            { pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$/, message: '仅允许 3-64 位字母、数字、点、下划线或短横线' },
          ]}><Input /></Form.Item>
          <Form.Item name={['binding', 'data_ready']} valuePropName="checked" rules={[{ validator: (_, value) => value ? Promise.resolve() : Promise.reject(new Error('提交前必须确认数据已就绪')) }]}>
            <Checkbox>确认该数据范围已在医院 Connector 侧就绪</Checkbox>
          </Form.Item>
        </section>}
      </Form>
      <div className="phase51-actions">
        <Button disabled={step === 0} onClick={() => setStep((value) => Math.max(0, value - 1))}>上一步</Button>
        <div />
        <Button icon={<SaveOutlined />} loading={saving} onClick={() => save(false)}>保存草稿</Button>
        {step < 3
          ? <Button type="primary" onClick={next}>保存并继续</Button>
          : <Button type="primary" icon={<SendOutlined />} loading={saving} onClick={confirmSubmit}>提交上架审核</Button>}
      </div>
    </PageLoad>
  </div>
}

function EvidencePanel({ versionId }: { versionId: string }) {
  const state = useLoad<{ items: AuditItem[]; audit_chain_valid: boolean; total: number }>(
    `/data-product-versions/${versionId}/audit-events`,
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
      <Link to={`/audit?subjectType=data_product_version&subjectId=${versionId}`}>查看完整审计链</Link>
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

function OperatorReview({ detail, onChanged }: { detail: ProductDetail; onChanged: () => void }) {
  const { identity } = useRoadshow()
  if (identity !== 'space_operator' || detail.version_status !== 'under_review') return null
  return <OperatorReviewForm detail={detail} onChanged={onChanged} identity={identity} />
}

function OperatorReviewForm({
  detail,
  onChanged,
  identity,
}: {
  detail: ProductDetail
  onChanged: () => void
  identity: 'space_operator'
}) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [api, holder] = message.useMessage()
  const guard = useRef(createSingleFlight()).current
  const decide = async (action: 'approve' | 'return') => {
    await guard.run(async () => {
      setBusy(action); setError('')
      try {
        const values = await form.validateFields()
        await platformCommand(
          `/data-product-versions/${detail.version_id}/${action}`,
          identity,
          `phase51-review-${secureUuid()}`,
          { ...values, allow_catalog: action === 'approve' },
        )
        api.success(action === 'approve' ? '数据产品已批准并正式发布' : '数据产品已退回医院补充')
        onChanged()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '审核失败')
      } finally { setBusy('') }
    })
  }
  return <Card title="运营方上架审核">
    {holder}{error && <Alert type="error" showIcon title="审核未完成" description={error} />}
    <Form form={form} layout="vertical" initialValues={{ risk_level: 'low' }}>
      <Form.Item name="review_opinion" label="审核意见" rules={[{ required: true, min: 5 }]}><Input.TextArea rows={3} /></Form.Item>
      <Row gutter={16}>
        <Col xs={24} md={12}><Form.Item name="risk_level" label="风险等级" rules={[{ required: true }]}><Radio.Group options={[
          { value: 'low', label: '低' }, { value: 'medium', label: '中' }, { value: 'high', label: '高' },
        ]} /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="additional_conditions" label="附加条件"><Input /></Form.Item></Col>
      </Row>
      <Form.Item name="requested_materials" label="要求补充材料"><Input /></Form.Item>
      <Space>
        <Button danger loading={busy === 'return'} disabled={Boolean(busy)} onClick={() => decide('return')}>退回补充</Button>
        <Button type="primary" icon={<CheckCircleOutlined />} loading={busy === 'approve'} disabled={Boolean(busy)} onClick={() => decide('approve')}>批准并发布</Button>
      </Space>
    </Form>
  </Card>
}

export function DataProductDetailPage() {
  const { identity } = useRoadshow()
  const { versionId = '' } = useParams()
  const navigate = useNavigate()
  const state = useLoad<ProductDetail>(versionId ? `/data-product-versions/${versionId}` : null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [authorizationOffering, setAuthorizationOffering] = useState<ServiceOffering | null>(null)
  const [api, holder] = message.useMessage()
  const guard = useRef(createSingleFlight()).current
  const detail = state.data
  const offerings = detail
    ? availableOfferings(detail.offerings, 'data', detail.service_capability.requestability === 'eligible')
    : []
  const submit = async () => {
    if (!detail) return
    await guard.run(async () => {
      setBusy(true); setError('')
      try {
        await platformCommand(`/data-product-versions/${detail.version_id}/submit`, identity, `phase51-submit-${secureUuid()}`)
        api.success(`产品 ${detail.product_code} 已提交上架审核`)
        state.refresh()
      } catch (reason) { setError(reason instanceof Error ? reason.message : '提交失败') }
      finally { setBusy(false) }
    })
  }
  const confirmSubmit = () => {
    const modes = ((detail?.policy.service_modes as DataProductDraft['policy']['service_modes']) || ['controlled_compute'])
    Modal.confirm({
      title: '确认数据产品的授权方式',
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
          description={`${detail.product_code} · ${detail.provider} · ${detail.version_label}`}
          actions={<>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(identity === 'data_requester' ? '/data-catalog' : '/data-products')}>返回列表</Button>
            {detail.allowed_actions.includes('edit') && <Button icon={<EditOutlined />} onClick={() => navigate(`/data-products/${detail.version_id}/edit`)}>编辑草稿</Button>}
            {detail.allowed_actions.includes('submit') && <Button type="primary" icon={<SendOutlined />} loading={busy} onClick={confirmSubmit}>提交上架审核</Button>}
            {identity === 'data_requester' && detail.status === 'published' && offerings.filter((offering) => offering.requestable).map((offering) => offering.mode === 'controlled_compute'
              ? <Button key={offering.mode} type="primary" icon={<SendOutlined />} onClick={() => navigate('/applications/new', {
                state: { productSelection: { dataVersionId: detail.version_id } },
              })}>申请调用</Button>
              : <Button key={offering.mode} type="primary" ghost icon={<SendOutlined />} onClick={() => setAuthorizationOffering(offering)}>申请授权</Button>)}
            <LifecycleActions
              targetType="data_product"
              productId={detail.product_id}
              allowedActions={detail.allowed_actions}
              current={detail.current_lifecycle_request}
              onChanged={state.refresh}
            />
          </>}
        />
        <DatasetModelEvidenceSummary productId={detail.product_id} direction="data-to-models" />
        {error && <Alert type="error" showIcon title="操作未完成" description={error} />}
        {detail.latest_return && detail.version_status === 'draft' && <Alert type="warning" showIcon title="运营方已退回补充" description={`${detail.latest_return.review_opinion} ${detail.latest_return.requested_materials || ''}`} />}
        {detail.external_metadata && <section className="phase51-section">
          <Descriptions bordered column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="外部 ID">{detail.external_metadata.external_id}</Descriptions.Item>
            <Descriptions.Item label="目录版本">{detail.external_metadata.catalog_version}</Descriptions.Item>
            <Descriptions.Item label="上游权利主体">{detail.external_metadata.upstream_rights_holder || '未知 / 上游未提供'}</Descriptions.Item>
            <Descriptions.Item label="物化状态">{materializationStatus(detail.external_metadata.materialization_status).label}</Descriptions.Item>
            <Descriptions.Item label="执行状态">{detail.service_capability.runtime_availability_label}</Descriptions.Item>
            <Descriptions.Item label="申请资格">{detail.service_capability.requestability_label}</Descriptions.Item>
            <Descriptions.Item label="官方来源" span={2}><a href={detail.external_metadata.official_source_url} target="_blank" rel="noreferrer">查看上游官方页面</a></Descriptions.Item>
            <Descriptions.Item label="来源摘要" span={2}><Text code>{detail.external_metadata.source_record_digest}</Text></Descriptions.Item>
            <Descriptions.Item label="治理摘要" span={2}><Text code>{detail.external_metadata.governance_snapshot_digest}</Text></Descriptions.Item>
          </Descriptions>
        </section>}
        <div className="phase51-detail-grid">
          <div className="phase51-detail-main">
            <section className="phase51-section">
              <Descriptions title="基本信息" bordered column={{ xs: 1, md: 2 }}>
                <Descriptions.Item label="当前状态">{statusTag(detail.status)}</Descriptions.Item>
                <Descriptions.Item label="疾病领域">{detail.domain}</Descriptions.Item>
                <Descriptions.Item label="数据模态">{detail.resource?.modality}</Descriptions.Item>
                <Descriptions.Item label="来源类型">{String(detail.linkage.source_type || '')}</Descriptions.Item>
                <Descriptions.Item label="所属部门">{String(detail.linkage.department || '')}</Descriptions.Item>
                <Descriptions.Item label="数据负责人">{String(detail.linkage.data_owner || '')}</Descriptions.Item>
                <Descriptions.Item label="产品简介" span={2}>{detail.description}</Descriptions.Item>
              </Descriptions>
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
              <Descriptions title="数据构成与质量" bordered column={{ xs: 1, md: 3 }}>
                <Descriptions.Item label="病例">{Number(detail.scope.case_count || 0)}</Descriptions.Item>
                <Descriptions.Item label="切片">{Number(detail.scope.slide_count || 0)}</Descriptions.Item>
                <Descriptions.Item label="图像">{Number(detail.scope.image_count || 0)}</Descriptions.Item>
                <Descriptions.Item label="格式">{detail.resource?.format}</Descriptions.Item>
                <Descriptions.Item label="完整率">{Number(detail.quality.completeness_rate || 0)}%</Descriptions.Item>
                <Descriptions.Item label="质量状态">{String(detail.quality.quality_status || '')}</Descriptions.Item>
                <Descriptions.Item label="资源摘要" span={3}>{String(detail.quality.resource_summary || '')}</Descriptions.Item>
              </Descriptions>
            </section>
            <section className="phase51-section">
              <Title level={4}>使用策略与输出</Title>
              <Space wrap className="phase51-catalog-offerings">
                {offerings.map((offering) => <Tag color={offeringTagColor(offering)} key={offering.mode}>{offeringLabel(offering)}</Tag>)}
              </Space>
              <CommercialOfferPreview productKind="data" versionId={detail.version_id} compact />
              <Row gutter={16}>
                <Col xs={24} md={12}><Title level={5}>允许用途</Title><Flex vertical gap={8}>{((detail.policy.allowed_purposes as string[]) || []).map((item) => <div key={item}><CheckCircleOutlined className="text-success" /> {item}</div>)}</Flex></Col>
                <Col xs={24} md={12}><Title level={5}>允许输出</Title><Flex vertical gap={8}>{((detail.policy.allowed_outputs as string[]) || []).map((item) => <div key={item}><SafetyCertificateOutlined /> {item}</div>)}</Flex></Col>
              </Row>
            </section>
            <OperatorReview detail={detail} onChanged={state.refresh} />
          </div>
          <aside className="phase51-detail-side">
            <Card>
              <FourAxisStatus
                lifecycle={detail.status}
                materialization={materializationStatus(detail.external_metadata?.materialization_status || 'materialized')}
                service={dataServiceStatus(detail.service_capability, detail.service_capability.requestability === 'eligible')}
                evidence={dataEvidenceStatus(detail.quality, detail.service_capability, detail.snapshot_digest)}
              />
            </Card>
            <Card>
              <DataTrustPassport
                source={trustSourceLabel(detail.external_metadata?.upstream_rights_holder, detail.provider, detail.external_metadata?.official_source_url)}
                version={detail.version_label}
                officialSourceUrl={detail.external_metadata?.official_source_url}
                purposes={(detail.policy.allowed_purposes as string[]) || []}
                quality={detail.quality}
                evidence={detail.external_metadata?.governance_snapshot_digest ? '治理快照已固化' : detail.snapshot_digest ? '版本快照已固化' : undefined}
                updatedAt={detail.updated_at}
              />
            </Card>
            <Card title="Connector 绑定">
              {detail.resource?.connector ? <Descriptions column={1} size="small">
                <Descriptions.Item label="节点">{detail.resource.connector.name}</Descriptions.Item>
                <Descriptions.Item label="在线状态"><Tag color="green">{detail.resource.connector.runtime_status}</Tag></Descriptions.Item>
                <Descriptions.Item label="资源标识">{detail.resource.resource_identifier}</Descriptions.Item>
              </Descriptions> : <Empty description="未绑定" />}
            </Card>
            <EvidencePanel versionId={detail.version_id} />
          </aside>
        </div>
      </>}
    </PageLoad>
    <ServiceAuthorizationModal
      open={Boolean(authorizationOffering)}
      productKind="data"
      productName={detail?.name || ''}
      versionId={detail?.version_id || ''}
      offering={authorizationOffering}
      onCancel={() => setAuthorizationOffering(null)}
    />
  </div>
}

export function PublishedDataCatalogPage() {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const state = useLoad<{ items: DataCatalogItem[] }>('/data-product-catalog')
  const [query, setQuery] = useState('')
  const [diseaseOrOrgan, setDiseaseOrOrgan] = useState('')
  const [modality, setModality] = useState<string>()
  const [publicPage, setPublicPage] = useState(1)
  const [modeFilter, setModeFilter] = useState<'all' | 'controlled_compute' | 'deidentified_data_delivery'>('all')
  const [sourceFilter, setSourceFilter] = useState<MarketplaceSourceFilter>('all')
  const [authorizationTarget, setAuthorizationTarget] = useState<{ item: DataCatalogItem; offering: ServiceOffering } | null>(null)
  const [publicDetailTarget, setPublicDetailTarget] = useState<PublicDatasetCatalogItem | null>(null)
  const [publicDetail, setPublicDetail] = useState<PublicDatasetCatalogDetail | null>(null)
  const [publicDetailLoading, setPublicDetailLoading] = useState(false)
  const publicDetailRequestId = useRef(0)
  const debouncedQuery = useDebouncedText(query)
  const debouncedDiseaseOrOrgan = useDebouncedText(diseaseOrOrgan)
  const publicCatalogEnabled = (sourceFilter === 'all' || sourceFilter === 'public') && modeFilter === 'all'
  const publicCatalogPath = useMemo(() => {
    if (!publicCatalogEnabled) return null
    const parameters = new URLSearchParams({
      offset: String((publicPage - 1) * PUBLIC_DATA_PAGE_SIZE),
      limit: String(PUBLIC_DATA_PAGE_SIZE),
    })
    if (debouncedQuery.trim()) parameters.set('q', debouncedQuery.trim())
    if (debouncedDiseaseOrOrgan.trim()) parameters.set('disease_or_organ', debouncedDiseaseOrOrgan.trim())
    if (modality) parameters.set('modality', modality)
    return `/external-catalog/datasets?${parameters}`
  }, [debouncedDiseaseOrOrgan, debouncedQuery, modality, publicCatalogEnabled, publicPage])
  const publicState = useLoad<{ items: PublicDatasetCatalogItem[]; total: number; offset: number; limit: number }>(publicCatalogPath)
  const items = useMemo(() => (state.data?.items || []).filter((item) => {
    const source = dataMarketplaceSource(item)
    if (sourceFilter !== 'all' && source.kind !== sourceFilter) return false
    if (modeFilter !== 'all' && !availableOfferings(item.offerings, 'data', item.application_eligibility).some((offering) => offering.mode === modeFilter)) return false
    const searchable = [item.name, item.product_code, item.provider, item.description, item.disease_domain, item.modality].join(' ').toLocaleLowerCase()
    if (debouncedQuery.trim() && !searchable.includes(debouncedQuery.trim().toLocaleLowerCase())) return false
    if (debouncedDiseaseOrOrgan.trim() && !item.disease_domain.toLocaleLowerCase().includes(debouncedDiseaseOrOrgan.trim().toLocaleLowerCase())) return false
    return !modality || item.modality === modality
  }), [debouncedDiseaseOrOrgan, debouncedQuery, modality, modeFilter, sourceFilter, state.data?.items])
  const publicItems = publicCatalogEnabled
    ? (publicState.data?.items || []).filter((item) => !item.published_product_version_id)
    : []
  const filtersActive = modeFilter !== 'all' || sourceFilter !== 'all' || Boolean(query.trim() || diseaseOrOrgan.trim() || modality)
  const openPublicDetail = async (item: PublicDatasetCatalogItem) => {
    const requestId = ++publicDetailRequestId.current
    setPublicDetailTarget(item)
    setPublicDetail(null)
    setPublicDetailLoading(true)
    try {
      const detail = await platformGet<PublicDatasetCatalogDetail>(`/external-catalog/datasets/${item.id}`, identity)
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
    <PageTitle title="数据商城" description="已发布服务与公共目录资源统一展示；目录资源完成治理接入后才开放报价与申请。" actions={<>
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
          aria-label="搜索数据名称"
          placeholder="搜索数据名称或编号"
          value={query}
          onChange={(event) => { setPublicPage(1); setQuery(event.target.value) }}
          className="phase51-catalog-filter"
        />
        <Input
          allowClear
          prefix={<SearchOutlined />}
          aria-label="搜索疾病或器官"
          placeholder="输入疾病或器官"
          value={diseaseOrOrgan}
          onChange={(event) => { setPublicPage(1); setDiseaseOrOrgan(event.target.value) }}
          className="phase51-catalog-filter"
        />
        <Select
          allowClear
          aria-label="按数据模态筛选"
          placeholder="全部模态"
          value={modality}
          onChange={(value) => { setPublicPage(1); setModality(value) }}
          options={['CT', 'MR', 'MRI', 'X-Ray', 'Pathology', 'Ultrasound'].map((value) => ({ value }))}
          className="phase51-catalog-filter"
        />
      <Select
        aria-label="按数据来源筛选"
        value={sourceFilter}
        onChange={(value) => { setPublicPage(1); setSourceFilter(value) }}
        options={[
          { value: 'all', label: '全部来源' },
          { value: 'public', label: '公共来源' },
          { value: 'provider', label: '机构自有' },
        ]}
        className="phase51-catalog-filter"
      />
      <Select
        aria-label="按数据服务方式筛选"
        value={modeFilter}
        onChange={(value) => { setPublicPage(1); setModeFilter(value) }}
        options={[
          { value: 'all', label: '全部授权方式' },
          { value: 'controlled_compute', label: serviceModeLabels.controlled_compute },
          { value: 'deidentified_data_delivery', label: serviceModeLabels.deidentified_data_delivery },
        ]}
        className="phase51-catalog-filter"
      />
      </Space>
    </Card>
    <PageLoad loading={state.loading} error={state.error} hasContent={state.data !== null}>
      {publicState.error && publicCatalogEnabled && <Alert type="error" showIcon title="公共目录加载失败，已保留已发布产品" description={publicState.error} />}
      {publicCatalogEnabled && publicState.loading && publicState.data === null && <Flex className="phase51-loading" justify="center" align="center"><Spin /></Flex>}
      {items.length || publicItems.length ? <Row gutter={[16, 16]}>
        {items.map((item) => {
          const offerings = availableOfferings(item.offerings, 'data', item.application_eligibility)
          const source = dataMarketplaceSource(item)
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
            <CommercialOfferPreview productKind="data" versionId={item.version_id} compact />
            <div className="marketplace-card-details" aria-label="悬停查看数据产品详情">
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
              <Link to={`/data-products/${item.version_id}`}><Button icon={<FileSearchOutlined />}>查看详情</Button></Link>
              {identity === 'data_requester' && offerings.filter((offering) => offering.requestable).map((offering) => offering.mode === 'controlled_compute'
                ? <Button key={offering.mode} type="primary" onClick={() => navigate('/applications/new', {
                  state: { productSelection: { dataVersionId: item.version_id } },
                })}>申请调用</Button>
                : <Button key={offering.mode} type="primary" ghost onClick={() => setAuthorizationTarget({ item, offering })}>申请授权</Button>)}
            </div>
           </Card>
         </Col>})}
        {publicItems.map((item) => <Col xs={24} lg={12} key={item.id}>
          <PublicDatasetCatalogCard item={item} onOpen={(target) => void openPublicDetail(target)} />
        </Col>)}
      </Row> : <Empty description={filtersActive ? '当前没有匹配筛选条件的数据产品' : '当前没有已发布的数据产品'}>
        {filtersActive && <Button onClick={() => { setQuery(''); setDiseaseOrOrgan(''); setModality(undefined); setModeFilter('all'); setSourceFilter('all'); setPublicPage(1) }}>清除筛选</Button>}
      </Empty>}
      {publicCatalogEnabled && publicState.data && publicState.data.total > PUBLIC_DATA_PAGE_SIZE && <Flex justify="center">
        <Pagination
          current={publicPage}
          pageSize={PUBLIC_DATA_PAGE_SIZE}
          total={publicState.data.total}
          showSizeChanger={false}
          showTotal={(total) => `公共目录共 ${total.toLocaleString()} 条`}
          onChange={setPublicPage}
        />
      </Flex>}
    </PageLoad>
    <ServiceAuthorizationModal
      open={Boolean(authorizationTarget)}
      productKind="data"
      productName={authorizationTarget?.item.name || ''}
      versionId={authorizationTarget?.item.version_id || ''}
      offering={authorizationTarget?.offering || null}
      onCancel={() => setAuthorizationTarget(null)}
    />
    <Drawer
      size="large"
      title={publicDetailTarget ? publicDatasetName(publicDetailTarget) : '公共数据目录详情'}
      open={Boolean(publicDetailTarget)}
      onClose={closePublicDetail}
    >
      {publicDetailLoading && <Flex className="phase51-loading" justify="center" align="center"><Spin /></Flex>}
      {publicDetail && <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="资源状态"><Space wrap><Tag color="blue">目录资源</Tag><Tag>待治理接入</Tag></Space></Descriptions.Item>
        <Descriptions.Item label="外部 ID">{publicDetail.external_id}</Descriptions.Item>
        <Descriptions.Item label="来源目录">{publicDetail.source_catalog}</Descriptions.Item>
        <Descriptions.Item label="模态">{publicDetail.modalities.join('、') || '待补充'}</Descriptions.Item>
        <Descriptions.Item label="疾病 / 器官">{[...publicDetail.disease_areas, ...publicDetail.organs].join('、') || '待补充'}</Descriptions.Item>
        <Descriptions.Item label="样本数">{publicDetail.sample_count?.toLocaleString() || '待补充'}</Descriptions.Item>
        <Descriptions.Item label="许可">{publicDetail.license_name || publicDetail.license_status}</Descriptions.Item>
        <Descriptions.Item label="访问方式">{publicDetail.access_level}</Descriptions.Item>
        <Descriptions.Item label="数据格式">{publicDetail.data_formats.join('、') || '待补充'}</Descriptions.Item>
        <Descriptions.Item label="质量待核项">{publicDetail.quality_flags.join('、') || '无'}</Descriptions.Item>
        <Descriptions.Item label="官方来源">{publicDetail.official_source_url
          ? <a href={publicDetail.official_source_url} target="_blank" rel="noreferrer noopener">查看上游页面</a>
          : publicDetail.official_source_name || '待补充'}</Descriptions.Item>
      </Descriptions>}
    </Drawer>
  </div>
}
