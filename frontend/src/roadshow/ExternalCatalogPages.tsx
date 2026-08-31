import { Button, Descriptions, Drawer, Empty, Input, Select, Space, Table, Tag, Typography, message } from 'antd'
import { CloudSyncOutlined, DatabaseOutlined, SearchOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useRef, useState } from 'react'
import { platformCommand, platformGet } from './api'
import { useRoadshow } from './RoadshowContext'

type Source = { id: string; display_name: string; status: string; last_successful_catalog_version: string | null; last_synced_at: string | null }
type RecordRow = {
  id: string; external_id: string; canonical_name: string; modalities: string[]; disease_areas: string[]; organs: string[]
  sample_count: number | null; approximate_size_bytes: number | null; license_name: string | null; license_status: string
  access_level: string; link_status: string; quality_flags: string[]; first_seen_at: string; last_seen_at: string
}
type Detail = RecordRow & {
  official_source_name: string | null; official_source_url: string | null; catalog_source_url: string | null
  task_types: string[]; species: string | null; file_count: number | null; data_formats: string[]
  registration_required: boolean | null; versions: Array<{ catalog_version: string; record_digest: string; observed_at: string; is_current: boolean }>
}
type Run = {
  status: string; http_status: number | null; received_record_count: number | null; inserted_count: number
  updated_count: number; unchanged_count: number; stale_count: number; error_summary: string | null
}

const SEARCH_DEBOUNCE_MS = 275

function useDebouncedText(value: string): string {
  const [debouncedValue, setDebouncedValue] = useState(value)

  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedValue(value), SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timeout)
  }, [value])

  return debouncedValue
}

const formatTime = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚未同步'
const formatSize = (value: number | null) => value === null ? '未知' : value < 1024 ** 3
  ? `${(value / 1024 ** 2).toFixed(1)} MB`
  : `${(value / 1024 ** 3).toFixed(1)} GB`

export function ExternalCatalogPage({ operator = false }: { operator?: boolean }) {
  const { identity } = useRoadshow()
  const [records, setRecords] = useState<RecordRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [diseaseOrOrgan, setDiseaseOrOrgan] = useState('')
  const debouncedQuery = useDebouncedText(query)
  const debouncedDiseaseOrOrgan = useDebouncedText(diseaseOrOrgan)
  const [modality, setModality] = useState<string>()
  const [license, setLicense] = useState<string>()
  const [quality, setQuality] = useState<string>()
  const [initialLoading, setInitialLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [syncing, setSyncing] = useState(false)
  const latestRequestId = useRef(0)
  const pageSize = 50

  const load = useCallback(async () => {
    const requestId = ++latestRequestId.current
    try {
      const parameters = new URLSearchParams({ offset: String((page - 1) * pageSize), limit: String(pageSize) })
      if (debouncedQuery.trim()) parameters.set('q', debouncedQuery.trim())
      if (debouncedDiseaseOrOrgan.trim()) parameters.set('disease_or_organ', debouncedDiseaseOrOrgan.trim())
      if (modality) parameters.set('modality', modality)
      if (license) parameters.set('license_status', license)
      if (quality) parameters.set('quality_flag', quality)
      const result = await platformGet<{ items: RecordRow[]; total: number }>(`/external-catalog/datasets?${parameters}`, identity)
      if (requestId !== latestRequestId.current) return
      setRecords(result.items)
      setTotal(result.total)
      if (operator) {
        const sourceResult = await platformGet<{ items: Source[] }>('/external-catalog/sources', identity)
        if (requestId !== latestRequestId.current) return
        setSources(sourceResult.items)
      }
      setLoadError(null)
    } catch (error) {
      if (requestId !== latestRequestId.current) return
      setLoadError(error instanceof Error ? error.message : '目录加载失败，请稍后重试')
    } finally {
      if (requestId === latestRequestId.current) setInitialLoading(false)
    }
  }, [debouncedDiseaseOrOrgan, debouncedQuery, identity, license, modality, operator, page, quality])

  useEffect(() => {
    void load()
    return () => { latestRequestId.current += 1 }
  }, [load])

  const synchronize = async () => {
    setSyncing(true)
    try {
      let source = sources[0]
      if (!source) {
        source = await platformCommand<Source>('/external-catalog/sources/configured', identity, crypto.randomUUID())
      }
      const run = await platformCommand<Run>(`/external-catalog/sources/${source.id}/sync`, identity, crypto.randomUUID())
      if (run.status === 'failed') message.error(run.error_summary || '目录同步失败')
      else message.success(run.status === 'not_modified' ? '目录未变化（HTTP 304）' : '目录同步完成')
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '同步失败')
    } finally {
      setSyncing(false)
    }
  }

  const columns = [
    { title: '数据集', width: 280, render: (_: unknown, row: RecordRow) => <Button type="link" className="external-catalog-name" onClick={async () => setDetail(await platformGet<Detail>(`/external-catalog/datasets/${row.id}`, identity))}>{row.canonical_name}</Button> },
    { title: '模态', dataIndex: 'modalities', width: 150, render: (values: string[]) => values.length ? <Space size={[4, 4]} wrap>{values.map((value) => <Tag key={value}>{value}</Tag>)}</Space> : '未知' },
    { title: '疾病 / 器官', width: 210, render: (_: unknown, row: RecordRow) => [...row.disease_areas, ...row.organs].join('、') || '未知' },
    { title: '样本数', dataIndex: 'sample_count', width: 100, render: (value: number | null) => value?.toLocaleString() ?? '未知' },
    { title: '体积', dataIndex: 'approximate_size_bytes', width: 100, render: formatSize },
    { title: '许可 / 访问', width: 160, render: (_: unknown, row: RecordRow) => <Space direction="vertical" size={2}><Tag color={row.license_status === 'unknown' ? 'gold' : 'green'}>{row.license_name || row.license_status}</Tag><Typography.Text type="secondary">{row.access_level}</Typography.Text></Space> },
    { title: '质量', dataIndex: 'quality_flags', width: 120, render: (values: string[]) => values.length ? <Tag color="orange">{values.length} 项待核</Tag> : <Tag color="green">无标记</Tag> },
  ]

  return <div className="external-catalog-page">
    <div className="external-catalog-heading">
      <div><Typography.Title level={3}><DatabaseOutlined /> 公共候选数据目录</Typography.Title><Space size={8} wrap><Typography.Text type="secondary">共 {total.toLocaleString()} 条已同步元数据</Typography.Text><Tag color="blue">目录元数据</Tag></Space></div>
      {operator && <Button type="primary" icon={<CloudSyncOutlined />} loading={syncing} onClick={() => void synchronize()}>{sources.length ? '立即同步' : '配置并同步'}</Button>}
    </div>
    {operator && sources[0] && <div className="external-catalog-source"><Space wrap><Tag color={sources[0].status === 'ready' ? 'green' : 'red'}>{sources[0].status === 'ready' ? '已同步' : sources[0].status}</Tag><strong>{sources[0].display_name}</strong><Typography.Text type="secondary">最近同步 {formatTime(sources[0].last_synced_at)}</Typography.Text></Space></div>}
    <div className="external-catalog-filters external-catalog-filters--datasets">
      <Input allowClear prefix={<SearchOutlined />} placeholder="搜索名称或外部 ID" value={query} onChange={(event) => { setPage(1); setQuery(event.target.value) }} />
      <Input allowClear prefix={<SearchOutlined />} placeholder="输入疾病或器官，如 Breast、Brain" maxLength={120} value={diseaseOrOrgan} onChange={(event) => { setPage(1); setDiseaseOrOrgan(event.target.value) }} />
      <Select allowClear placeholder="模态" value={modality} onChange={(value) => { setPage(1); setModality(value) }} options={['CT', 'MR', 'MRI', 'X-Ray', 'Pathology', 'Ultrasound'].map((value) => ({ value }))} />
      <Select allowClear placeholder="许可证" value={license} onChange={(value) => { setPage(1); setLicense(value) }} options={[{ value: 'unknown', label: '待审核 / unknown' }]} />
      <Select allowClear placeholder="质量标记" value={quality} onChange={(value) => { setPage(1); setQuality(value) }} options={[{ value: 'license_missing', label: '缺少许可' }, { value: 'official_source_unknown', label: '官方来源未知' }, { value: 'sample_count_unknown', label: '样本数未知' }]} />
    </div>
    {loadError && <Typography.Text type="danger">目录加载失败：{loadError}</Typography.Text>}
    <Table rowKey="id" loading={initialLoading} columns={columns} dataSource={records} scroll={{ x: 1120 }} locale={{ emptyText: <Empty description="尚无已同步候选目录" /> }} pagination={{ current: page, pageSize, total, showSizeChanger: false, onChange: setPage }} />
    <Drawer width="min(680px, 100vw)" title={detail?.canonical_name} open={Boolean(detail)} onClose={() => setDetail(null)}>
      {detail && <Descriptions column={1} bordered size="small">
        <Descriptions.Item label="外部 ID">{detail.external_id}</Descriptions.Item>
        <Descriptions.Item label="模态">{detail.modalities.join('、') || '未知'}</Descriptions.Item>
        <Descriptions.Item label="疾病">{detail.disease_areas.join('、') || '未知'}</Descriptions.Item>
        <Descriptions.Item label="器官">{detail.organs.join('、') || '未知'}</Descriptions.Item>
        <Descriptions.Item label="许可">{detail.license_name || detail.license_status}</Descriptions.Item>
        <Descriptions.Item label="访问级别">{detail.access_level}</Descriptions.Item>
        <Descriptions.Item label="链接状态">{detail.link_status}</Descriptions.Item>
        <Descriptions.Item label="质量标记">{detail.quality_flags.join('、') || '无'}</Descriptions.Item>
        <Descriptions.Item label="目录版本">{detail.versions[0]?.catalog_version || '未知'}</Descriptions.Item>
        <Descriptions.Item label="记录摘要">{detail.versions[0]?.record_digest || '未知'}</Descriptions.Item>
        <Descriptions.Item label="官方来源">{detail.official_source_name || detail.official_source_url || '未知'}</Descriptions.Item>
        <Descriptions.Item label="首次发现">{formatTime(detail.first_seen_at)}</Descriptions.Item>
        <Descriptions.Item label="最近同步">{formatTime(detail.last_seen_at)}</Descriptions.Item>
        <Descriptions.Item label="已下载"><Tag>否</Tag></Descriptions.Item>
        <Descriptions.Item label="正式 DataProduct"><Tag>否</Tag></Descriptions.Item>
        <Descriptions.Item label="可执行"><Tag>否</Tag></Descriptions.Item>
      </Descriptions>}
    </Drawer>
  </div>
}
