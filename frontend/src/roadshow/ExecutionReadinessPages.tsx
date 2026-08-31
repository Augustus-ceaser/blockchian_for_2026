import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Form,
  Input,
  List,
  Space,
  Spin,
  Steps,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import { secureUuid } from '../lib/secureUuid'
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CodeSandboxOutlined,
  EyeOutlined,
  FileSearchOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { platformCommand, platformGet } from './api'
import { createSingleFlight, startAbortableLoad } from './requestLifecycle'
import { useRoadshow } from './RoadshowContext'

const { Paragraph, Text, Title } = Typography

type CheckResult = {
  code: string
  result: 'PASS' | 'WARNING' | 'BLOCKER'
  expected: unknown
  actual: unknown
  source: string
  message: string
}

type ReadinessRecord = {
  id: string
  type: string
  target_digest: string
  evidence_digest: string
  confirmed_at: string
  responsible_organization_id: string
  target: Record<string, unknown>
  evidence: Record<string, unknown>
}

type Eligibility = {
  id: string
  digest: string
  created_at: string
  valid_until: string
  checks: CheckResult[]
  environment: Record<string, unknown>
  snapshot: Record<string, unknown>
}

type ComputeJob = {
  id: string
  status: string
  created_at: string
  eligibility_snapshot_id: string
  eligibility_snapshot_digest: string
  slot_ordinal: number
  slot_digest: string
  run_limit: number
  run: null | {
    id: string
    status: string
    attempt_no: number
    reservation_ordinal: number
    prepared_at: string
    reserved_at: string
    dispatched_at: string | null
    started_at: string | null
    finished_at: string | null
    callbacks: Array<{
      id: string
      type: string
      status: string
      occurred_at: string
      outcome: string | null
      payload: Record<string, unknown>
    }>
  }
  artifact: null | {
    id: string
    status: string
    type: string
    content_digest: string
    size_bytes: number
    created_at: string
    release_package_count: number
    download_grant_count: number
  }
}

type ExecutionReadiness = {
  contract_id: string
  contract_number: string
  contract_revision_id: string
  contract_revision_no: number
  contract_status: string
  contract_digest: string
  application_id: string
  application_number: string
  requester_organization_id: string
  effective_from: string
  effective_until: string
  run_count: number
  data: {
    product: string
    version: string
    version_id: string
    snapshot_digest: string
    scope: Record<string, unknown>
  }
  model: {
    product: string
    version: string
    version_id: string
    snapshot_digest: string
    model_digest: string
    entrypoint_id: string
    runtime: Record<string, unknown>
  }
  parties: Array<{
    role: string
    organization_id: string
    organization_name: string
  }>
  readiness: {
    data_ready: ReadinessRecord | null
    model_ready: ReadinessRecord | null
    platform_ready: ReadinessRecord | null
  }
  readiness_state: string
  next_responsible: string
  blocker_count: number
  eligibility: Eligibility | null
  jobs: ComputeJob[]
  hard_isolation: false
}

type AuditEvent = {
  event_id: string
  event_type: string
  occurred_at: string
  actor_organization_id: string | null
  actor_user_id: string | null
  subject_type: string
  subject_id: string
  result: string
  evidence: Record<string, unknown>
  previous_hash: string | null
  current_hash: string
  outbox_status: string | null
}

const stateLabels: Record<string, string> = {
  waiting_for_data_ready: '待医院确认数据就绪',
  waiting_for_model_ready: '待模型方确认模型就绪',
  waiting_for_platform_check: '待平台资格检查',
  blocked: '存在阻断项',
  eligible: '具备执行资格',
  job_created: '任务已创建',
}

const stateColors: Record<string, string> = {
  waiting_for_data_ready: 'default',
  waiting_for_model_ready: 'default',
  waiting_for_platform_check: 'blue',
  blocked: 'red',
  eligible: 'purple',
  job_created: 'cyan',
}

function stateTag(state: string) {
  return <Tag color={stateColors[state] || 'default'}>{stateLabels[state] || state}</Tag>
}

function checkTag(result: CheckResult['result']) {
  const color = result === 'PASS' ? 'green' : result === 'WARNING' ? 'gold' : 'red'
  return <Tag color={color}>{result}</Tag>
}

function valueText(value: unknown) {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function useLoad<T>(path: string) {
  const { identity } = useRoadshow()
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    setLoading(true)
    setError('')
    return startAbortableLoad(
      (signal) => platformGet<T>(path, identity, signal),
      {
        onSuccess: setData,
        onError: (reason) => setError(reason instanceof Error ? reason.message : '读取失败'),
        onSettled: () => setLoading(false),
      },
    )
  }, [identity, nonce, path])
  return { data, error, loading, refresh: () => setNonce((value) => value + 1) }
}

export function ExecutionReadinessListPage() {
  const navigate = useNavigate()
  const state = useLoad<{ items: ExecutionReadiness[]; total: number }>('/execution-readiness')
  return <div className="page-stack">
    <Flex justify="space-between" align="center" wrap gap={12}>
      <div>
        <Title level={2}>执行准备</Title>
        <Paragraph type="secondary">查看本组织已生效合约的资产就绪与执行进度。</Paragraph>
      </div>
      <Button icon={<ReloadOutlined />} onClick={state.refresh}>刷新</Button>
    </Flex>
    {state.error && <Alert type="error" showIcon title="无法读取执行准备列表" description={state.error} />}
    <Spin spinning={state.loading}>
      <Card>
        <Table
          rowKey="contract_id"
          dataSource={state.data?.items || []}
          locale={{ emptyText: <Empty description="暂无生效合约需要准备" /> }}
          scroll={{ x: 1040 }}
          columns={[
            { title: '合约编号', dataIndex: 'contract_number' },
            { title: '数据产品', render: (_, item) => `${item.data.product || '-'} / ${item.data.version || '-'}` },
            { title: '模型产品', render: (_, item) => `${item.model.product || '-'} / ${item.model.version || '-'}` },
            { title: '运行次数', dataIndex: 'run_count' },
            { title: '准备状态', render: (_, item) => stateTag(item.readiness_state) },
            { title: '下一责任方', dataIndex: 'next_responsible' },
            { title: '任务', render: (_, item) => item.jobs.length ? `${item.jobs.length} 个 · ${item.jobs[0].status}` : '未创建' },
            { title: '操作', render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => navigate(`/execution/${item.contract_id}`)}>进入准备</Button> },
          ]}
        />
      </Card>
    </Spin>
  </div>
}

export function ExecutionReadinessDetailPage() {
  const { identity } = useRoadshow()
  const { contractId = '' } = useParams()
  const navigate = useNavigate()
  const [api, holder] = message.useMessage()
  const state = useLoad<ExecutionReadiness>(`/execution-readiness/${contractId}`)
  const audit = useLoad<{ items: AuditEvent[]; total: number }>(`/execution-readiness/${contractId}/audit-events`)
  const [accepted, setAccepted] = useState(false)
  const [confirmationNote, setConfirmationNote] = useState('')
  const [busy, setBusy] = useState('')
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null)
  const guard = useRef(createSingleFlight()).current
  const detail = state.data
  const providerAction = identity === 'data_provider'
    ? { type: 'data_ready', path: 'data-readiness', label: '确认数据执行就绪' }
    : identity === 'model_provider'
      ? { type: 'model_ready', path: 'model-readiness', label: '确认模型执行就绪' }
      : null
  const providerReadiness = providerAction
    ? detail?.readiness[providerAction.type as 'data_ready' | 'model_ready']
    : null
  const refresh = () => {
    state.refresh()
    audit.refresh()
  }
  const runCommand = async (action: 'provider' | 'eligibility' | 'job' | 'revoke' | 'dispatch', jobId?: string) => {
    if (!detail) return
    await guard.run(async () => {
      setBusy(action)
      try {
        if (action === 'provider' && providerAction) {
          await platformCommand(
            `/execution-readiness/${detail.contract_id}/${providerAction.path}`,
            identity,
            `phase5.5-ui:${providerAction.type}:${secureUuid()}`,
            { declaration_accepted: accepted, confirmation_note: confirmationNote.trim() },
          )
          api.success(`${providerAction.label}已记录`)
          setAccepted(false)
          setConfirmationNote('')
        } else if (action === 'eligibility') {
          await platformCommand(
            `/execution-readiness/${detail.contract_id}/eligibility-check`,
            identity,
            `phase5.5-ui:eligibility:${secureUuid()}`,
          )
          api.success('平台资格检查已完成')
        } else if (action === 'job' && detail.eligibility) {
          const result = await platformCommand<{ job_id: string }>(
            `/execution-readiness/${detail.contract_id}/jobs`,
            identity,
            `phase5.5-ui:job:${secureUuid()}`,
            { eligibility_snapshot_id: detail.eligibility.id },
          )
          api.success(`ComputeJob 已创建：${result.job_id}`)
        } else if (action === 'revoke' && providerReadiness) {
          await platformCommand(
            `/execution-readiness/readiness/${providerReadiness.id}/revoke`,
            identity,
            `phase5.5-ui:revoke:${secureUuid()}`,
            { reason_code: 'provider_withdrawn_before_dispatch' },
          )
          api.success('就绪确认已撤销，关联资格快照已失效')
        } else if (jobId) {
          const result = await platformCommand<{ run_id: string }>(
            `/execution-readiness/jobs/${jobId}/dispatch`,
            identity,
            `phase5.6-ui:dispatch:${secureUuid()}`,
          )
          api.success(`受控派发已请求：${result.run_id}`)
        }
        refresh()
      } catch (reason) {
        api.error(reason instanceof Error ? reason.message : '操作失败')
      } finally {
        setBusy('')
      }
    })
  }
  const progress = detail ? [
    { title: '数字合约已生效', status: detail.contract_status === 'active' ? 'finish' as const : 'error' as const },
    { title: '医院数据执行就绪', status: detail.readiness.data_ready ? 'finish' as const : 'wait' as const },
    { title: '模型执行资产就绪', status: detail.readiness.model_ready ? 'finish' as const : 'wait' as const },
    { title: '平台能力核验', status: detail.readiness.platform_ready ? 'finish' as const : 'wait' as const },
    { title: '执行资格', status: detail.eligibility ? 'finish' as const : 'wait' as const },
    { title: '计算任务', status: detail.jobs.length ? 'finish' as const : 'wait' as const },
  ] : []

  return <div className="page-stack phase55-detail-page">
    {holder}
    <Flex justify="space-between" align="center" wrap gap={12}>
      <div>
        <Title level={2}>执行准备详情</Title>
        <Paragraph type="secondary">{detail?.contract_number || contractId} · 后端权威状态</Paragraph>
      </div>
      <Space wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/execution')}>返回列表</Button>
        <Button icon={<ReloadOutlined />} onClick={refresh}>刷新</Button>
      </Space>
    </Flex>
    {state.error && <Alert type="error" showIcon title="无法读取执行准备详情" description={state.error} />}
    <Spin spinning={state.loading}>
      {detail && <>
        <Alert
          type={detail.jobs.length ? 'info' : detail.eligibility ? 'success' : 'warning'}
          showIcon
          title={detail.jobs.some((job) => job.status === 'succeeded') ? '固定模型执行成功，结果保持隔离' : detail.jobs.length ? '任务已创建，等待或正在受控执行' : stateLabels[detail.readiness_state] || detail.readiness_state}
          description={`下一责任方：${detail.jobs.some((job) => job.status === 'succeeded') ? '后续阶段的多方结果审核' : detail.next_responsible}。`}
        />
        <Card>
          <Descriptions bordered column={{ xs: 1, md: 2, xl: 3 }}>
            <Descriptions.Item label="合约编号">{detail.contract_number}</Descriptions.Item>
            <Descriptions.Item label="合约版本">v{detail.contract_revision_no}</Descriptions.Item>
            <Descriptions.Item label="状态">{stateTag(detail.readiness_state)}</Descriptions.Item>
            <Descriptions.Item label="数据产品">{detail.data.product} / {detail.data.version}</Descriptions.Item>
            <Descriptions.Item label="模型产品">{detail.model.product} / {detail.model.version}</Descriptions.Item>
            <Descriptions.Item label="运行次数上限">{detail.run_count}</Descriptions.Item>
            <Descriptions.Item label="有效期至">{new Date(detail.effective_until).toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="BLOCKER">{detail.blocker_count}</Descriptions.Item>
          </Descriptions>
        </Card>
        <Card title="准备进度">
          <Steps responsive items={progress} />
        </Card>
        <div className="phase55-readiness-grid">
          <Card title="数据执行就绪" extra={detail.readiness.data_ready ? <Tag color="green">READY</Tag> : <Tag>待确认</Tag>}>
            <List size="small">
              <List.Item><Text type="secondary">固定版本</Text><Text>{detail.data.version_id}</Text></List.Item>
              <List.Item><Text type="secondary">资源摘要</Text><Text code>{detail.data.snapshot_digest}</Text></List.Item>
              <List.Item><Text type="secondary">输入挂载</Text><Text>只读</Text></List.Item>
              <List.Item><Text type="secondary">原始数据下载</Text><Text type="danger">禁止</Text></List.Item>
            </List>
          </Card>
          <Card title="模型执行资产就绪" extra={detail.readiness.model_ready ? <Tag color="green">READY</Tag> : <Tag>待确认</Tag>}>
            <List size="small">
              <List.Item><Text type="secondary">固定版本</Text><Text>{detail.model.version_id}</Text></List.Item>
              <List.Item><Text type="secondary">模型摘要</Text><Text code>{detail.model.model_digest}</Text></List.Item>
              <List.Item><Text type="secondary">固定入口</Text><Text>{detail.model.entrypoint_id}</Text></List.Item>
              <List.Item><Text type="secondary">模型下载</Text><Text type="danger">禁止</Text></List.Item>
            </List>
          </Card>
        </div>
        {providerAction && !providerReadiness && <Card title={providerAction.label} className="phase55-provider-form">
          <Form layout="vertical">
            <Form.Item label="锁定合约版本"><Input value={`v${detail.contract_revision_no} · ${detail.contract_digest}`} readOnly /></Form.Item>
            <Form.Item label={identity === 'data_provider' ? '锁定数据与资源摘要' : '锁定模型与模型摘要'}>
              <Input value={identity === 'data_provider' ? `${detail.data.product} / ${detail.data.version} / ${detail.data.snapshot_digest}` : `${detail.model.product} / ${detail.model.version} / ${detail.model.model_digest}`} readOnly />
            </Form.Item>
            <Form.Item
              label={<span id="readiness-confirmation-note-label">确认意见</span>}
              htmlFor="readiness-confirmation-note"
              extra={<span id="readiness-confirmation-note-description">供当前操作复核；正式证据由服务端根据锁定资产生成。</span>}
            >
              <Input.TextArea
                id="readiness-confirmation-note"
                aria-labelledby="readiness-confirmation-note-label"
                aria-describedby="readiness-confirmation-note-description"
                value={confirmationNote}
                onChange={(event) => setConfirmationNote(event.target.value)}
                maxLength={240}
                rows={3}
              />
            </Form.Item>
            <Checkbox checked={accepted} onChange={(event) => setAccepted(event.target.checked)}>
              我已核对锁定版本、摘要、节点有效期和风险边界，并确认资产可在提供方控制节点内按合约执行。
            </Checkbox>
            <div className="phase55-form-actions">
              <Button type="primary" icon={<CheckCircleOutlined />} disabled={!accepted || confirmationNote.trim().length < 3} loading={busy === 'provider'} onClick={() => runCommand('provider')}>{providerAction.label}</Button>
            </div>
          </Form>
        </Card>}
        {providerAction && providerReadiness && <Card title="本方就绪证据">
          <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="确认时间">{new Date(providerReadiness.confirmed_at).toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="确认记录">{providerReadiness.id}</Descriptions.Item>
            <Descriptions.Item label="目标摘要">{providerReadiness.target_digest}</Descriptions.Item>
            <Descriptions.Item label="证据摘要">{providerReadiness.evidence_digest}</Descriptions.Item>
          </Descriptions>
          {!detail.jobs.length && <Button danger icon={<StopOutlined />} loading={busy === 'revoke'} onClick={() => runCommand('revoke')} style={{ marginTop: 16 }}>撤销本方就绪确认</Button>}
        </Card>}
        {identity === 'space_operator' && <Card title="平台执行资格检查">
          {!detail.readiness.data_ready || !detail.readiness.model_ready
            ? <Alert type="warning" showIcon title="提供方就绪尚未完成" description="平台不能代替医院或模型提供方确认资产就绪。" />
            : <Button type="primary" icon={<SafetyCertificateOutlined />} loading={busy === 'eligibility'} onClick={() => runCommand('eligibility')}>运行资格检查</Button>}
          {detail.eligibility && <div className="phase55-check-table">
            <Table
              pagination={false}
              rowKey="code"
              dataSource={detail.eligibility.checks}
              scroll={{ x: 900 }}
              columns={[
                { title: '检查项', dataIndex: 'code' },
                { title: '结果', dataIndex: 'result', render: checkTag },
                { title: '期望值', dataIndex: 'expected', render: valueText },
                { title: '实际值', dataIndex: 'actual', render: valueText },
                { title: '来源', dataIndex: 'source' },
                { title: '说明', dataIndex: 'message' },
              ]}
            />
          </div>}
        </Card>}
        {detail.eligibility && <Card title="不可变执行资格快照">
          <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="快照 ID">{detail.eligibility.id}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{new Date(detail.eligibility.created_at).toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="快照摘要">{detail.eligibility.digest}</Descriptions.Item>
            <Descriptions.Item label="有效期至">{new Date(detail.eligibility.valid_until).toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="网络策略">默认禁止</Descriptions.Item>
            <Descriptions.Item label="输入策略">只读挂载</Descriptions.Item>
          </Descriptions>
          {identity === 'data_requester' && !detail.jobs.length && <Button type="primary" icon={<CodeSandboxOutlined />} loading={busy === 'job'} onClick={() => runCommand('job')} style={{ marginTop: 16 }}>创建待派发 ComputeJob</Button>}
        </Card>}
        {detail.jobs.map((job) => {
          const completed = job.run?.callbacks.find((item) => item.type === 'execution.completed')
          const summary = completed?.payload.execution_summary as Record<string, unknown> | undefined
          const metrics = completed?.payload.output_manifest ? completed.payload : undefined
          return <Card key={job.id} title="ComputeJob 执行详情" extra={<Tag color={job.status === 'succeeded' ? 'green' : job.run ? 'blue' : 'default'}>{job.status}</Tag>}>
          <div className="phase55-job-grid">
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="任务编号">{job.id}</Descriptions.Item>
              <Descriptions.Item label="当前状态">{job.status}</Descriptions.Item>
              <Descriptions.Item label="创建时间">{new Date(job.created_at).toLocaleString()}</Descriptions.Item>
              <Descriptions.Item label="资格快照">{job.eligibility_snapshot_id}</Descriptions.Item>
              <Descriptions.Item label="预留槽位">#{job.slot_ordinal} / {job.run_limit}</Descriptions.Item>
              <Descriptions.Item label="槽位摘要">{job.slot_digest}</Descriptions.Item>
              <Descriptions.Item label="ComputeRun">{job.run?.id || '未创建'}</Descriptions.Item>
              <Descriptions.Item label="Run 状态">{job.run?.status || '等待派发'}</Descriptions.Item>
            </Descriptions>
            <Card size="small" title="运行约束">
              <List>
                <List.Item><ClockCircleOutlined /> 合同网络策略：禁止外网</List.Item>
                <List.Item><SafetyCertificateOutlined /> 固定本地代码路径，无网络依赖</List.Item>
              </List>
              {identity === 'space_operator' && !job.run && <Button type="primary" icon={<PlayCircleOutlined />} loading={busy === 'dispatch'} onClick={() => runCommand('dispatch', job.id)}>发起受控执行</Button>}
            </Card>
          </div>
          {job.run && <Card size="small" title="真实执行时间线" style={{ marginTop: 16 }}>
            <Timeline items={[
              { color: 'green', children: `派发请求 / Run 已预留：${job.run.reserved_at ? new Date(job.run.reserved_at).toLocaleString() : '-'}` },
              { color: job.run.dispatched_at ? 'green' : 'gray', children: `Dispatcher / Coordinator：${job.run.dispatched_at ? '已认领并提交固定执行器' : '等待认领'}` },
              ...job.run.callbacks.map((item) => ({ color: item.status === 'completed' ? 'green' : 'blue', children: `${item.type} · ${new Date(item.occurred_at).toLocaleString()}` })),
            ]} />
          </Card>}
          {completed && <Card size="small" title="本次运行指标" style={{ marginTop: 16 }}>
            <Descriptions bordered column={{ xs: 1, md: 2 }}>
              <Descriptions.Item label="处理图像">{String(summary?.sample_count || 20)}</Descriptions.Item>
              <Descriptions.Item label="设备">{String((summary?.resource_usage as Record<string, unknown> | undefined)?.device || 'cpu')}</Descriptions.Item>
              <Descriptions.Item label="正确预测">{String(summary?.correct_predictions ?? '-')}</Descriptions.Item>
              <Descriptions.Item label="Accuracy">{String(summary?.accuracy ?? '-')}</Descriptions.Item>
              <Descriptions.Item label="Mean confidence">{String(summary?.mean_confidence ?? '-')}</Descriptions.Item>
              <Descriptions.Item label="模型 digest 验证">{summary?.model_digest_verified === true ? '通过' : '未通过'}</Descriptions.Item>
              <Descriptions.Item label="数据 digest 验证">{summary?.dataset_digest_unchanged === true ? '前后一致' : '未通过'}</Descriptions.Item>
              <Descriptions.Item label="输入完整性">逻辑只读 + 完整性校验</Descriptions.Item>
            </Descriptions>
          </Card>}
          {job.artifact && <Card size="small" title="Artifact 隔离" style={{ marginTop: 16 }}>
            <Alert type="warning" showIcon title={`Artifact 状态：${job.artifact.status}`} description="结果已生成并写入隔离区；出域审核未开始；安全结果包未生成；下载权限为无。" />
            <Descriptions bordered column={{ xs: 1, md: 2 }} style={{ marginTop: 12 }}>
              <Descriptions.Item label="Artifact ID">{job.artifact.id}</Descriptions.Item>
              <Descriptions.Item label="内容摘要">{job.artifact.content_digest}</Descriptions.Item>
              <Descriptions.Item label="文件总大小">{job.artifact.size_bytes} bytes</Descriptions.Item>
              <Descriptions.Item label="Release Package">{job.artifact.release_package_count}</Descriptions.Item>
              <Descriptions.Item label="下载授权">{job.artifact.download_grant_count}</Descriptions.Item>
              <Descriptions.Item label="下载按钮">不提供</Descriptions.Item>
            </Descriptions>
          </Card>}
        </Card>})}
        <Card title="操作证据">
          <Timeline items={(audit.data?.items || []).map((event) => ({
            color: event.result === 'success' ? 'green' : event.result === 'denied' ? 'red' : 'blue',
            content: <button className="phase51-event-button" onClick={() => setSelectedEvent(event)}>
              <strong>{event.event_type}</strong>
              <span>{new Date(event.occurred_at).toLocaleString()} · {event.subject_type}</span>
              <small>{event.event_id}</small>
            </button>,
          }))} />
        </Card>
      </>}
    </Spin>
    <Drawer title="技术证据" width={560} open={Boolean(selectedEvent)} onClose={() => setSelectedEvent(null)}>
      {selectedEvent && <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="Event ID">{selectedEvent.event_id}</Descriptions.Item>
        <Descriptions.Item label="事件">{selectedEvent.event_type}</Descriptions.Item>
        <Descriptions.Item label="主体">{selectedEvent.subject_type} / {selectedEvent.subject_id}</Descriptions.Item>
        <Descriptions.Item label="Actor ID">{selectedEvent.actor_user_id || '-'}</Descriptions.Item>
        <Descriptions.Item label="Organization ID">{selectedEvent.actor_organization_id || '-'}</Descriptions.Item>
        <Descriptions.Item label="结果">{selectedEvent.result}</Descriptions.Item>
        <Descriptions.Item label="Previous hash">{selectedEvent.previous_hash || '-'}</Descriptions.Item>
        <Descriptions.Item label="Current hash">{selectedEvent.current_hash}</Descriptions.Item>
        <Descriptions.Item label="Outbox">{selectedEvent.outbox_status || '-'}</Descriptions.Item>
        <Descriptions.Item label="证据摘要"><Text code>{valueText(selectedEvent.evidence)}</Text></Descriptions.Item>
      </Descriptions>}
    </Drawer>
  </div>
}
