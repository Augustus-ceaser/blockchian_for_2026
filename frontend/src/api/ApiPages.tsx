import {
  AuditOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  CodeSandboxOutlined,
  DatabaseOutlined,
  FileProtectOutlined,
  LockOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { secureUuid } from '../lib/secureUuid'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  List,
  Row,
  Space,
  Spin,
  Statistic,
  Steps,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { MetricCard } from '../components/MetricCard'
import { PageHeading } from '../components/PageHeading'
import { StatusPill } from '../components/StatusPill'
import { useRoadshow } from '../roadshow/RoadshowContext'
import { ApiError, apiGet, startPathmnistRun } from './client'
import type { ApiRecord, CollectionResponse, DemoRunResponse, OverviewResponse } from './types'

function useApi<T>(path: string, refresh = 0) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    apiGet<T>(path, controller.signal)
      .then(setData)
      .catch((reason) => {
        if (reason.name !== 'AbortError') setError(reason.message || '实时数据加载失败')
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [path, refresh])
  return { data, error, loading }
}

function Boundary({ loading, error, children }: { loading: boolean; error: string; children: ReactNode }) {
  if (loading) return <Card className="content-card"><Flex justify="center" className="api-loading"><Spin description="读取真实业务状态…" /></Flex></Card>
  if (error) return <Alert type="error" showIcon title="无法读取后端数据" description={error} />
  return <>{children}</>
}

function LiveHeading({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return <PageHeading eyebrow="真实后端模式 · PostgreSQL 业务状态" title={title} description={description} actions={actions} />
}

export function ApiOverviewPage() {
  const { data, error, loading } = useApi<OverviewResponse>('/overview')
  return <div className="page-stack">
    <LiveHeading title="可信流通工作台" description="当前页面直接读取合同、任务、隔离制品和审计事件；不从前端阶段变量推导状态。" />
    <Boundary loading={loading} error={error}>{data && <>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} xl={6}><MetricCard label="数据产品" value={data.counts.data_products || 0} detail="固定版本、受控计算" icon={<DatabaseOutlined />} tone="blue" /></Col>
        <Col xs={24} sm={12} xl={6}><MetricCard label="有效合约" value={data.counts.contracts || 0} detail="Revision承载真实状态" icon={<FileProtectOutlined />} tone="teal" /></Col>
        <Col xs={24} sm={12} xl={6}><MetricCard label="计算任务" value={data.counts.compute_jobs || 0} detail={String(data.latest_run?.status || '暂无运行')} icon={<CodeSandboxOutlined />} tone="purple" /></Col>
        <Col xs={24} sm={12} xl={6}><MetricCard label="审计事件" value={data.counts.audit_events || 0} detail={`${data.outbox.published}/${data.outbox.total} 已投递 · 链${data.audit_chain_valid ? '有效' : '异常'}`} icon={<AuditOutlined />} tone="amber" /></Col>
      </Row>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}><Card className="content-card" title="已验证的 v0.2 权威基线">
          <div className="result-metrics">
            <Statistic title="样本" value={data.verified_baseline_metrics.sample_count} suffix="张" />
            <Statistic title="准确率" value={Number(data.verified_baseline_metrics.accuracy) * 100} precision={1} suffix="%" />
            <Statistic title="平均置信度" value={Number(data.verified_baseline_metrics.mean_confidence) * 100} precision={2} suffix="%" />
          </div>
          <Alert type="warning" showIcon title={`Artifact保持 ${data.verified_baseline_metrics.artifact_status}`} description="指标来自冻结的已验证基线，不代表当前待执行Run已经完成，也不提供样本级结果或下载入口。" />
        </Card></Col>
        <Col xs={24} xl={9}><Card className="content-card" title="当前执行边界">
          <List size="small" dataSource={[
            ['硬隔离', data.capability.hard_isolation ? '已实现' : '未实现'],
            ['临床用途', data.capability.clinical_use ? '允许' : '不允许'],
            ['制品下载', data.capability.artifact_download_enabled ? '开放' : '关闭'],
            ['最新Run', String(data.latest_run?.status || '无')],
            ['执行Inbox', `${data.inbox.consumer_completed}/${data.inbox.consumer_total} 完成`],
            ['回调Inbox', `${data.inbox.callback_completed}/${data.inbox.callback_total} 完成`],
          ]} renderItem={(item) => <List.Item><span>{item[0]}</span><Tag>{item[1]}</Tag></List.Item>} />
        </Card></Col>
      </Row>
    </>}</Boundary>
  </div>
}

export function ApiProductsPage() {
  const { data, error, loading } = useApi<CollectionResponse<ApiRecord>>('/data-products')
  return <div className="page-stack">
    <LiveHeading title="数据产品目录" description="目录展示可发现的数据产品身份和已发布固定版本，而不是医院数据库或原始文件。" />
    <Boundary loading={loading} error={error}>{data && (data.total ? <Row gutter={[16, 16]}>{data.items.map((item) =>
      <Col xs={24} xl={12} key={item.id}><Card className="content-card" title={item.name} extra={<StatusPill value={item.status} />}>
        <p>{item.description}</p><Descriptions size="small" column={2}>
          <Descriptions.Item label="提供方">{item.provider}</Descriptions.Item><Descriptions.Item label="版本">{item.version}</Descriptions.Item>
          <Descriptions.Item label="使用模式">受控计算</Descriptions.Item><Descriptions.Item label="分类">{item.classification}</Descriptions.Item>
        </Descriptions><Button type="link"><Link to={`/products/${item.id}`}>查看固定版本与资源</Link></Button>
      </Card></Col>)}</Row> : <Empty description="暂无可发现的数据产品" />)}</Boundary>
  </div>
}

export function ApiProductDetailPage() {
  const { id = '' } = useParams()
  const { data, error, loading } = useApi<ApiRecord>(`/data-products/${id}`)
  const version = data?.versions?.[0]
  return <div className="page-stack"><LiveHeading title={data?.name || '数据产品详情'} description="产品身份、不可变版本、来源资源与默认使用策略。" />
    <Boundary loading={loading} error={error}>{data && <>
      <Card className="content-card"><Descriptions bordered column={2}>
        <Descriptions.Item label="提供方">{data.provider}</Descriptions.Item><Descriptions.Item label="产品状态"><StatusPill value={data.status} /></Descriptions.Item>
        <Descriptions.Item label="固定版本">{version?.label}</Descriptions.Item><Descriptions.Item label="版本状态">{version?.status}</Descriptions.Item>
        <Descriptions.Item label="版本摘要" span={2}><Typography.Text copyable>{version?.snapshot_digest}</Typography.Text></Descriptions.Item>
      </Descriptions></Card>
      <Card className="content-card" title="数据资源"><Table rowKey="id" pagination={false} dataSource={data.resources || []} columns={[
        { title: '资源', dataIndex: 'name' }, { title: '类型', dataIndex: 'type' }, { title: '模态', dataIndex: 'modality' }, { title: '格式', dataIndex: 'format' }, { title: '范围', dataIndex: 'scope', render: (value: ApiRecord) => value ? JSON.stringify(value) : '—' },
      ]} /></Card>
      <Card className="content-card" title="默认使用策略"><pre className="policy-json">{JSON.stringify(version?.policy || {}, null, 2)}</pre></Card>
    </>}</Boundary>
  </div>
}

export function ApiApplicationsPage() {
  const { data, error, loading } = useApi<CollectionResponse<ApiRecord>>('/applications')
  return <div className="page-stack"><LiveHeading title="使用申请" description="这里展示申请意图和固定数据产品版本；申请获批不等于获得数据访问权。" />
    <Boundary loading={loading} error={error}>{data && <Card className="content-card"><Table rowKey="id" dataSource={data.items} pagination={false} columns={[
      { title: '申请编号', dataIndex: 'number' }, { title: '用途', dataIndex: 'purpose' }, { title: '固定算法', dataIndex: 'algorithm' },
      { title: '数据产品', dataIndex: 'products', render: (values: string[]) => values?.join('、') }, { title: '次数', dataIndex: 'requested_run_limit' },
      { title: '状态', dataIndex: 'status', render: (value: string) => <StatusPill value={value} /> },
    ]} /></Card>}</Boundary>
  </div>
}

export function ApiContractsPage() {
  const { data, error, loading } = useApi<CollectionResponse<ApiRecord>>('/contracts')
  const [detail, setDetail] = useState<ApiRecord | null>(null)
  const [detailError, setDetailError] = useState('')
  const openDetail = useCallback((id: string) => {
    setDetailError('')
    apiGet<ApiRecord>(`/contracts/${id}`).then(setDetail).catch((reason) => setDetailError(reason.message))
  }, [])
  return <div className="page-stack"><LiveHeading title="数字合约" description="Contract是系列身份，Revision承载生效状态、对象、策略、节点能力绑定和签署证据。" />
    <Boundary loading={loading} error={error}>{data && <Card className="content-card"><Table rowKey="id" dataSource={data.items} pagination={false} columns={[
      { title: '合同编号', dataIndex: 'number' }, { title: '协议', dataIndex: 'name' }, { title: 'Revision', dataIndex: 'revision_no' },
      { title: '状态', dataIndex: 'status', render: (value: string) => <StatusPill value={value} /> },
      { title: '', dataIndex: 'id', render: (id: string) => <Button type="link" onClick={() => openDetail(id)}>查看执行约束</Button> },
    ]} /></Card>}</Boundary>
    <Drawer size="large" open={Boolean(detail || detailError)} onClose={() => { setDetail(null); setDetailError('') }} title="合同执行约束">
      {detailError ? <Alert type="error" title={detailError} /> : detail && <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Descriptions column={1} bordered><Descriptions.Item label="合同">{detail.number}</Descriptions.Item><Descriptions.Item label="Revision状态">{detail.status}</Descriptions.Item><Descriptions.Item label="内容摘要"><Typography.Text copyable>{detail.content_digest}</Typography.Text></Descriptions.Item></Descriptions>
        <Card size="small" title="合同对象"><List dataSource={detail.objects || []} renderItem={(item: ApiRecord) => <List.Item>{item.name} · 版本 {String(item.version_id).slice(0, 8)}…</List.Item>} /></Card>
        <Card size="small" title="策略"><List dataSource={detail.policies || []} renderItem={(item: ApiRecord) => <List.Item><Tag color={item.effect === 'deny' ? 'red' : 'green'}>{item.effect}</Tag>{item.action}</List.Item>} /></Card>
        <Alert type="info" showIcon title="签署不等于数据交付" description="只有ACTIVE Revision允许创建任务；具体运行仍需次数、节点能力和当前有效性复核。" />
      </Space>}
    </Drawer>
  </div>
}

const runStep: Record<string, number> = { prepared: 0, reserved: 1, dispatched: 2, running: 3, succeeded: 4, failed: 4, interrupted: 4, cancelled: 4 }

export function ApiComputePage() {
  const [refresh, setRefresh] = useState(0)
  const [runResult, setRunResult] = useState<DemoRunResponse | null>(null)
  const [runState, setRunState] = useState<ApiRecord | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [commandError, setCommandError] = useState('')
  const jobs = useApi<CollectionResponse<ApiRecord>>('/compute-jobs', refresh)
  const overview = useApi<OverviewResponse>('/overview', refresh)

  useEffect(() => {
    if (!runResult) return
    const load = () => apiGet<ApiRecord>(runResult.status_url.replace('/api/v1', '')).then((value) => {
      setRunState(value)
      setRefresh((current) => current + 1)
    }).catch(() => undefined)
    load()
    const timer = window.setInterval(load, 1800)
    return () => window.clearInterval(timer)
  }, [runResult])

  const start = async () => {
    setSubmitting(true); setCommandError('')
    const key = `browser-${secureUuid()}`
    try {
      const response = await startPathmnistRun(key)
      setRunResult(response); setRunState({ id: response.run_id, status: response.run_status }); setRefresh((value) => value + 1)
    } catch (reason) {
      setCommandError(reason instanceof ApiError ? reason.message : '命令提交失败')
    } finally { setSubmitting(false) }
  }

  const latest = runState || jobs.data?.items?.[0]?.run
  const metrics = overview.data?.verified_baseline_metrics
  return <div className="page-stack">
    <LiveHeading title="可信计算" description="Compute只执行ACTIVE Contract确定的规则。新任务通过Dispatcher与本地固定执行器推进，结果默认隔离。" actions={<Button type="primary" loading={submitting} onClick={start}>启动 PathMNIST 20张受控推理</Button>} />
    {commandError && <Alert type="warning" showIcon title="任务未启动" description={commandError} />}
    <Boundary loading={jobs.loading} error={jobs.error}>{jobs.data && <>
      <Card className="content-card" title="真实运行状态" extra={<Button icon={<ReloadOutlined />} onClick={() => setRefresh((value) => value + 1)}>刷新</Button>}>
        {latest ? <>
          <Steps current={runStep[String(latest.status)] ?? 0} status={latest.status === 'failed' ? 'error' : 'process'} items={[
            { title: '任务就绪' }, { title: '额度预留' }, { title: '可信投递' }, { title: 'CPU推理' }, { title: '隔离制品' },
          ]} />
          <Descriptions size="small" column={3} className="api-run-summary">
            <Descriptions.Item label="Run">{String(latest.id).slice(0, 8)}…</Descriptions.Item><Descriptions.Item label="状态"><StatusPill value={String(latest.status)} /></Descriptions.Item><Descriptions.Item label="次数">{latest.reservation_ordinal || runResult?.run_count.ordinal || '—'} / {latest.run_limit || runResult?.run_count.limit || '—'}</Descriptions.Item>
          </Descriptions>
        </> : <Empty description="尚无任务，点击右上角创建固定受控运行" />}
      </Card>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}><Card className="content-card" title="权威基线聚合结果（已验证）">
          {metrics && <div className="result-metrics"><Statistic title="样本" value={metrics.sample_count} /><Statistic title="准确率" value={Number(metrics.accuracy) * 100} suffix="%" /><Statistic title="平均置信度" value={Number(metrics.mean_confidence) * 100} precision={2} suffix="%" /></div>}
          <Alert type="info" showIcon title="指标只来自冻结基线" description="新Run完成前不会把基线指标伪装成新运行结果。" />
        </Card></Col>
        <Col xs={24} xl={10}><Card className="content-card" title="制品边界"><div className="control-list">
          <div><LockOutlined /><span><strong>默认状态</strong>quarantined</span></div><div><CheckCircleOutlined /><span><strong>审核</strong>最多一个终态决定</span></div><div><SafetyCertificateOutlined /><span><strong>出域</strong>未开放</span></div>
        </div><Button disabled block>下载制品（未开放）</Button></Card></Col>
      </Row>
      <Card className="content-card" title="任务记录"><Table rowKey="id" dataSource={jobs.data.items} pagination={false} columns={[
        { title: '算法', dataIndex: 'algorithm' }, { title: '用途', dataIndex: 'purpose' }, { title: 'Job状态', dataIndex: 'status', render: (value: string) => <StatusPill value={value} /> },
        { title: 'Run状态', dataIndex: ['run', 'status'], render: (value: string) => value ? <StatusPill value={value} /> : '—' },
      ]} /></Card>
    </>}</Boundary>
  </div>
}

export function ApiConnectorsPage() {
  const { data, error, loading } = useApi<CollectionResponse<ApiRecord>>('/connectors')
  const { identity } = useRoadshow()
  const controlPath = identity === 'space_operator'
    ? '/portal/operator/connectors'
    : identity === 'data_provider'
      ? '/portal/hospital/connectors'
      : null
  const controlLabel = identity === 'space_operator' ? '进入运营方控制与证据中心' : '进入本组织控制与证据中心'
  return <div className="page-stack"><LiveHeading
    title="Connector 兼容记录"
    description="查看兼容接口返回的节点记录；节点管理请使用 Hospital Connector 控制面。"
    actions={controlPath ? <Link to={controlPath}><Button type="primary">{controlLabel}</Button></Link> : undefined}
  />
    <Boundary loading={loading} error={error}>{data && <Row gutter={[16, 16]}>{data.items.map((item) => <Col xs={24} xl={12} key={item.id}><Card className="content-card" title={<Space><CloudServerOutlined />{item.name}</Space>} extra={<StatusPill value={item.runtime_status} />}>
      <Descriptions size="small"><Descriptions.Item label="组织">{item.organization}</Descriptions.Item><Descriptions.Item label="认证">{item.verification_status}</Descriptions.Item></Descriptions>
      <Space wrap>{item.capabilities.map((capability: ApiRecord) => <Tag color={capability.status === 'verified' ? 'green' : 'default'} key={`${capability.code}-${capability.version}`}>{capability.code} {capability.version}</Tag>)}</Space>
    </Card></Col>)}</Row>}</Boundary>
  </div>
}

export function ApiAuditPage() {
  const { data, error, loading } = useApi<CollectionResponse<ApiRecord>>('/audit-events?limit=100')
  const items = useMemo(() => data?.items || [], [data])
  return <div className="page-stack"><LiveHeading title="审计中心" description="AuditEvent是追加式业务证据；数据库内哈希链提供篡改检测线索，不等于第三方存证。" />
    <Boundary loading={loading} error={error}>{data && (items.length ? <Card className="content-card"><Timeline items={items.map((item) => ({
      color: item.result === 'success' ? 'green' : 'red', content: <div><Space><strong>{item.event_type}</strong><Tag>#{item.sequence}</Tag><StatusPill value={item.result} /></Space><p>{item.actor} · {item.subject_type} · {String(item.subject_id).slice(0, 8)}…</p><Typography.Text type="secondary">{item.occurred_at}</Typography.Text></div>,
    }))} /></Card> : <Empty description="暂无审计事件" />)}</Boundary>
  </div>
}
