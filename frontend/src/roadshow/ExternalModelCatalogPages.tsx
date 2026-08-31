import { Button, Descriptions, Drawer, Input, Select, Space, Table, Tag, Typography, message } from 'antd'
import { CloudSyncOutlined, RobotOutlined, SearchOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useState } from 'react'
import { platformCommand, platformGet } from './api'
import { useRoadshow } from './RoadshowContext'

type Source = { id: string; display_name: string; status: string; resource_kind: string; last_successful_catalog_version: string | null }
type Run = { status: string; http_status: number | null; inserted_count: number; updated_count: number; unchanged_count: number; stale_count: number; error_summary: string | null }
type ModelRow = { id: string; external_model_id: string; canonical_name: string; model_categories: string[]; modalities: string[]; task_types: string[]; framework: string | null; license_status: string; weights_status: string; estimated_weights_size_bytes: number | null; execution_status: string }
type Detail = ModelRow & { paper_title: string | null; paper_url: string | null; code_repository_url: string | null; model_card_url: string | null; architecture: string | null; input_schema: string | null; output_schema: string | null; clinical_use_status: string; intended_use_summary: string | null; limitations_summary: string | null; training_dataset_references: string[]; evaluation_dataset_references: string[]; versions: Array<{ catalog_version: string; record_digest: string }> }
const externalLink = (url: string | null, label: string) => url ? <a href={url} target="_blank" rel="noreferrer noopener">{label}（外部链接）</a> : '未提供'

export function ExternalModelCatalogPage({ operator = false }: { operator?: boolean }) {
  const { identity } = useRoadshow()
  const [items, setItems] = useState<ModelRow[]>([]); const [total, setTotal] = useState(0)
  const [query, setQuery] = useState(''); const [category, setCategory] = useState<string>(); const [weights, setWeights] = useState<string>()
  const [detail, setDetail] = useState<Detail | null>(null); const [sources, setSources] = useState<Source[]>([]); const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const parameters = new URLSearchParams({ limit: '100' })
      if (query.trim()) parameters.set('q', query.trim()); if (category) parameters.set('category', category); if (weights) parameters.set('weights_status', weights)
      const result = await platformGet<{ items: ModelRow[]; total: number }>(`/external-model-catalog/models?${parameters}`, identity)
      setItems(result.items); setTotal(result.total)
      if (operator) {
        const sourceResult = await platformGet<{ items: Source[] }>('/external-model-catalog/sources', identity)
        setSources(sourceResult.items)
      }
    } finally { setLoading(false) }
  }, [category, identity, operator, query, weights])
  useEffect(() => { void load() }, [load])
  const synchronize = async () => {
    setLoading(true)
    try {
      let source = sources[0]
      if (!source) source = await platformCommand<Source>('/external-model-catalog/sources/configured', identity, crypto.randomUUID())
      const run = await platformCommand<Run>(`/external-model-catalog/sources/${source.id}/sync`, identity, crypto.randomUUID())
      run.status === 'failed' ? message.error(run.error_summary || '模型目录同步失败') : message.success(run.status === 'not_modified' ? '目录未变化（HTTP 304）' : '模型目录同步完成')
      await load()
    } finally { setLoading(false) }
  }
  const columns = [
    { title: '模型', width: 190, render: (_: unknown, row: ModelRow) => <Button type="link" onClick={async () => setDetail(await platformGet<Detail>(`/external-model-catalog/models/${row.id}`, identity))}>{row.canonical_name}</Button> },
    { title: '类别', dataIndex: 'model_categories', width: 190, render: (values: string[]) => <Space wrap>{values.map((value) => <Tag key={value}>{value}</Tag>)}</Space> },
    { title: '模态 / 任务', width: 220, render: (_: unknown, row: ModelRow) => [...row.modalities, ...row.task_types].slice(0, 3).join('、') },
    { title: '框架', dataIndex: 'framework', width: 100, render: (value: string | null) => value || '未知' },
    { title: '许可证', dataIndex: 'license_status', width: 120, render: (value: string) => <Tag color={value === 'unknown' ? 'gold' : 'blue'}>{value}</Tag> },
    { title: '权重状态', dataIndex: 'weights_status', width: 130, render: (value: string) => <Tag>{value}</Tag> },
    { title: '平台状态', width: 170, render: () => <Space size={[4, 4]} wrap><Tag>未物化</Tag><Tag color="orange">不可执行</Tag></Space> },
  ]
  return <div className="external-catalog-page">
    <div className="external-catalog-heading"><div><Typography.Title level={3}><RobotOutlined /> 公共医疗 AI 模型候选目录</Typography.Title><Space size={8} wrap><Typography.Text type="secondary">已同步 {total} 条模型元数据候选</Typography.Text><Tag color="blue">目录元数据</Tag></Space></div>{operator && <Button type="primary" icon={<CloudSyncOutlined />} loading={loading} onClick={() => void synchronize()}>{sources.length ? '立即同步' : '配置并同步'}</Button>}</div>
    {operator && sources[0] && <div className="external-catalog-source"><Space wrap><Tag>模型元数据</Tag><strong>{sources[0].display_name}</strong><Typography.Text type="secondary">目录版本 {sources[0].last_successful_catalog_version || '未同步'}</Typography.Text></Space></div>}
    <div className="external-catalog-filters external-catalog-filters--models"><Input allowClear prefix={<SearchOutlined />} placeholder="搜索模型或论文" value={query} onChange={(event) => setQuery(event.target.value)} /><Select allowClear placeholder="模型类别" value={category} onChange={setCategory} options={['pathology_foundation', 'vision_language', 'spatial_transcriptomics', 'cell_segmentation', 'medical_segmentation'].map((value) => ({ value }))} /><Select allowClear placeholder="权重状态" value={weights} onChange={setWeights} options={['public_available', 'gated', 'request_required', 'not_released', 'unknown'].map((value) => ({ value }))} /></div>
    <Table rowKey="id" loading={loading} columns={columns} dataSource={items} scroll={{ x: 1060 }} pagination={false} />
    <Drawer width="min(760px, 100vw)" title={detail?.canonical_name} open={Boolean(detail)} onClose={() => setDetail(null)}>{detail && <Descriptions bordered size="small" column={1}>
      <Descriptions.Item label="外部 ID">{detail.external_model_id}</Descriptions.Item><Descriptions.Item label="目录版本">{detail.versions[0]?.catalog_version}</Descriptions.Item><Descriptions.Item label="记录摘要">{detail.versions[0]?.record_digest}</Descriptions.Item>
      <Descriptions.Item label="架构">{detail.architecture || '未知'}</Descriptions.Item><Descriptions.Item label="输入">{detail.input_schema || '未知'}</Descriptions.Item><Descriptions.Item label="输出">{detail.output_schema || '未知'}</Descriptions.Item>
      <Descriptions.Item label="权重状态">{detail.weights_status} / 未下载</Descriptions.Item><Descriptions.Item label="执行状态">{detail.execution_status} / 不可执行</Descriptions.Item><Descriptions.Item label="临床状态">{detail.clinical_use_status}</Descriptions.Item>
      <Descriptions.Item label="作者声明训练数据">{detail.training_dataset_references.join('、') || '未记录'}（External declaration）</Descriptions.Item><Descriptions.Item label="作者声明评估数据">{detail.evaluation_dataset_references.join('、') || '未记录'}（External declaration）</Descriptions.Item>
      <Descriptions.Item label="用途">{detail.intended_use_summary || '未记录'}</Descriptions.Item><Descriptions.Item label="限制">{detail.limitations_summary || '未记录'}</Descriptions.Item>
      <Descriptions.Item label="论文">{externalLink(detail.paper_url, detail.paper_title || '论文')}</Descriptions.Item><Descriptions.Item label="官方仓库">{externalLink(detail.code_repository_url, '打开仓库')}</Descriptions.Item><Descriptions.Item label="模型卡">{externalLink(detail.model_card_url, '打开模型卡')}</Descriptions.Item>
    </Descriptions>}</Drawer>
  </div>
}
