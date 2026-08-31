import { Button, Descriptions, Drawer, Form, Input, Progress, Select, Space, Statistic, Table, Tag, Timeline, Typography, message } from 'antd'
import { ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useState } from 'react'
import { platformCommand, platformGet } from './api'
import { useRoadshow } from './RoadshowContext'

type Summary = {
  total_models: number
  profile_total: number
  status_counts: Record<string, number>
  weight_public_not_downloaded: number
  license_unknown: number
  revision_unpinned: number
  input_missing: number
  output_missing: number
  preprocessing_missing: number
  external_model_products: number
  published_metadata_only: number
  remaining_external_drafts: number
  materialized_external_models: number
  execution_ready_external_models: number
}
type Model = { id: string; canonical_name: string; weights_status: string; execution_status: string }
type Profile = {
  primary_status: string
  source_review_status: string
  paper_review_status: string
  repository_review_status: string
  model_card_review_status: string
  license_review_status: string
  weight_review_status: string
  revision_review_status: string
  technical_contract_score: number
  technical_missing_fields: string[]
  clinical_boundary_status: string
  security_review_status: string
  security_risk_flags: string[]
  model_family_status: string
  potential_family_key: string | null
  productization_eligible: boolean
  blocking_reasons: string[]
  warning_reasons: string[]
}
type Review = { id: string; review_dimension: string; decision: string; evidence_type: string; evidence_note: string; reviewed_at: string; previous_value: string | null }
type ModelProductDraft = {
  product: { product_code: string; lifecycle_status: string }
  version: { status: string; runtime: string }
  publication: null | { status: string; published_at: string }
  publication_review: null | { task_status: string; decision: string | null }
  source_link: { materialization_status: string; execution_readiness: string; platform_validation: string }
}
type Detail = { model: Model; profile: Profile | null; reviews: Review[]; model_product_draft: ModelProductDraft | null; boundaries: { local_weights: string; execution_image: null; platform_validation: string; executable: boolean; eligible_explanation: string } }

const dimensionOptions = [
  'source', 'paper', 'repository', 'model_card', 'license', 'weights',
  'revision', 'technical_contract', 'clinical_boundary', 'security',
  'model_family', 'productization',
].map((value) => ({ value, label: value }))

export function ExternalModelGovernancePage({ operator = false }: { operator?: boolean }) {
  const { identity } = useRoadshow()
  const [summary, setSummary] = useState<Summary | null>(null)
  const [models, setModels] = useState<Model[]>([])
  const [profiles, setProfiles] = useState<Record<string, Profile>>({})
  const [detail, setDetail] = useState<Detail | null>(null)
  const [status, setStatus] = useState<string>()
  const [loading, setLoading] = useState(false)
  const [creatingDraft, setCreatingDraft] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [summaryResult, modelResult] = await Promise.all([
        platformGet<Summary>('/external-model-catalog/governance/summary', identity),
        platformGet<{ items: Model[] }>('/external-model-catalog/models?limit=100', identity),
      ])
      setSummary(summaryResult)
      setModels(modelResult.items)
      const details = await Promise.all(modelResult.items.map(async (model) => {
        const value = await platformGet<Detail>(`/external-model-catalog/models/${model.id}/governance`, identity)
        return [model.id, value.profile] as const
      }))
      setProfiles(Object.fromEntries(details.filter((item) => item[1])))
    } finally {
      setLoading(false)
    }
  }, [identity])

  useEffect(() => { void load() }, [load])

  const openDetail = async (model: Model) => {
    setDetail(await platformGet<Detail>(`/external-model-catalog/models/${model.id}/governance`, identity))
    form.resetFields()
  }

  const recalculate = async () => {
    setLoading(true)
    try {
      await platformCommand('/external-model-catalog/governance/recalculate', identity, crypto.randomUUID())
      message.success('模型治理画像已重新计算')
      await load()
    } finally { setLoading(false) }
  }

  const submitReview = async () => {
    if (!detail) return
    const values = await form.validateFields()
    await platformCommand(
      `/external-model-catalog/models/${detail.model.id}/reviews`,
      identity,
      crypto.randomUUID(),
      { method: 'POST', body: JSON.stringify({ ...values, decision_payload: {} }) },
    )
    message.success('Review 已追加，原历史保持不变')
    await openDetail(detail.model)
    await load()
  }

  const createDraft = async () => {
    if (!detail) return
    setCreatingDraft(true)
    try {
      await platformCommand(
        `/external-model-catalog/models/${detail.model.id}/model-product-draft`,
        identity,
        crypto.randomUUID(),
        {
          method: 'POST',
          body: JSON.stringify({ curator_note: 'Operator-created governed metadata-only model draft.' }),
        },
      )
      message.success('模型元数据产品草稿已创建')
      await openDetail(detail.model)
    } finally {
      setCreatingDraft(false)
    }
  }

  const approvePublication = async () => {
    if (!detail) return
    setPublishing(true)
    try {
      await platformCommand(
        `/external-model-catalog/models/${detail.model.id}/model-product-publication/approve`,
        identity,
        crypto.randomUUID(),
        {
          method: 'POST',
          body: JSON.stringify({
            allow_catalog: true,
            review_opinion: '来源、治理快照和仅元数据边界复核通过。',
            risk_level: 'medium',
            additional_conditions: '仅用于目录发现；禁止下载、申请、就绪确认和执行。',
          }),
        },
      )
      message.success('模型元数据产品已发布到公共目录')
      await openDetail(detail.model)
      await load()
    } finally {
      setPublishing(false)
    }
  }

  const rows = models.filter((model) => !status || profiles[model.id]?.primary_status === status)
  const columns = [
    { title: '模型', width: 220, render: (_: unknown, model: Model) => <Button type="link" onClick={() => void openDetail(model)}>{model.canonical_name}</Button> },
    { title: '主状态', width: 190, render: (_: unknown, model: Model) => <Tag>{profiles[model.id]?.primary_status || 'unreviewed'}</Tag> },
    { title: '技术契约完整度', width: 170, render: (_: unknown, model: Model) => <Progress percent={profiles[model.id]?.technical_contract_score || 0} size="small" /> },
    { title: '许可证', width: 120, render: (_: unknown, model: Model) => profiles[model.id]?.license_review_status || 'unknown' },
    { title: '权重', width: 160, render: (_: unknown, model: Model) => <span>{profiles[model.id]?.weight_review_status || 'unknown'} / 未下载</span> },
    { title: 'Revision', width: 130, render: (_: unknown, model: Model) => profiles[model.id]?.revision_review_status || 'unknown' },
    { title: '临床边界', width: 150, render: (_: unknown, model: Model) => profiles[model.id]?.clinical_boundary_status || 'not_assessed' },
    { title: '可创建草稿', width: 110, render: (_: unknown, model: Model) => <Tag color={profiles[model.id]?.productization_eligible ? 'green' : 'default'}>{profiles[model.id]?.productization_eligible ? '是' : '否'}</Tag> },
  ]

  return <div className="external-catalog-page">
    <div className="external-catalog-heading">
      <div><Typography.Title level={3}><SafetyCertificateOutlined /> 公共模型治理工作台</Typography.Title><Typography.Text type="secondary">治理结论与外部原始记录分离；草稿资格、物化和执行状态分别展示。</Typography.Text></div>
      {operator && <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void recalculate()}>重新计算画像</Button>}
    </div>
    {summary && <div className="metric-grid">
      <Statistic title="模型候选" value={summary.total_models} />
      <Statistic title="治理画像" value={summary.profile_total} />
      <Statistic title="许可证待核验" value={summary.license_unknown} />
      <Statistic title="Revision 未固定" value={summary.revision_unpinned} />
      <Statistic title="公开权重但未下载" value={summary.weight_public_not_downloaded} />
      <Statistic title="允许草稿" value={summary.status_counts.eligible_for_model_draft || 0} />
      <Statistic title="外部模型产品" value={summary.external_model_products} />
      <Statistic title="已发布仅元数据" value={summary.published_metadata_only} />
      <Statistic title="剩余草稿" value={summary.remaining_external_drafts} />
    </div>}
    <div className="external-catalog-filters">
      <Select allowClear placeholder="治理队列" value={status} onChange={setStatus} options={Object.keys(summary?.status_counts || {}).map((value) => ({ value, label: `${value} (${summary?.status_counts[value]})` }))} />
    </div>
    <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} scroll={{ x: 1250 }} pagination={false} />
    <Drawer size={820} styles={{ wrapper: { maxWidth: '100vw' } }} title={detail?.model.canonical_name} open={Boolean(detail)} onClose={() => setDetail(null)}>
      {detail?.profile && <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          <Space wrap size={[4, 4]}>
            {detail.model_product_draft && <Tag color="blue">元数据草稿 {detail.model_product_draft.product.product_code}</Tag>}
            {detail.model_product_draft?.publication && <Tag color="green">已发布到公共模型目录</Tag>}
            <Tag>权重 {detail.boundaries.local_weights}</Tag>
            <Tag>执行镜像 {detail.boundaries.execution_image || '无'}</Tag>
            <Tag>平台验证 {detail.boundaries.platform_validation}</Tag>
            <Tag color={detail.boundaries.executable ? 'green' : 'orange'}>{detail.boundaries.executable ? '可执行' : '不可执行'}</Tag>
          </Space>
          <Typography.Text type="secondary">{detail.boundaries.eligible_explanation}</Typography.Text>
        </Space>
        {!detail.model_product_draft && operator && detail.profile.productization_eligible && <Button
          type="primary"
          loading={creatingDraft}
          onClick={() => void createDraft()}
        >创建模型元数据草稿</Button>}
        {operator
          && detail.model_product_draft?.version.status === 'under_review'
          && <Button
            type="primary"
            loading={publishing}
            onClick={() => void approvePublication()}
          >批准并发布仅元数据产品</Button>}
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="主状态">{detail.profile.primary_status}</Descriptions.Item>
          <Descriptions.Item label="来源 / 论文">{detail.profile.source_review_status} / {detail.profile.paper_review_status}</Descriptions.Item>
          <Descriptions.Item label="仓库 / 模型卡">{detail.profile.repository_review_status} / {detail.profile.model_card_review_status}</Descriptions.Item>
          <Descriptions.Item label="许可证 / 权重">{detail.profile.license_review_status} / {detail.profile.weight_review_status}</Descriptions.Item>
          <Descriptions.Item label="Revision">{detail.profile.revision_review_status}</Descriptions.Item>
          <Descriptions.Item label="技术契约完整度"><Progress percent={detail.profile.technical_contract_score} /></Descriptions.Item>
          <Descriptions.Item label="缺失字段">{detail.profile.technical_missing_fields.join('、') || '无'}</Descriptions.Item>
          <Descriptions.Item label="临床边界">{detail.profile.clinical_boundary_status}</Descriptions.Item>
          <Descriptions.Item label="安全风险">{detail.profile.security_risk_flags.join('、') || '待审核'}</Descriptions.Item>
          <Descriptions.Item label="模型家族">{detail.profile.model_family_status} {detail.profile.potential_family_key || ''}</Descriptions.Item>
          <Descriptions.Item label="阻断原因">{detail.profile.blocking_reasons.join('、') || '无'}</Descriptions.Item>
          <Descriptions.Item label="警告原因">{detail.profile.warning_reasons.join('、') || '无'}</Descriptions.Item>
        </Descriptions>
        <Typography.Title level={5}>Review 时间线</Typography.Title>
        <Timeline items={detail.reviews.map((review) => ({ children: `${review.review_dimension}: ${review.decision} · ${review.evidence_type} · ${review.evidence_note}` }))} />
        {operator && <Form form={form} layout="vertical">
          <Form.Item name="review_dimension" label="审核维度" rules={[{ required: true }]}><Select options={dimensionOptions} /></Form.Item>
          <Form.Item name="decision" label="结论" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="evidence_type" label="证据类型" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="evidence_reference" label="证据引用"><Input /></Form.Item>
          <Form.Item name="evidence_note" label="证据说明" rules={[{ required: true }]}><Input.TextArea rows={3} /></Form.Item>
          <Button type="primary" onClick={() => void submitReview()}>追加 Review</Button>
        </Form>}
      </Space>}
    </Drawer>
  </div>
}
