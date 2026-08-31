import {
  AuditOutlined,
  CheckCircleOutlined,
  CloudDownloadOutlined,
  EyeOutlined,
  FileProtectOutlined,
  LockOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { secureUuid } from '../lib/secureUuid'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Empty,
  Flex,
  Form,
  Input,
  List,
  Modal,
  Progress,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { platformCommand, platformDownload, platformGet } from './api'
import { createSingleFlight, startAbortableLoad } from './requestLifecycle'
import { useRoadshow } from './RoadshowContext'

const { Paragraph, Text, Title } = Typography

const reviewLabels: Record<string, string> = {
  data_provider_egress_review: '医院数据出域审核',
  model_provider_quality_review: '模型方技术确认',
  platform_compliance_review: '平台合规审核',
}

type ResultListItem = {
  artifact_id: string
  artifact_status: string
  created_at: string
  job_id: string
  run_id: string
  contract_id: string
  contract_number: string
  application_number: string
  requester_organization: string
  review_progress: { approved: number; required: number; package_allowed: boolean }
  package: null | { package_id: string; status: string }
}

type ReviewItem = {
  task_id: string
  review_type: string
  status: string
  required: boolean
  mine: boolean
  decision: null | {
    decision: string
    reason_code: string
    comment: string
    decision_digest: string
  }
}

type ResultDetail = {
  artifact_id: string
  artifact_status: string
  artifact_type: string
  content_digest: string
  size_bytes: number
  classification: string
  created_at: string
  job_id: string
  job_status: string
  run_id: string
  run_status: string
  contract_id: string
  contract_number: string
  contract_revision_id: string
  contract_status: string
  requester_organization: string
  application_number: string
  metrics: {
    sample_count: number
    correct_predictions: number
    accuracy: string | number
    mean_confidence: string | number
  }
  manifest: Array<{ name: string; size_bytes: number; digest: string; media_type: string }>
  allowlist: string[]
  denylist: string[]
  hard_isolation: boolean
  raw_artifact_download_allowed: boolean
  review_progress: { approved: number; required: number; package_allowed: boolean }
  reviews: ReviewItem[]
  package: null | {
    package_id: string
    status: string
    package_digest: string
    size_bytes: number
    created_at: string
    files: Array<{ name: string; size_bytes: number; digest: string }>
  }
  download_grants: Array<{
    grant_id: string
    status: string
    download_count: number
    max_downloads: number
    expires_at: string
    last_downloaded_at: string | null
  }>
}

type AuditPayload = {
  audit_chain_valid: boolean
  items: Array<{
    event_id: string
    sequence: number
    event_type: string
    result: string
    occurred_at: string
    previous_hash: string | null
    current_hash: string
  }>
}

function statusTag(value: string) {
  const colors: Record<string, string> = {
    quarantined: 'purple',
    pending: 'gold',
    claimed: 'blue',
    decided: 'green',
    available: 'green',
    active: 'blue',
    exhausted: 'default',
    rejected: 'red',
  }
  const labels: Record<string, string> = {
    quarantined: '隔离中',
    pending: '待审核',
    claimed: '审核中',
    decided: '已决定',
    available: '可下载',
    active: '有效',
    exhausted: '已使用',
    rejected: '已拒绝',
  }
  return <Tag color={colors[value]}>{labels[value] || value}</Tag>
}

function PageHeading({ title, description, action }: { title: string; description: string; action?: React.ReactNode }) {
  return <div className="phase51-heading"><div><Title level={2}>{title}</Title><Paragraph>{description}</Paragraph></div>{action}</div>
}

export function ResultReleaseListPage() {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const [items, setItems] = useState<ResultListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    setLoading(true); setError('')
    return startAbortableLoad(
      (signal) => platformGet<{ items: ResultListItem[] }>('/result-artifacts', identity, signal),
      {
        onSuccess: (value) => setItems(value.items),
        onError: (reason) => setError(reason instanceof Error ? reason.message : '结果列表加载失败'),
        onSettled: () => setLoading(false),
      },
    )
  }, [identity, nonce])
  return <div className="page-stack phase57-page">
    <PageHeading title="结果审核与安全下载" description="原始 Artifact 始终留在隔离区；只有完成合同要求审核的独立结果包可以受控下载。" action={<Button icon={<ReloadOutlined />} onClick={() => setNonce((value) => value + 1)}>刷新</Button>} />
    {error && <Alert type="error" showIcon title="无法读取结果中心" description={error} />}
    {loading ? <Card><Flex justify="center"><Spin /></Flex></Card> : items.length ? <Table
      rowKey="artifact_id"
      scroll={{ x: 980 }}
      dataSource={items}
      columns={[
        { title: 'Artifact', dataIndex: 'artifact_id', width: 150, render: (value: string) => <Text code>{value.slice(0, 8)}</Text> },
        { title: '合同', dataIndex: 'contract_number', width: 150 },
        { title: '申请', dataIndex: 'application_number', width: 150 },
        { title: '需求企业', dataIndex: 'requester_organization', width: 190 },
        { title: '隔离状态', dataIndex: 'artifact_status', width: 110, render: statusTag },
        { title: '审核', width: 150, render: (_, item) => <Space><Progress type="circle" size={34} percent={item.review_progress.required ? Math.round(item.review_progress.approved / item.review_progress.required * 100) : 0} /><Text>{item.review_progress.approved}/{item.review_progress.required}</Text></Space> },
        { title: '结果包', width: 110, render: (_, item) => item.package ? statusTag(item.package.status) : <Tag>未生成</Tag> },
        { title: '操作', fixed: 'right', width: 92, render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => navigate(`/results/${item.artifact_id}`)}>查看</Button> },
      ]}
    /> : <Empty description="当前没有可见的 quarantined Artifact" />}
  </div>
}

function ReviewModal({ review, detail, open, onClose, onChanged }: {
  review: ReviewItem | null
  detail: ResultDetail
  open: boolean
  onClose: () => void
  onChanged: () => void
}) {
  const { identity } = useRoadshow()
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  const guard = useRef(createSingleFlight()).current
  const [api, holder] = message.useMessage()
  const submit = async (decision: 'approved' | 'rejected') => {
    if (!review) return
    await guard.run(async () => {
      const values = await form.validateFields()
      setBusy(true)
      try {
        await platformCommand(
          `/result-review-tasks/${review.task_id}/decide`,
          identity,
          `phase57-review-${secureUuid()}`,
          {
            ...values,
            decision,
            reason_code: decision === 'approved' ? 'scope_verified' : 'policy_conflict',
            approved_files: detail.manifest.map((item) => item.name),
          },
        )
        api.success(decision === 'approved' ? '审核已批准' : '审核已拒绝')
        onClose(); onChanged()
      } finally { setBusy(false) }
    })
  }
  return <Modal open={open} title={review ? reviewLabels[review.review_type] : '结果审核'} onCancel={onClose} footer={[
    <Button key="reject" danger icon={<StopOutlined />} loading={busy} onClick={() => void submit('rejected')}>拒绝</Button>,
    <Button key="approve" type="primary" icon={<CheckCircleOutlined />} loading={busy} onClick={() => void submit('approved')}>批准</Button>,
  ]}>
    {holder}
    <Alert type="warning" showIcon title={`Artifact 状态：${detail.artifact_status}`} description="审核决定不会解除原始 Artifact 隔离。" />
    <Form form={form} layout="vertical" initialValues={{
      purpose_and_scope_match: true,
      aggregate_only: true,
      no_patient_level_data: true,
      no_reidentification_risk: true,
      digest_verified: true,
      schema_verified: true,
      allowlist_verified: true,
      comment: '已核对合同范围、文件白名单、摘要和聚合结果边界。',
      additional_conditions: '',
    }}>
      <Form.Item name="comment" label="审核意见" rules={[{ required: true, min: 5 }]}><Input.TextArea rows={3} /></Form.Item>
      {[
        ['purpose_and_scope_match', '用途和范围与合同一致'],
        ['aggregate_only', '仅包含聚合结果'],
        ['no_patient_level_data', '未发现患者级信息'],
        ['no_reidentification_risk', '未发现可重识别风险'],
        ['digest_verified', '文件摘要验证通过'],
        ['schema_verified', 'JSON/CSV 结构验证通过'],
        ['allowlist_verified', '文件精确匹配输出白名单'],
      ].map(([name, label]) => <Form.Item key={name} name={name} valuePropName="checked"><Checkbox>{label}</Checkbox></Form.Item>)}
      <Form.Item name="additional_conditions" label="附加条件"><Input.TextArea rows={2} /></Form.Item>
    </Form>
  </Modal>
}

export function ResultReleaseDetailPage() {
  const { artifactId } = useParams()
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<ResultDetail | null>(null)
  const [audit, setAudit] = useState<AuditPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [nonce, setNonce] = useState(0)
  const [selectedReview, setSelectedReview] = useState<ReviewItem | null>(null)
  const [lastToken, setLastToken] = useState('')
  const guard = useRef(createSingleFlight()).current
  const [api, holder] = message.useMessage()
  const refresh = useCallback(() => setNonce((value) => value + 1), [])
  useEffect(() => {
    if (!artifactId) return
    setLoading(true); setError('')
    return startAbortableLoad(
      (signal) => Promise.all([
        platformGet<ResultDetail>(`/result-artifacts/${artifactId}`, identity, signal),
        platformGet<AuditPayload>(`/result-artifacts/${artifactId}/audit-events`, identity, signal),
      ]),
      {
        onSuccess: ([nextDetail, nextAudit]) => { setDetail(nextDetail); setAudit(nextAudit) },
        onError: (reason) => setError(reason instanceof Error ? reason.message : '结果详情加载失败'),
        onSettled: () => setLoading(false),
      },
    )
  }, [artifactId, identity, nonce])
  const mine = detail?.reviews.find((item) => item.mine && ['pending', 'claimed'].includes(item.status)) || null
  const runCommand = async (name: string, operation: () => Promise<unknown>) => {
    await guard.run(async () => {
      setBusy(name); setError('')
      try { await operation(); api.success('操作已完成'); refresh() }
      catch (reason) { setError(reason instanceof Error ? reason.message : '操作失败') }
      finally { setBusy('') }
    })
  }
  const createPlan = () => runCommand('plan', () => platformCommand(`/result-artifacts/${artifactId}/review-plan`, identity, `phase57-plan-${secureUuid()}`))
  const createPackage = () => runCommand('package', () => platformCommand(`/result-artifacts/${artifactId}/package`, identity, `phase57-package-${secureUuid()}`))
  const download = () => runCommand('download', async () => {
    if (!detail?.package) return
    const grant = await platformCommand<{ token: string }>(
      `/result-packages/${detail.package.package_id}/download-grants`,
      identity,
      `phase57-grant-${secureUuid()}`,
      { lifetime_seconds: 300 },
    )
    setLastToken(grant.token)
    const blob = await platformDownload(identity, grant.token, `phase57-download-${secureUuid()}`)
    const href = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = href; anchor.download = `medtrust-result-${detail.package.package_id}.zip`; anchor.click()
    URL.revokeObjectURL(href)
  })
  const verifyReplayRejected = () => runCommand('replay', async () => {
    try {
      await platformDownload(identity, lastToken, `phase57-replay-${secureUuid()}`)
      throw new Error('重复下载未被拒绝')
    } catch (reason) {
      if (reason instanceof Error && reason.message === '重复下载未被拒绝') throw reason
      api.info('二次使用已被后端拒绝并记录审计证据')
    }
  })
  const packageFiles = useMemo(() => detail?.package?.files || [], [detail])
  return <div className="page-stack phase57-page">{holder}
    <PageHeading title="Artifact 审核与结果发布" description="审核状态、隔离状态、结果包和下载授权分别记录；原始 Artifact 不提供下载。" action={<Space><Button onClick={() => navigate('/results')}>返回</Button><Button icon={<ReloadOutlined />} onClick={refresh}>刷新</Button></Space>} />
    {error && <Alert type="error" showIcon title="操作未完成" description={error} />}
    {loading ? <Card><Flex justify="center"><Spin /></Flex></Card> : detail ? <>
      <Alert type="warning" showIcon title={`Artifact 状态：${detail.artifact_status}`} description="原始 Artifact 下载：禁止。安全结果包是独立数据库对象和独立 MinIO 对象。" />
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}><Card title="执行与制品摘要">
          <Descriptions bordered column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="Artifact ID"><Text code>{detail.artifact_id}</Text></Descriptions.Item>
            <Descriptions.Item label="隔离状态">{statusTag(detail.artifact_status)}</Descriptions.Item>
            <Descriptions.Item label="Job / Run"><Text code>{detail.job_id.slice(0, 8)} / {detail.run_id.slice(0, 8)}</Text></Descriptions.Item>
            <Descriptions.Item label="合同">{detail.contract_number}</Descriptions.Item>
            <Descriptions.Item label="处理样本">{detail.metrics.sample_count}</Descriptions.Item>
            <Descriptions.Item label="正确预测">{detail.metrics.correct_predictions}</Descriptions.Item>
            <Descriptions.Item label="Accuracy">{String(detail.metrics.accuracy)}</Descriptions.Item>
            <Descriptions.Item label="Mean confidence">{String(detail.metrics.mean_confidence)}</Descriptions.Item>
            <Descriptions.Item label="内容摘要" span={2}><Text code copyable>{detail.content_digest}</Text></Descriptions.Item>
            <Descriptions.Item label="原始下载">禁止</Descriptions.Item>
          </Descriptions>
        </Card></Col>
        <Col xs={24} xl={8}><Card title="当前责任与下一步">
          <Progress percent={detail.review_progress.required ? Math.round(detail.review_progress.approved / detail.review_progress.required * 100) : 0} />
          <Paragraph>{detail.review_progress.approved}/{detail.review_progress.required} 项 required review 已批准</Paragraph>
          <Space direction="vertical" className="phase57-full">
            {identity === 'space_operator' && !detail.reviews.length && <Button block type="primary" icon={<SendOutlined />} loading={busy === 'plan'} onClick={() => void createPlan()}>创建审核计划</Button>}
            {mine && <Button block type="primary" icon={<SafetyCertificateOutlined />} onClick={() => setSelectedReview(mine)}>处理我的审核</Button>}
            {identity === 'space_operator' && detail.review_progress.package_allowed && !detail.package && <Button block type="primary" icon={<FileProtectOutlined />} loading={busy === 'package'} onClick={() => void createPackage()}>生成安全结果包</Button>}
            {identity === 'data_requester' && detail.package && !detail.download_grants.some((item) => item.status === 'exhausted') && <Button block type="primary" icon={<CloudDownloadOutlined />} loading={busy === 'download'} onClick={() => void download()}>创建授权并下载</Button>}
            {identity === 'data_requester' && lastToken && <Button block icon={<LockOutlined />} loading={busy === 'replay'} onClick={() => void verifyReplayRejected()}>验证二次使用被拒绝</Button>}
          </Space>
        </Card></Col>
      </Row>
      <Card title="三方审核">
        <List dataSource={detail.reviews} locale={{ emptyText: '尚未创建审核计划' }} renderItem={(item) => <List.Item actions={item.mine && ['pending', 'claimed'].includes(item.status) ? [<Button key="review" type="link" onClick={() => setSelectedReview(item)}>审核</Button>] : []} extra={statusTag(item.status)}>
          <List.Item.Meta title={<Space><strong>{reviewLabels[item.review_type] || item.review_type}</strong>{item.required ? <Tag color="red">required</Tag> : <Tag>conditional</Tag>}</Space>} description={item.decision ? `${item.decision.comment} · ${item.decision.reason_code}` : '等待责任组织提交结构化结论'} />
        </List.Item>} />
      </Card>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}><Card title="Artifact manifest">
          <Table rowKey="name" pagination={false} scroll={{ x: 680 }} dataSource={detail.manifest} columns={[
            { title: '文件', dataIndex: 'name', width: 190 },
            { title: '大小', dataIndex: 'size_bytes', width: 100, render: (value) => `${value} B` },
            { title: 'Digest', dataIndex: 'digest', render: (value) => <Text code>{String(value).slice(0, 22)}...</Text> },
          ]} />
        </Card></Col>
        <Col xs={24} xl={12}><Card title="独立安全结果包">
          {detail.package ? <>
            <Alert type="success" showIcon title="结果包可用" description={`Package digest：${detail.package.package_digest.slice(0, 24)}...`} />
            <List dataSource={packageFiles} renderItem={(item) => <List.Item><FileProtectOutlined /> {item.name} <Text type="secondary">{item.size_bytes} B</Text></List.Item>} />
          </> : <Empty description="required review 全部批准后生成" />}
          {detail.download_grants.map((grant) => <Descriptions key={grant.grant_id} size="small" column={2} bordered>
            <Descriptions.Item label="Grant">{grant.grant_id.slice(0, 8)}</Descriptions.Item>
            <Descriptions.Item label="状态">{statusTag(grant.status)}</Descriptions.Item>
            <Descriptions.Item label="使用次数">{grant.download_count}/{grant.max_downloads}</Descriptions.Item>
            <Descriptions.Item label="有效期">{new Date(grant.expires_at).toLocaleString()}</Descriptions.Item>
          </Descriptions>)}
        </Card></Col>
      </Row>
      <Card title={<Space><AuditOutlined /> 审计证据</Space>} extra={audit?.audit_chain_valid ? <Tag color="green">审计链有效</Tag> : <Tag color="red">审计链异常</Tag>}>
        <Timeline items={(audit?.items || []).map((item) => ({
          color: item.result === 'denied' ? 'red' : 'green',
          children: <div className="phase57-audit-item"><strong>{item.event_type}</strong><span>{new Date(item.occurred_at).toLocaleString()} · Event ID {item.event_id}</span><Text code>Previous hash: {item.previous_hash || 'GENESIS'}</Text><Text code>Current hash: {item.current_hash}</Text></div>,
        }))} />
      </Card>
      <ReviewModal review={selectedReview} detail={detail} open={Boolean(selectedReview)} onClose={() => setSelectedReview(null)} onChanged={refresh} />
    </> : <Empty description="Artifact 不存在或当前组织无权访问" />}
  </div>
}
