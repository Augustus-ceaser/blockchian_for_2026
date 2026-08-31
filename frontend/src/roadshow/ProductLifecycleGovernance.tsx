import {
  DeleteOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
  RollbackOutlined,
} from '@ant-design/icons'
import { secureUuid } from '../lib/secureUuid'
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { useEffect, useState } from 'react'
import { platformCommand, platformGet } from './api'
import { startAbortableLoad } from './requestLifecycle'
import { useRoadshow } from './RoadshowContext'

const { Paragraph, Title } = Typography

export type LifecycleRequest = {
  id: string
  target_type: 'data_product' | 'model_product'
  target_product_id: string
  target_version_id: string | null
  action: 'unpublish' | 'relist' | 'archive'
  reason: string
  details: Record<string, unknown>
  status: string
  impact: {
    applications?: Record<string, number>
    contracts?: Record<string, number>
    compute_jobs?: Record<string, number>
    running_compute_runs?: number
    quarantined_artifacts?: number
    available_release_packages?: number
    active_download_grants?: number
    audit_chain_valid?: boolean
    blockers?: string[]
  }
  impact_digest: string
  requested_at: string
  reviewed_at: string | null
  review_comment: string | null
  decision: string | null
}

const actionLabels = {
  unpublish: '下架',
  relist: '重新上架',
  archive: '逻辑删除',
}

function time(value: string | null | undefined) {
  if (!value) return '未发生'
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
  return `${new Date(value).toLocaleString()} (${zone})`
}

export function LifecycleActions({
  targetType,
  productId,
  allowedActions,
  current,
  onChanged,
}: {
  targetType: 'data_product' | 'model_product'
  productId: string
  allowedActions: string[]
  current: LifecycleRequest | null
  onChanged: () => void
}) {
  const { identity } = useRoadshow()
  const [action, setAction] = useState<LifecycleRequest['action'] | null>(null)
  const [busy, setBusy] = useState(false)
  const [form] = Form.useForm()
  const [api, holder] = message.useMessage()
  const path = targetType === 'data_product' ? `/data-products/${productId}` : `/model-products/${productId}`
  const submit = async () => {
    const values = await form.validateFields()
    if (!action) return
    setBusy(true)
    try {
      await platformCommand(
        `${path}/lifecycle-requests`,
        identity,
        `phase59-${action}-${secureUuid()}`,
        { action, ...values },
      )
      api.success(`${actionLabels[action]}申请已提交平台审核`)
      setAction(null)
      form.resetFields()
      onChanged()
    } finally {
      setBusy(false)
    }
  }
  const cancel = async () => {
    if (!current) return
    setBusy(true)
    try {
      await platformCommand(
        `/product-lifecycle-requests/${current.id}/cancel`,
        identity,
        `phase59-cancel-${secureUuid()}`,
      )
      api.success('生命周期申请已撤回')
      onChanged()
    } finally {
      setBusy(false)
    }
  }
  return <>
    {holder}
    <Space wrap>
      {allowedActions.includes('request_unpublish') && <Button icon={<PauseCircleOutlined />} onClick={() => setAction('unpublish')}>申请下架</Button>}
      {allowedActions.includes('request_relist') && <Button type="primary" icon={<RollbackOutlined />} onClick={() => setAction('relist')}>申请重新上架</Button>}
      {allowedActions.includes('request_archive') && <Button danger icon={<DeleteOutlined />} onClick={() => setAction('archive')}>申请删除</Button>}
      {current && <Button loading={busy} onClick={cancel}>撤回当前申请</Button>}
    </Space>
    {current && <Alert
      type="warning"
      showIcon
      title={`${actionLabels[current.action]}审核中`}
      description={`提交时间：${time(current.requested_at)}。产品状态在平台批准前不会改变。`}
    />}
    <Modal
      title={action ? `提交${actionLabels[action]}申请` : ''}
      open={Boolean(action)}
      confirmLoading={busy}
      onOk={submit}
      onCancel={() => setAction(null)}
      okText="提交平台审核"
    >
      <Form form={form} layout="vertical">
        <Form.Item name="reason" label="申请原因" rules={[{ required: true, min: 5 }]}>
          <Input.TextArea rows={4} />
        </Form.Item>
        <Form.Item name="existing_cooperation_note" label="对现有合作的说明">
          <Input.TextArea rows={3} />
        </Form.Item>
        <Form.Item name="contact_note" label="联系人或备注">
          <Input />
        </Form.Item>
      </Form>
    </Modal>
  </>
}

export function LifecycleTimeline({
  createdAt,
  submittedAt,
  approvedAt,
  publishedAt,
  unpublishedAt,
  deletedAt,
  updatedAt,
}: {
  createdAt: string
  submittedAt: string | null
  approvedAt: string | null
  publishedAt: string | null
  unpublishedAt: string | null
  deletedAt: string | null
  updatedAt: string
}) {
  return <Descriptions title="生命周期时间" bordered column={{ xs: 1, md: 2 }}>
    <Descriptions.Item label="创建">{time(createdAt)}</Descriptions.Item>
    <Descriptions.Item label="最近更新">{time(updatedAt)}</Descriptions.Item>
    <Descriptions.Item label="提交上架审核">{time(submittedAt)}</Descriptions.Item>
    <Descriptions.Item label="平台审核通过">{time(approvedAt)}</Descriptions.Item>
    <Descriptions.Item label="正式上架">{time(publishedAt)}</Descriptions.Item>
    <Descriptions.Item label="正式下架">{time(unpublishedAt)}</Descriptions.Item>
    <Descriptions.Item label="逻辑删除生效">{time(deletedAt)}</Descriptions.Item>
  </Descriptions>
}

export function ProductLifecycleReviewPage() {
  const { identity } = useRoadshow()
  const [items, setItems] = useState<LifecycleRequest[]>([])
  const [selected, setSelected] = useState<LifecycleRequest | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [api, holder] = message.useMessage()
  useEffect(() => startAbortableLoad(
    (signal) => platformGet<{ items: LifecycleRequest[] }>('/product-lifecycle-requests', identity, signal),
    {
      onSuccess: (value) => { setItems(value.items); setError('') },
      onError: (reason) => setError(reason instanceof Error ? reason.message : '读取失败'),
      onSettled: () => setLoading(false),
    },
  ), [identity, nonce])
  const decide = async (decision: 'approved' | 'rejected' | 'returned') => {
    if (!selected || comment.trim().length < 3) return
    setBusy(true)
    try {
      await platformCommand(
        `/product-lifecycle-requests/${selected.id}/decision`,
        identity,
        `phase59-${decision}-${secureUuid()}`,
        { decision, comment },
      )
      api.success('审核决定已生效')
      setSelected(null); setComment(''); setNonce((value) => value + 1)
    } finally {
      setBusy(false)
    }
  }
  return <div className="page-stack phase59-lifecycle-page">
    {holder}
    <div className="phase51-heading">
      <div><Title level={2}>产品生命周期审核</Title><Paragraph>下架、重新上架和逻辑删除均由平台基于实时影响分析决定。</Paragraph></div>
      <Button icon={<ReloadOutlined />} onClick={() => setNonce((value) => value + 1)}>刷新</Button>
    </div>
    {error && <Alert type="error" showIcon title="读取失败" description={error} />}
    <Table
      loading={loading}
      rowKey="id"
      dataSource={items}
      columns={[
        { title: '请求编号', dataIndex: 'id', render: (value: string) => value.slice(0, 8) },
        { title: '产品类型', dataIndex: 'target_type', render: (value) => value === 'data_product' ? '数据产品' : '模型产品' },
        { title: '操作', dataIndex: 'action', render: (value) => actionLabels[value as keyof typeof actionLabels] },
        { title: '提交时间', dataIndex: 'requested_at', render: time },
        { title: 'BLOCKER', render: (_, row) => <Tag color={(row.impact.blockers?.length || 0) ? 'red' : 'green'}>{row.impact.blockers?.length || 0}</Tag> },
        { title: '状态', dataIndex: 'status', render: (value) => <Tag>{value}</Tag> },
        { title: '操作', render: (_, row) => <Button type="link" onClick={() => setSelected(row)}>查看审核</Button> },
      ]}
    />
    <Drawer title="生命周期审核详情" width={620} open={Boolean(selected)} onClose={() => setSelected(null)}>
      {selected && <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Descriptions bordered column={1} size="small">
          <Descriptions.Item label="申请">{actionLabels[selected.action]}</Descriptions.Item>
          <Descriptions.Item label="原因">{selected.reason}</Descriptions.Item>
          <Descriptions.Item label="提交时间">{time(selected.requested_at)}</Descriptions.Item>
          <Descriptions.Item label="影响摘要">{selected.impact_digest}</Descriptions.Item>
          <Descriptions.Item label="审计链">{selected.impact.audit_chain_valid ? '有效' : '无效'}</Descriptions.Item>
          <Descriptions.Item label="运行中任务">{selected.impact.running_compute_runs || 0}</Descriptions.Item>
          <Descriptions.Item label="隔离结果">{selected.impact.quarantined_artifacts || 0}</Descriptions.Item>
          <Descriptions.Item label="BLOCKER">{selected.impact.blockers?.join('、') || '无'}</Descriptions.Item>
        </Descriptions>
        {selected.status === 'pending' && <>
          <Input.TextArea rows={4} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="填写平台审核意见" />
          <Space>
            <Button danger icon={<CloseCircleOutlined />} loading={busy} onClick={() => decide('rejected')}>拒绝</Button>
            <Button loading={busy} onClick={() => decide('returned')}>退回补充</Button>
            <Button type="primary" icon={<CheckCircleOutlined />} loading={busy} disabled={Boolean(selected.impact.blockers?.length)} onClick={() => decide('approved')}>批准并生效</Button>
          </Space>
        </>}
      </Space>}
    </Drawer>
  </div>
}
