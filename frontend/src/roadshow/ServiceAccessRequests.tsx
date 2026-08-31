import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ReloadOutlined,
  SendOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { secureUuid } from '../lib/secureUuid'
import { CommercialOfferPreview } from './CommercialCheckoutPage'
import { createOrderFromServiceAccess } from './commerce'
import { platformCommand, platformGet } from './api'
import { createSingleFlight, startAbortableLoad } from './requestLifecycle'
import { roleProfiles, useRoadshow } from './RoadshowContext'
import {
  offeringLabel,
  serviceModeLabels,
  type ProductKind,
  type ServiceMode,
  type ServiceOffering,
} from './serviceAccess'

const { Paragraph, Text } = Typography

export type ServiceAccessRequest = {
  request_id: string
  request_number: string
  product_kind: ProductKind
  product_id: string
  version_id: string
  service_mode: Exclude<ServiceMode, 'controlled_compute'>
  status: string
  purpose: string
  intended_use: string
  requested_duration_days: number
  requester?: { id?: string; name?: string }
  provider?: { id?: string; name?: string }
  product?: { name?: string; version?: string; provider?: string }
  product_snapshot?: { name?: string; version?: string; provider?: string }
  requested_at?: string
  created_at?: string
  provider_decision?: { decision: string; summary: string; decided_at?: string; decided_by?: string } | null
  operator_decision?: { decision: string; summary: string; decided_at?: string; decided_by?: string } | null
  next_step?: string
  allowed_actions?: string[]
}

type AuthorizationModalProps = {
  open: boolean
  productKind: ProductKind
  productName: string
  versionId: string
  offering: ServiceOffering | null
  onCancel: () => void
  onCreated?: (request: ServiceAccessRequest) => void
}

type AuthorizationForm = {
  purpose: string
  intended_use: string
  requested_duration_days: number
}

export function ServiceAuthorizationModal({
  open,
  productKind,
  productName,
  versionId,
  offering,
  onCancel,
  onCreated,
}: AuthorizationModalProps) {
  const { identity } = useRoadshow()
  const [form] = Form.useForm<AuthorizationForm>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [api, holder] = message.useMessage()
  const guard = useRef(createSingleFlight()).current

  useEffect(() => {
    if (!open) return
    setError('')
    form.setFieldsValue({
      purpose: '科研分析与技术验证',
      intended_use: productKind === 'data'
        ? '申请经审核的匿名化数据授权交付，使用范围以合同为准。'
        : '申请经审核的模型使用许可，使用范围以合同为准。',
      requested_duration_days: 30,
    })
  }, [form, open, productKind])

  const submit = async () => {
    if (!offering || identity !== 'data_requester') return
    await guard.run(async () => {
      setBusy(true)
      setError('')
      try {
        const values = await form.validateFields()
        const result = await platformCommand<ServiceAccessRequest>(
          '/service-access-requests',
          identity,
          `service-access-create-${secureUuid()}`,
          {
            product_kind: productKind,
            version_id: versionId,
            service_mode: offering.mode,
            ...values,
          },
        )
        api.success('授权申请已提交，后续由提供方和平台审核')
        onCreated?.(result)
        onCancel()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '授权申请提交失败')
      } finally {
        setBusy(false)
      }
    })
  }

  return <>
    {holder}
    <Modal
      open={open}
      title={`申请${offering ? offeringLabel(offering) : '授权'}`}
      okText="提交授权申请"
      cancelText="取消"
      confirmLoading={busy}
      onOk={submit}
      onCancel={onCancel}
      destroyOnHidden
    >
      <Paragraph><Text strong>{productName}</Text></Paragraph>
      <Paragraph type="secondary">提交后由提供方和平台审核；申请阶段不付款，获批后再确认协议并结算。</Paragraph>
      {offering && <CommercialOfferPreview productKind={productKind} versionId={versionId} serviceMode={offering.mode} />}
      {error && <Alert className="phase51-form-alert" type="error" showIcon title="申请未提交" description={error} />}
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="purpose" label="申请用途" rules={[{ required: true, min: 4 }]}>
          <Input />
        </Form.Item>
        <Form.Item name="intended_use" label="拟用方式与范围" rules={[{ required: true, min: 10 }]}>
          <Input.TextArea rows={3} />
        </Form.Item>
        <Form.Item name="requested_duration_days" label="申请授权期（天）" rules={[{ required: true }]}>
          <InputNumber min={1} max={365} className="phase51-full" />
        </Form.Item>
      </Form>
    </Modal>
  </>
}

const requestStatusLabels: Record<string, { label: string; color: string }> = {
  submitted: { label: '待提供方审核', color: 'gold' },
  provider_approved: { label: '待平台审核', color: 'blue' },
  approved_pending_contract: { label: '已批准·可结算', color: 'green' },
  rejected: { label: '已拒绝', color: 'red' },
  withdrawn: { label: '已撤回', color: 'default' },
}

function requestProductName(item: ServiceAccessRequest) {
  return item.product?.name || item.product_snapshot?.name || '未命名产品'
}

function decisionActionAllowed(item: ServiceAccessRequest, identity: string, stage: 'provider' | 'operator') {
  if (item.allowed_actions?.length) {
    return item.allowed_actions.includes(stage === 'provider' ? 'provider_decide' : 'operator_decide')
  }
  if (stage === 'operator') return identity === 'space_operator' && item.status === 'provider_approved'
  return ((identity === 'data_provider' && item.product_kind === 'data')
    || (identity === 'model_provider' && item.product_kind === 'model'))
    && item.status === 'submitted'
}

export function ServiceAccessRequestsPanel() {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const [items, setItems] = useState<ServiceAccessRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [decisionBusy, setDecisionBusy] = useState('')
  const [api, holder] = message.useMessage()
  const guard = useRef(createSingleFlight()).current

  const load = () => {
    setLoading(true)
    setError('')
    return startAbortableLoad(
      (signal) => platformGet<{ items: ServiceAccessRequest[]; total: number }>('/service-access-requests', identity, signal),
      {
        onSuccess: (result) => setItems(result.items || []),
        onError: (reason) => setError(reason instanceof Error ? reason.message : '授权申请加载失败'),
        onSettled: () => setLoading(false),
      },
    )
  }

  useEffect(load, [identity])

  const decide = async (item: ServiceAccessRequest, stage: 'provider' | 'operator', decision: 'approve' | 'reject') => {
    await guard.run(async () => {
      setDecisionBusy(`${item.request_id}:${decision}`)
      try {
        await platformCommand(
          `/service-access-requests/${item.request_id}/${stage}-decision`,
          identity,
          `service-access-${stage}-${secureUuid()}`,
          {
            decision,
            summary: decision === 'approve'
              ? '同意进入下一阶段，具体范围以合同与履约审核为准。'
              : '当前条件不满足授权要求。',
          },
        )
        api.success(decision === 'approve' ? '已批准当前审核阶段' : '已拒绝授权申请')
        load()
      } catch (reason) {
        api.error(reason instanceof Error ? reason.message : '审核操作失败')
      } finally {
        setDecisionBusy('')
      }
    })
  }

  const checkout = async (item: ServiceAccessRequest) => {
    await guard.run(async () => {
      setDecisionBusy(`${item.request_id}:checkout`)
      try {
        const order = await createOrderFromServiceAccess(
          item.request_id,
          identity,
          `commerce-service-order-${secureUuid()}`,
        )
        navigate(`/commercial-checkout/${order.order_id}`)
      } catch (reason) {
        api.error(reason instanceof Error ? reason.message : '结算订单创建失败')
      } finally {
        setDecisionBusy('')
      }
    })
  }

  const visibleTitle = useMemo(() => identity === 'data_requester'
    ? '我的授权申请'
    : `${roleProfiles[identity].shortLabel}授权审核`, [identity])

  return <Card
    title={visibleTitle}
    extra={<Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>}
  >
    {holder}
    {error && <Alert type="error" showIcon title="授权申请未加载" description={error} />}
    {!error && !items.length && !loading
      ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={identity === 'data_requester' ? '尚未提交数据或模型授权申请' : '当前没有授权审核待办'} />
      : <Table
        size="small"
        loading={loading}
        rowKey="request_id"
        dataSource={items}
        pagination={{ pageSize: 6 }}
        expandable={{
          expandedRowRender: (item) => <Descriptions size="small" column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="申请用途">{item.purpose}</Descriptions.Item>
            <Descriptions.Item label="授权期">{item.requested_duration_days} 天</Descriptions.Item>
            <Descriptions.Item label="拟用范围" span={2}>{item.intended_use}</Descriptions.Item>
          </Descriptions>,
        }}
        columns={[
          { title: '申请编号', dataIndex: 'request_number', render: (value) => <Text code>{value}</Text> },
          { title: '产品', render: (_, item) => <div><strong>{requestProductName(item)}</strong><div><Tag>{item.product_kind === 'data' ? '数据' : '模型'}</Tag></div></div> },
          { title: '授权方式', dataIndex: 'service_mode', render: (value: ServiceMode) => serviceModeLabels[value] || value },
          { title: '状态', dataIndex: 'status', render: (value) => {
            const status = requestStatusLabels[value] || { label: value, color: 'default' }
            return <Tag color={status.color}>{status.label}</Tag>
          } },
          {
            title: '操作', width: 180, render: (_, item) => {
              if (identity === 'data_requester' && item.status === 'approved_pending_contract') {
                return <Button
                  size="small"
                  type="primary"
                  loading={decisionBusy === `${item.request_id}:checkout`}
                  onClick={() => checkout(item)}
                >去结算</Button>
              }
              const stage = identity === 'space_operator' ? 'operator' : 'provider'
              if (!decisionActionAllowed(item, identity, stage)) return <Text type="secondary">等待下一流程</Text>
              return <Space size={4}>
                <Popconfirm title="确认批准当前审核阶段？" onConfirm={() => decide(item, stage, 'approve')}>
                  <Button size="small" type="primary" icon={<CheckCircleOutlined />} loading={decisionBusy === `${item.request_id}:approve`}>批准</Button>
                </Popconfirm>
                <Popconfirm title="确认拒绝此授权申请？" onConfirm={() => decide(item, stage, 'reject')}>
                  <Button size="small" danger icon={<CloseCircleOutlined />} loading={decisionBusy === `${item.request_id}:reject`}>拒绝</Button>
                </Popconfirm>
              </Space>
            },
          },
        ]}
      />}
    <div className="phase51-card-actions">
      <Text type="secondary"><SendOutlined /> 获批后确认协议、结算，再进入受控履约。</Text>
    </div>
  </Card>
}
