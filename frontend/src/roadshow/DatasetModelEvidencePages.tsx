import { Button, Card, Form, Input, Select, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { platformCommand, platformGet } from './api'
import { useRoadshow } from './RoadshowContext'

type VersionOption = { product_id: string; version_id: string; name: string; version: string }
type Relation = {
  id: string
  data_product: { id: string; name: string }
  model_product: { id: string; name: string }
  current_status: string
  strongest_evidence_level: string
  public_visible: boolean
  current_evidence?: null | {
    evidence_reference?: Record<string, string>
    evidence_note: string
    structured_assessment?: {
      sample_count?: number
      success_count?: number
      failure_count?: number
      correct_count?: number
      aggregate_metrics?: { accuracy?: string; mean_confidence?: string }
      resource_usage?: { device?: string; hard_isolation?: boolean }
      platform_verification?: { review_status?: string; verified_at?: string }
      limitations?: string[]
    }
    transformation_requirements: Array<{ name?: string; implementation_verified?: boolean }>
    blocking_reasons: string[]
  }
}
type MatrixResponse = {
  items: Relation[]
  total: number
  matrix: { data_versions: VersionOption[]; model_versions: VersionOption[] }
}

const statusLabels: Record<string, string> = {
  static_schema_compatible: '静态结构兼容',
  static_schema_compatible_with_transformation: '经转换后静态兼容',
  static_schema_incompatible: '静态结构不兼容',
  insufficient_metadata: '元数据不足',
  external_declaration_only: '仅外部声明',
  executed: '已实际执行',
  verified: '平台已验证',
}

export function DatasetModelEvidencePage() {
  const { identity } = useRoadshow()
  const [result, setResult] = useState<MatrixResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [dataFilter, setDataFilter] = useState<string>()
  const [modelFilter, setModelFilter] = useState<string>()
  const [statusFilter, setStatusFilter] = useState<string>()
  const [form] = Form.useForm()
  const load = useCallback(async () => {
    setLoading(true)
    try {
      setResult(await platformGet<MatrixResponse>('/dataset-model-relations?matrix=true', identity))
    } finally {
      setLoading(false)
    }
  }, [identity])
  useEffect(() => { void load() }, [load])

  const relationByPair = useMemo(() => new Map((result?.items || []).map((item) => [
    `${item.data_product.id}:${item.model_product.id}`, item,
  ])), [result])
  const visibleModels = useMemo(
    () => (result?.matrix.model_versions || []).filter((model) => !modelFilter || model.version_id === modelFilter),
    [modelFilter, result],
  )
  const rows = useMemo(() => (result?.matrix.data_versions || [])
    .filter((data) => !dataFilter || data.version_id === dataFilter)
    .map((data) => ({
    key: data.version_id,
    data,
    ...Object.fromEntries(visibleModels.map((model) => [
      model.version_id, relationByPair.get(`${data.product_id}:${model.product_id}`),
    ])),
  }))
    .filter((row) => !statusFilter || visibleModels.some((model) => {
      const relation = row[model.version_id] as Relation | undefined
      return statusFilter === 'not_assessed' ? !relation : relation?.current_status === statusFilter
    })), [dataFilter, relationByPair, result, statusFilter, visibleModels])

  const submit = async () => {
    const values = await form.validateFields()
    const transforms = values.evidence_type === 'static_schema_compatible_with_transformation'
    await platformCommand('/dataset-model-relations/static-review', identity, crypto.randomUUID(), {
      data_product_version_id: values.data_product_version_id,
      model_product_version_id: values.model_product_version_id,
      evidence_type: values.evidence_type,
      outcome: 'supports',
      evidence_scope: 'input_schema',
      evidence_note: values.evidence_note,
      structured_assessment: {
        review_method: 'operator_structured_static_review',
        metadata_only: true,
        runtime_execution_performed: false,
        modality_result: values.modality_result,
        data_object_result: values.data_object_result,
        file_format_result: values.file_format_result,
        input_dimension_result: values.input_dimension_result,
        resolution_result: values.resolution_result,
        preprocessing_result: values.preprocessing_result,
        task_result: values.task_result,
        output_result: values.output_result,
        license_access_result: values.license_access_result,
      },
      transformation_requirements: transforms ? [{
        name: values.transformation_note || 'tissue masking, patch extraction and official model transform',
        implementation_available: false,
        implementation_verified: false,
      }] : [],
      blocking_reasons: values.blocking_reasons
        ? values.blocking_reasons.split('\n').map((value: string) => value.trim()).filter(Boolean)
        : [],
      warning_reasons: [
        'Metadata-only assessment; no execution or platform verification was performed.',
        ...(values.warning_reasons
          ? values.warning_reasons.split('\n').map((value: string) => value.trim()).filter(Boolean)
          : []),
      ],
    })
    message.success('静态证据已追加，历史记录保持不可变')
    form.resetFields(['evidence_note'])
    await load()
  }

  const columns = [
    {
      title: '公共数据产品', dataIndex: 'data', key: 'data', fixed: 'left' as const, width: 220,
      render: (data: VersionOption) => <><strong>{data.name}</strong><br /><Typography.Text type="secondary">{data.version}</Typography.Text></>,
    },
    ...visibleModels.map((model) => ({
      title: model.name,
      dataIndex: model.version_id,
      key: model.version_id,
      width: 250,
      render: (relation?: Relation) => relation
        ? <Space orientation="vertical" size={4}>
            <Tag color={relation.current_status === 'verified' ? 'green' : relation.current_status === 'static_schema_incompatible' ? 'red' : 'blue'}>
              {statusLabels[relation.current_status] || relation.current_status}
            </Tag>
            <Typography.Text type="secondary">{relation.strongest_evidence_level}</Typography.Text>
            <Space size={4}>
              <Tag color={relation.current_status === 'executed' || relation.current_status === 'verified' ? 'green' : undefined}>
                {relation.current_status === 'executed' || relation.current_status === 'verified' ? '已实际运行' : '未执行'}
              </Tag>
              <Tag color={relation.current_status === 'verified' ? 'green' : undefined}>
                {relation.current_status === 'verified' ? '平台验证' : '未验证'}
              </Tag>
              {relation.public_visible && <Tag color="green">公开</Tag>}
            </Space>
          </Space>
        : <Typography.Text type="secondary">尚未评估</Typography.Text>,
    })),
  ]

  return <div className="page-stack dataset-model-evidence-page">
    <div className="external-governance-heading">
      <div>
        <Typography.Title level={3}>数据—模型证据矩阵</Typography.Title>
        <Typography.Text type="secondary">锁定已发布版本与治理摘要，记录可追溯的静态判断。</Typography.Text>
      </div>
      <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button>
    </div>
    <Space wrap className="external-governance-filters evidence-filter-row">
      <Select allowClear placeholder="筛选数据产品" style={{ width: 220 }} value={dataFilter} onChange={setDataFilter}
        options={(result?.matrix.data_versions || []).map((item) => ({ value: item.version_id, label: item.name }))} />
      <Select allowClear placeholder="筛选模型产品" style={{ width: 220 }} value={modelFilter} onChange={setModelFilter}
        options={(result?.matrix.model_versions || []).map((item) => ({ value: item.version_id, label: item.name }))} />
      <Select allowClear placeholder="筛选关系状态" style={{ width: 250 }} value={statusFilter} onChange={setStatusFilter}
        options={[...Object.entries(statusLabels).map(([value, label]) => ({ value, label })), { value: 'not_assessed', label: '尚未评估' }]} />
    </Space>
    <Table loading={loading} columns={columns} dataSource={rows} pagination={false} scroll={{ x: 'max-content' }} bordered />
    <div className="evidence-review-panel">
      <Typography.Title level={4}>追加静态复核证据</Typography.Title>
      <Form form={form} layout="vertical" onFinish={() => void submit()} initialValues={{
        modality_result: 'unknown',
        data_object_result: 'unknown',
        file_format_result: 'unknown',
        input_dimension_result: 'unknown',
        resolution_result: 'unknown',
        preprocessing_result: 'needs_transformation',
        task_result: 'unknown',
        output_result: 'unknown',
        license_access_result: 'unknown',
      }}>
        <Space wrap align="start" className="evidence-form-row">
          <Form.Item name="data_product_version_id" label="数据版本" rules={[{ required: true }]}>
            <Select style={{ width: 250 }} options={(result?.matrix.data_versions || []).map((item) => ({ value: item.version_id, label: item.name }))} />
          </Form.Item>
          <Form.Item name="model_product_version_id" label="模型版本" rules={[{ required: true }]}>
            <Select style={{ width: 250 }} options={(result?.matrix.model_versions || []).map((item) => ({ value: item.version_id, label: item.name }))} />
          </Form.Item>
          <Form.Item name="evidence_type" label="静态结论" rules={[{ required: true }]}>
            <Select style={{ width: 250 }} options={[
              { value: 'static_schema_compatible', label: '静态结构兼容' },
              { value: 'static_schema_compatible_with_transformation', label: '经转换后静态兼容' },
              { value: 'static_schema_incompatible', label: '静态结构不兼容' },
              { value: 'insufficient_metadata', label: '元数据不足' },
            ]} />
          </Form.Item>
        </Space>
        <Space wrap align="start" className="evidence-form-row">
          {[
            ['modality_result', '模态'],
            ['data_object_result', '数据对象'],
            ['file_format_result', '文件格式'],
            ['input_dimension_result', '输入维度'],
            ['resolution_result', '分辨率'],
            ['preprocessing_result', '预处理'],
            ['task_result', '任务'],
            ['output_result', '输出'],
            ['license_access_result', '许可与访问'],
          ].map(([name, label]) => <Form.Item key={name} name={name} label={label} rules={[{ required: true }]}>
            <Select style={{ width: 180 }} options={[
              { value: 'pass', label: '未发现结构冲突' },
              { value: 'needs_transformation', label: '需要转换' },
              { value: 'conflict', label: '存在冲突' },
              { value: 'unknown', label: '信息不足' },
            ]} />
          </Form.Item>)}
        </Space>
        <Form.Item name="transformation_note" label="转换要求（如适用）">
          <Input.TextArea rows={2} maxLength={1000} />
        </Form.Item>
        <Form.Item name="blocking_reasons" label="阻断原因（每行一项）">
          <Input.TextArea rows={2} maxLength={1200} />
        </Form.Item>
        <Form.Item name="warning_reasons" label="警告（每行一项）">
          <Input.TextArea rows={2} maxLength={1200} />
        </Form.Item>
        <Form.Item name="evidence_note" label="证据说明" rules={[{ required: true, min: 20 }]}>
          <Input.TextArea rows={3} maxLength={2000} showCount />
        </Form.Item>
        <Button type="primary" htmlType="submit">追加证据</Button>
      </Form>
    </div>
  </div>
}

export function DatasetModelEvidenceSummary({
  productId,
  direction,
}: {
  productId: string
  direction: 'data-to-models' | 'model-to-data'
}) {
  const { identity } = useRoadshow()
  const [items, setItems] = useState<Relation[]>([])
  useEffect(() => {
    const controller = new AbortController()
    const path = direction === 'data-to-models'
      ? `/data-products/${productId}/model-evidence`
      : `/model-products/${productId}/dataset-evidence`
    platformGet<{ items: Relation[] }>(path, identity, controller.signal)
      .then((value) => setItems(value.items))
      .catch(() => undefined)
    return () => controller.abort()
  }, [direction, identity, productId])
  const relatedLabel = direction === 'data-to-models' ? '模型' : '数据'
  return <Card title={`相关${relatedLabel}证据`}>
    {items.length === 0
      ? <Typography.Text type="secondary">当前活动版本尚无公开证据。</Typography.Text>
      : <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          {items.map((item) => <div className="evidence-summary-item" key={item.id}>
            {(() => {
              const assessment = item.current_evidence?.structured_assessment
              const runtime = item.current_status === 'executed' || item.current_status === 'verified'
              const verified = item.current_status === 'verified'
              const runId = item.current_evidence?.evidence_reference?.compute_run_id
              const packageId = item.current_evidence?.evidence_reference?.result_package_id
              return <>
            <Space wrap>
              <strong>{direction === 'data-to-models' ? item.model_product.name : item.data_product.name}</strong>
              <Tag color={verified ? 'green' : item.current_status === 'static_schema_incompatible' ? 'red' : 'blue'}>
                {statusLabels[item.current_status] || item.current_status}
              </Tag>
              <Tag>{verified ? '平台验证证据' : runtime ? '运行证据' : '平台静态审查'}</Tag>
            </Space>
            <Typography.Paragraph type="secondary">{item.current_evidence?.evidence_note}</Typography.Paragraph>
            {runtime && <Space wrap>
              <Tag>固定范围：{assessment?.sample_count ?? '历史记录'} 个样本</Tag>
              {assessment?.correct_count !== undefined && <Tag>正确：{assessment.correct_count}</Tag>}
              {assessment?.aggregate_metrics?.accuracy && <Tag>聚合 accuracy：{assessment.aggregate_metrics.accuracy}</Tag>}
              {assessment?.resource_usage?.device && <Tag>设备：{assessment.resource_usage.device.toUpperCase()}</Tag>}
              {runId && <Tag>Run：{runId.slice(0, 8)}</Tag>}
              {packageId && <Tag>Package：{packageId.slice(0, 8)}</Tag>}
              {assessment?.platform_verification?.review_status && <Tag>{assessment.platform_verification.review_status}</Tag>}
            </Space>}
            {!!item.current_evidence?.transformation_requirements.length &&
              <Typography.Text>必要转换：{item.current_evidence.transformation_requirements.map((entry) => entry.name).join('；')}</Typography.Text>}
            {!!item.current_evidence?.blocking_reasons.length &&
              <Typography.Text type="danger">阻断原因：{item.current_evidence.blocking_reasons.join('；')}</Typography.Text>}
            <div>
              <Tag color={runtime ? 'green' : undefined}>实际运行：{runtime ? '有历史证据' : '尚无证据'}</Tag>
              <Tag color={verified ? 'green' : undefined}>平台验证：{verified ? '已完成' : '尚无证据'}</Tag>
            </div>
              </>
            })()}
          </div>)}
        </Space>}
  </Card>
}
