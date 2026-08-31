import { DatabaseOutlined, FileSearchOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { Alert, Button, Descriptions, Drawer, Form, Input, Select, Space, Statistic, Table, Tag, Typography, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { platformCommand, platformGet } from './api'
import { useRoadshow } from './RoadshowContext'

type Summary = { total_profiles: number; eligible_for_draft: number; formal_reviews: number; duplicate_groups: number; by_primary_status: Record<string, number> }
type Dataset = { id: string; external_id: string; canonical_name: string; modalities: string[]; license_status: string; link_status: string; data_product_draft_status?: string | null }
type Governance = {
  primary_status: string; source_review_status: string; license_review_status: string; access_review_status: string
  metadata_completeness_score: number; metadata_missing_fields: string[]; duplicate_review_status: string
  productization_eligible: boolean; blocking_reasons: string[]; warning_reasons: string[]
}
type Row = { dataset: Dataset; governance: Governance }
type Review = {
  id: string; review_dimension: string; decision: string; decision_payload: Record<string, unknown>
  evidence_type: string; evidence_reference?: string; evidence_note: string; reviewed_at: string
}
type ExternalDraft = {
  product: { product_code: string; lifecycle_status: string }
  version: { status: string; default_use_mode: string; linkage_metadata: Record<string, unknown> }
  source_link: { materialization_status: string; data_holder_status: string; redistribution_status: string; execution_readiness: string; governance_snapshot_digest: string; upstream_official_url: string }
}
type Detail = Row & { reviews: Review[]; data_product_draft: ExternalDraft | null }
type TriState = 'true' | 'false' | 'unknown'
type ReviewForm = {
  dimension: string; decision: string; evidence_type: string; evidence_reference?: string; evidence_note: string
  official_source_name?: string; official_source_url?: string; license_name?: string; license_url?: string
  research_use?: TriState; commercial_use?: TriState; redistribution?: TriState; derivatives?: TriState; rehosting?: TriState
  access_url?: string; access_note?: string
}

const statusLabels: Record<string, string> = {
  blocked: '已阻塞',
  duplicate_pending: '重复项待处理',
  needs_license_review: '待许可证审核',
  needs_source_review: '待来源审核',
  needs_access_review: '待访问审核',
  metadata_incomplete: '元数据不完整',
  in_review: '审核中',
  eligible_for_draft: '可进入产品草稿',
  unreviewed: '未审核',
  rejected: '已拒绝',
}

const decisionOptions: Record<string, string[]> = {
  source: ['official_source_confirmed', 'aggregator_only', 'source_missing', 'source_malformed', 'source_disputed', 'unreviewed'],
  license: ['unknown', 'permissive', 'research_only', 'noncommercial', 'controlled', 'custom_terms', 'redistribution_prohibited', 'unverified', 'not_applicable'],
  access: ['unknown', 'open_download', 'registration_required', 'application_required', 'controlled_access', 'request_author', 'metadata_only', 'unavailable'],
  metadata: ['accepted', 'incomplete', 'blocked'],
  link: ['missing', 'malformed', 'legacy_http', 'syntactically_valid_https', 'unchecked'],
  duplicate: ['not_duplicate', 'duplicate_unresolved', 'canonical_candidate', 'alias_candidate', 'separate_valid_record', 'duplicate_resolved'],
  productization: ['approved', 'rejected', 'unreviewed'],
}

const triStateOptions = [
  { value: 'true', label: '是' },
  { value: 'false', label: '否' },
  { value: 'unknown', label: '未知' },
]

function reviewPayload(values: ReviewForm): Record<string, unknown> {
  if (values.dimension === 'source') {
    return {
      official_source_name: values.official_source_name?.trim() || null,
      official_source_url: values.official_source_url?.trim() || null,
    }
  }
  if (values.dimension === 'license') {
    return {
      license_name: values.license_name?.trim() || null,
      license_url: values.license_url?.trim() || null,
      research_use: values.research_use || 'unknown',
      commercial_use: values.commercial_use || 'unknown',
      redistribution: values.redistribution || 'unknown',
      derivatives: values.derivatives || 'unknown',
      rehosting: values.rehosting || 'unknown',
    }
  }
  if (values.dimension === 'access') {
    return {
      access_url: values.access_url?.trim() || null,
      access_note: values.access_note?.trim() || null,
    }
  }
  return {}
}

export function ExternalGovernancePage({ operator = false }: { operator?: boolean }) {
  const { identity } = useRoadshow()
  const [summary, setSummary] = useState<Summary | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [status, setStatus] = useState<string>()
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [creatingDraft, setCreatingDraft] = useState(false)
  const [form] = Form.useForm<ReviewForm>()
  const dimension = Form.useWatch('dimension', form)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const parameters = new URLSearchParams({ limit: '100' })
      if (query.trim()) parameters.set('q', query.trim())
      if (status) parameters.set('primary_status', status)
      const [nextSummary, list] = await Promise.all([
        platformGet<Summary>('/external-catalog/governance/summary', identity),
        platformGet<{ items: Row[]; total: number }>(`/external-catalog/governance/datasets?${parameters}`, identity),
      ])
      setSummary(nextSummary)
      setRows(list.items)
      setTotal(list.total)
    } finally {
      setLoading(false)
    }
  }, [identity, query, status])

  useEffect(() => { void load() }, [load])

  const openDetail = async (recordId: string) => {
    const [governance, draft] = await Promise.all([
      platformGet<Omit<Detail, 'data_product_draft'>>(`/external-catalog/datasets/${recordId}/governance`, identity),
      platformGet<{ exists: boolean; draft: ExternalDraft | null }>(`/external-catalog/datasets/${recordId}/data-product-draft`, identity),
    ])
    setDetail({ ...governance, data_product_draft: draft.draft })
  }

  const createDraft = async () => {
    if (!detail) return
    setCreatingDraft(true)
    try {
      await platformCommand(`/external-catalog/datasets/${detail.dataset.id}/data-product-draft`, identity, crypto.randomUUID(), {
        curator_note: '由已完成治理审核的外部公共数据候选记录创建元数据产品草稿。',
      })
      message.success('元数据产品草稿已创建')
      await openDetail(detail.dataset.id)
      await load()
    } finally {
      setCreatingDraft(false)
    }
  }

  const recalculate = async () => {
    setSubmitting(true)
    try {
      await platformCommand('/external-catalog/governance/recalculate', identity, crypto.randomUUID())
      message.success('治理画像已重新计算')
      await load()
    } finally {
      setSubmitting(false)
    }
  }

  const submitReview = async (values: ReviewForm) => {
    if (!detail) return
    setSubmitting(true)
    try {
      await platformCommand(`/external-catalog/datasets/${detail.dataset.id}/reviews`, identity, crypto.randomUUID(), {
        dimension: values.dimension,
        decision: values.decision,
        evidence_type: values.evidence_type,
        evidence_reference: values.evidence_reference,
        evidence_note: values.evidence_note,
        decision_payload: reviewPayload(values),
      })
      message.success('正式审核记录已追加')
      form.resetFields()
      await openDetail(detail.dataset.id)
      await load()
    } finally {
      setSubmitting(false)
    }
  }

  const columns = [
    { title: '外部数据集', width: 300, render: (_: unknown, row: Row) => <Button type="link" onClick={() => void openDetail(row.dataset.id)}>{row.dataset.canonical_name}</Button> },
    { title: '治理状态', width: 150, render: (_: unknown, row: Row) => <Tag>{statusLabels[row.governance.primary_status] || row.governance.primary_status}</Tag> },
    { title: '元数据完整度', width: 130, render: (_: unknown, row: Row) => `${row.governance.metadata_completeness_score}%` },
    { title: '许可', width: 150, render: (_: unknown, row: Row) => row.governance.license_review_status },
    { title: '访问', width: 160, render: (_: unknown, row: Row) => row.governance.access_review_status },
    { title: '重复项', width: 160, render: (_: unknown, row: Row) => row.governance.duplicate_review_status },
    { title: '产品草稿', width: 120, render: (_: unknown, row: Row) => row.dataset.data_product_draft_status === 'draft' ? <Tag color="blue">已建立</Tag> : row.dataset.data_product_draft_status === 'archived' ? <Tag>已归档</Tag> : <Tag color={row.governance.productization_eligible ? 'green' : 'default'}>{row.governance.productization_eligible ? '可创建' : '不可创建'}</Tag> },
  ]

  return <div className="external-governance-page">
    <div className="external-governance-heading">
      <div>
        <Typography.Title level={3}><SafetyCertificateOutlined /> 公共数据治理工作台</Typography.Title>
        <Typography.Text type="secondary">治理结论与上游 982 条原始目录记录分层保存；来源、许可与访问条件按状态分别展示。</Typography.Text>
      </div>
      {operator && <Button icon={<ReloadOutlined />} loading={submitting} onClick={() => void recalculate()}>重新计算画像</Button>}
    </div>
    <div className="external-governance-stats">
      <Statistic title="治理画像" value={summary?.total_profiles || 0} />
      <Statistic title="待许可证审核" value={summary?.by_primary_status.needs_license_review || 0} />
      <Statistic title="重复项待处理" value={summary?.by_primary_status.duplicate_pending || 0} />
      <Statistic title="正式审核" value={summary?.formal_reviews || 0} />
      <Statistic title="可进入草稿" value={summary?.eligible_for_draft || 0} />
    </div>
    <div className="external-governance-filters">
      <Input.Search allowClear placeholder="搜索名称或外部 ID" onSearch={setQuery} />
      <Select allowClear placeholder="治理状态" value={status} onChange={setStatus} options={Object.entries(statusLabels).map(([value, label]) => ({ value, label }))} />
    </div>
    <Table rowKey={(row) => row.dataset.id} loading={loading} columns={columns} dataSource={rows} scroll={{ x: 1150 }} pagination={{ pageSize: 100, total, showSizeChanger: false }} />
    <Drawer styles={{ wrapper: { width: 'min(720px, 100vw)' } }} title={detail?.dataset.canonical_name} open={Boolean(detail)} onClose={() => setDetail(null)}>
      {detail && <Space orientation="vertical" size={20} style={{ width: '100%' }}>
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="治理状态">{statusLabels[detail.governance.primary_status] || detail.governance.primary_status}</Descriptions.Item>
          <Descriptions.Item label="来源审核">{detail.governance.source_review_status}</Descriptions.Item>
          <Descriptions.Item label="许可审核">{detail.governance.license_review_status}</Descriptions.Item>
          <Descriptions.Item label="访问审核">{detail.governance.access_review_status}</Descriptions.Item>
          <Descriptions.Item label="元数据完整度">{detail.governance.metadata_completeness_score}%</Descriptions.Item>
          <Descriptions.Item label="缺失字段">{detail.governance.metadata_missing_fields.join('、') || '无'}</Descriptions.Item>
          <Descriptions.Item label="阻塞原因">{detail.governance.blocking_reasons.join('、') || '无'}</Descriptions.Item>
          <Descriptions.Item label="提示">{detail.governance.warning_reasons.join('、') || '无'}</Descriptions.Item>
        </Descriptions>
        {detail.data_product_draft ? <Alert type="success" showIcon message="元数据产品草稿已建立" description={<Space orientation="vertical" size={4}>
          <span>{detail.data_product_draft.product.product_code} / {detail.data_product_draft.version.status}</span>
          <span>{detail.data_product_draft.version.default_use_mode} · {detail.data_product_draft.source_link.materialization_status} · {detail.data_product_draft.source_link.data_holder_status} · {detail.data_product_draft.source_link.execution_readiness}</span>
          <Typography.Text type="secondary">治理快照：{detail.data_product_draft.source_link.governance_snapshot_digest}</Typography.Text>
        </Space>} /> : operator && detail.governance.productization_eligible && <Button type="primary" icon={<DatabaseOutlined />} loading={creatingDraft} onClick={() => void createDraft()}>创建元数据产品草稿</Button>}
        <Typography.Title level={5}><FileSearchOutlined /> 审核时间线</Typography.Title>
        {detail.reviews.length ? detail.reviews.map((review) => <Alert key={review.id} type="success" title={`${review.review_dimension}: ${review.decision}`} description={<Space orientation="vertical" size={2}><span>{review.evidence_note}</span>{review.evidence_reference && <Typography.Link href={review.evidence_reference} target="_blank" rel="noreferrer">查看证据页面</Typography.Link>}<Typography.Text type="secondary">{new Date(review.reviewed_at).toLocaleString('zh-CN')}</Typography.Text></Space>} />) : <Alert type="warning" title="尚无正式审核记录" />}
        {operator && <Form form={form} layout="vertical" onFinish={(values) => void submitReview(values)}>
          <Typography.Title level={5}>追加正式审核</Typography.Title>
          <Form.Item name="dimension" label="审核维度" rules={[{ required: true }]}>
            <Select onChange={() => form.setFieldValue('decision', undefined)} options={Object.keys(decisionOptions).map((value) => ({ value }))} />
          </Form.Item>
          <Form.Item name="decision" label="结论" rules={[{ required: true }]}>
            <Select disabled={!dimension} options={(decisionOptions[dimension] || []).map((value) => ({ value }))} />
          </Form.Item>
          {dimension === 'source' && <>
            <Form.Item name="official_source_name" label="官方来源名称"><Input /></Form.Item>
            <Form.Item name="official_source_url" label="已核验官方地址" rules={[{ type: 'url' }]}><Input /></Form.Item>
          </>}
          {dimension === 'license' && <>
            <Form.Item name="license_name" label="许可证或条款名称"><Input /></Form.Item>
            <Form.Item name="license_url" label="许可证或条款地址" rules={[{ type: 'url' }]}><Input /></Form.Item>
            {(['research_use', 'commercial_use', 'redistribution', 'derivatives', 'rehosting'] as const).map((name) =>
              <Form.Item key={name} name={name} label={{ research_use: '研究使用', commercial_use: '商业使用', redistribution: '再分发', derivatives: '衍生修改', rehosting: '重新托管' }[name]} initialValue="unknown"><Select options={triStateOptions} /></Form.Item>,
            )}
          </>}
          {dimension === 'access' && <>
            <Form.Item name="access_url" label="访问说明地址" rules={[{ type: 'url' }]}><Input /></Form.Item>
            <Form.Item name="access_note" label="访问条件摘要"><Input.TextArea rows={2} /></Form.Item>
          </>}
          <Form.Item name="evidence_type" label="证据类型" rules={[{ required: true }]}><Select options={['official_page', 'official_terms', 'official_repository', 'official_metadata', 'institutional_page'].map((value) => ({ value }))} /></Form.Item>
          <Form.Item name="evidence_reference" label="证据引用" rules={[{ type: 'url' }]}><Input /></Form.Item>
          <Form.Item name="evidence_note" label="审核说明" rules={[{ required: true, min: 3 }]}><Input.TextArea rows={3} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={submitting}>追加审核记录</Button>
        </Form>}
      </Space>}
    </Drawer>
  </div>
}
