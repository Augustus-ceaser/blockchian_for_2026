import {
  Alert,
  Button,
  Descriptions,
  Form,
  Input,
  Modal,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  FileProtectOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useCallback, useEffect, useState } from 'react'
import { platformCommand, platformGet } from './api'
import { useRoadshow } from './RoadshowContext'

type Option = { id: string; label: string; connector_id?: string }
type Sources = {
  connectors: Option[]
  executors: Option[]
  applications: Option[]
  contracts: Option[]
  asset_versions: Option[]
  model_versions: Option[]
}
type Readiness = {
  id: string
  readiness_mode: string
  requested_action: string
  task_type: string
  status: string
  readiness_digest: string
  source_executor_status_event_digest?: string
  expires_at?: string
  execution_authorized: boolean
  hard_isolation: false
  checks: Array<{ code: string; passed: boolean }>
}
type Policy = {
  id: string
  policy_key: string
  connector_id: string
  application_id: string
  contract_id: string
  status: string
  expires_at: string
  version?: {
    id: string
    payload_digest: string
    signing_key_id: string
    signature: string
    canonical_payload: Record<string, unknown>
    execution_authorized: boolean
    requested_action: string
    execution_scope?: string
    task_type?: string
    max_execution_count: number
  }
}
type Order = {
  id: string
  order_key: string
  order_mode: string
  requested_action: string
  policy_bundle_id: string
  connector_sequence: number
  payload_digest: string
  status: string
  display_status: string
  execution_authorized: boolean
  execution_scope?: string
  task_type?: string
  max_execution_count: number
  consumed_count: number
  execution_started: boolean
  receipt?: Record<string, unknown> | null
  decision?: Record<string, unknown> | null
  consumption?: {
    id: string
    payload_digest: string
    authorization_snapshot_id: string
    task_manifest_id: string
    runtime_session_id: string
    reference_execution_id: string
  } | null
}

const statusColor: Record<string, string> = {
  compiled: 'processing',
  passed: 'green',
  active: 'green',
  accepted: 'green',
  rejected: 'red',
  validation_failed: 'red',
  revoked: 'orange',
  awaiting_local_review: 'blue',
  delivered: 'cyan',
}

export function PolicyControlPage() {
  const { identity } = useRoadshow()
  const operator = identity === 'space_operator'
  const [sources, setSources] = useState<Sources | null>(null)
  const [readiness, setReadiness] = useState<Readiness[]>([])
  const [policies, setPolicies] = useState<Policy[]>([])
  const [orders, setOrders] = useState<Order[]>([])
  const [selectedPolicy, setSelectedPolicy] = useState<Policy | null>(null)
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null)
  const [compileOpen, setCompileOpen] = useState(false)
  const [executionMode, setExecutionMode] = useState(
    'FIXED_REFERENCE_EXECUTION',
  )
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [policyResult, orderResult, readinessResult] = await Promise.all([
        platformGet<{ items: Policy[] }>('/policy-control/policies', identity),
        platformGet<{ items: Order[] }>('/policy-control/orders', identity),
        platformGet<{ items: Readiness[] }>(
          '/policy-control/readiness',
          identity,
        ),
      ])
      setPolicies(policyResult.items)
      setOrders(orderResult.items)
      setReadiness(readinessResult.items)
      if (operator) {
        setSources(
          await platformGet<Sources>('/policy-control/sources', identity),
        )
      }
    } finally {
      setLoading(false)
    }
  }, [identity, operator])

  useEffect(() => {
    void load()
  }, [load])

  const compile = async () => {
    const values = await form.validateFields()
    const result = await platformCommand<Policy>(
      '/policy-control/policies/compile',
      identity,
      crypto.randomUUID(),
      { ...values, execution_mode: executionMode },
    )
    setCompileOpen(false)
    form.resetFields()
    setSelectedPolicy(result)
    message.success('Policy compiled. No formal task has started.')
    await load()
  }

  const signActivate = async (policy: Policy) => {
    await platformCommand(
      `/policy-control/policies/${policy.id}/sign-activate`,
      identity,
      crypto.randomUUID(),
      {},
    )
    message.success('Policy signature verified and activated.')
    await load()
  }

  const createOrder = async (policy: Policy) => {
    const result = await platformCommand<Order>(
      '/policy-control/orders',
      identity,
      crypto.randomUUID(),
      {
        policy_bundle_id: policy.id,
        idempotency_key: crypto.randomUUID(),
      },
    )
    setSelectedOrder(result)
    message.success('Signed order created. It remains unconsumed.')
    await load()
  }

  const revoke = async (policy: Policy) => {
    await platformCommand(
      `/policy-control/policies/${policy.id}/revoke`,
      identity,
      crypto.randomUUID(),
      {
        reason_code: 'OPERATOR_CONTROL_REVOCATION',
        reason_text: 'Operator revocation before formal task creation.',
      },
    )
    message.success('Policy revocation recorded.')
    await load()
  }

  return (
    <div className="page-stack policy-control-page">
      <div className="external-governance-heading">
        <div>
          <Typography.Title level={3}>
            Signed Policy Control
          </Typography.Title>
          <Typography.Text type="secondary">
            Verified readiness, fixed policy, signed order, and local decision.
          </Typography.Text>
        </div>
        <Space wrap>
          {operator && (
            <Button
              icon={<FileProtectOutlined />}
              onClick={() => setCompileOpen(true)}
            >
              Compile policy
            </Button>
          )}
          <Button
            icon={<ReloadOutlined />}
            loading={loading}
            onClick={() => void load()}
          >
            Refresh
          </Button>
        </Space>
      </div>

      <Typography.Title level={4}>Execution Readiness</Typography.Title>
      <Table
        rowKey="id"
        dataSource={readiness}
        pagination={false}
        scroll={{ x: 960 }}
        columns={[
          {
            title: 'Mode',
            dataIndex: 'readiness_mode',
          },
          {
            title: 'Status',
            dataIndex: 'status',
            render: (value: string) => (
              <Tag color={statusColor[value]}>{value}</Tag>
            ),
          },
          {
            title: 'Status v2 source',
            dataIndex: 'source_executor_status_event_digest',
            ellipsis: true,
          },
          {
            title: 'Boundary',
            render: (_: unknown, row: Readiness) => (
              <Space wrap>
                <Tag>{row.task_type}</Tag>
                <Tag color="blue">Connector-attested</Tag>
              </Space>
            ),
          },
          { title: 'Valid until', dataIndex: 'expires_at' },
        ]}
      />

      <Typography.Title level={4}>PolicyBundle</Typography.Title>
      <Table
        rowKey="id"
        dataSource={policies}
        pagination={false}
        scroll={{ x: 960 }}
        columns={[
          {
            title: 'Policy',
            dataIndex: 'policy_key',
            render: (value: string, row: Policy) => (
              <Button type="link" onClick={() => setSelectedPolicy(row)}>
                {value}
              </Button>
            ),
          },
          {
            title: 'Status',
            dataIndex: 'status',
            render: (value: string) => (
              <Tag color={statusColor[value]}>{value}</Tag>
            ),
          },
          {
            title: 'Signed digest',
            render: (_: unknown, row: Policy) =>
              row.version?.payload_digest || '-',
          },
          {
            title: 'Boundary',
            render: (_: unknown, row: Policy) => (
              <Space wrap>
                <Tag>{row.version?.requested_action}</Tag>
                <Tag>
                  execution={String(
                    row.version?.execution_authorized ?? false,
                  )}
                </Tag>
              </Space>
            ),
          },
          {
            title: 'Actions',
            render: (_: unknown, row: Policy) =>
              operator && (
                <Space wrap>
                  {row.status === 'compiled' && (
                    <Button
                      icon={<SafetyCertificateOutlined />}
                      onClick={() => void signActivate(row)}
                    >
                      Sign and activate
                    </Button>
                  )}
                  {row.status === 'active' && (
                    <Button onClick={() => void createOrder(row)}>
                      Create signed order
                    </Button>
                  )}
                  {row.status === 'active' && (
                    <Button
                      danger
                      icon={<StopOutlined />}
                      onClick={() => void revoke(row)}
                    >
                      Revoke
                    </Button>
                  )}
                </Space>
              ),
          },
        ]}
      />

      <Typography.Title level={4}>ExecutionOrder</Typography.Title>
      <Table
        rowKey="id"
        dataSource={orders}
        pagination={false}
        scroll={{ x: 960 }}
        columns={[
          {
            title: 'Order',
            dataIndex: 'order_key',
            render: (value: string, row: Order) => (
              <Button
                type="link"
                onClick={async () =>
                  setSelectedOrder(
                    await platformGet<Order>(
                      `/policy-control/orders/${row.id}`,
                      identity,
                    ),
                  )
                }
              >
                {value}
              </Button>
            ),
          },
          {
            title: 'Status',
            render: (_: unknown, row: Order) => (
              <Tag color={statusColor[row.status]}>
                {row.display_status}
              </Tag>
            ),
          },
          { title: 'Sequence', dataIndex: 'connector_sequence' },
          { title: 'Mode', dataIndex: 'order_mode' },
          {
            title: 'Consumption',
            render: (_: unknown, row: Order) => (
              <Tag>
                {row.consumed_count}/{row.max_execution_count}
              </Tag>
            ),
          },
        ]}
      />

      <Modal
        title="Compile PolicyBundle"
        open={compileOpen}
        onCancel={() => setCompileOpen(false)}
        onOk={() => void compile()}
        okText="Compile"
        width={720}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item label="Policy mode">
            <Segmented
              block
              value={executionMode}
              onChange={(value) => setExecutionMode(String(value))}
              options={[
                {
                  label: 'Fixed reference',
                  value: 'FIXED_REFERENCE_EXECUTION',
                },
                {
                  label: 'Validation only',
                  value: 'CONTROL_POLICY_VALIDATION',
                },
              ]}
            />
          </Form.Item>
          {([
            ['connector_id', 'Hospital Connector', sources?.connectors],
            ['application_id', 'Approved application', sources?.applications],
            ['contract_id', 'Active contract', sources?.contracts],
            ['asset_version_id', 'Local asset metadata', sources?.asset_versions],
            ['model_version_id', 'Model metadata', sources?.model_versions],
          ] as const).map(([name, label, options]) => (
            <Form.Item
              key={name}
              name={name}
              label={label}
              rules={[{ required: true }]}
            >
              <Select
                showSearch
                optionFilterProp="label"
                options={(options || []).map((item) => ({
                  value: item.id,
                  label: item.label,
                }))}
              />
            </Form.Item>
          ))}
          {executionMode === 'FIXED_REFERENCE_EXECUTION' && (
            <Form.Item
              name="executor_mirror_id"
              label="Verified Executor Status v2"
              rules={[{ required: true }]}
            >
              <Select
                options={(sources?.executors || []).map((item) => ({
                  value: item.id,
                  label: item.label,
                }))}
              />
            </Form.Item>
          )}
          <Form.Item
            name="purpose_code"
            label="Purpose code"
            initialValue="FIXED_REFERENCE_AUTHORIZATION"
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={selectedPolicy?.policy_key || 'PolicyBundle'}
        open={Boolean(selectedPolicy)}
        onCancel={() => setSelectedPolicy(null)}
        footer={null}
        width={900}
      >
        <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
          <Descriptions.Item label="Status">
            {selectedPolicy?.status}
          </Descriptions.Item>
          <Descriptions.Item label="Action">
            {selectedPolicy?.version?.requested_action}
          </Descriptions.Item>
          <Descriptions.Item label="Execution authorized">
            {String(selectedPolicy?.version?.execution_authorized ?? false)}
          </Descriptions.Item>
          <Descriptions.Item label="Signing key">
            {selectedPolicy?.version?.signing_key_id || '-'}
          </Descriptions.Item>
          <Descriptions.Item label="Payload digest">
            {selectedPolicy?.version?.payload_digest || '-'}
          </Descriptions.Item>
        </Descriptions>
        <pre>
          {JSON.stringify(
            selectedPolicy?.version?.canonical_payload || {},
            null,
            2,
          )}
        </pre>
      </Modal>

      <Modal
        title={selectedOrder?.order_key || 'Signed order'}
        open={Boolean(selectedOrder)}
        onCancel={() => setSelectedOrder(null)}
        footer={null}
        width={860}
      >
        <Alert
          showIcon
          type={selectedOrder?.status === 'accepted' ? 'success' : 'info'}
          title={selectedOrder?.display_status || selectedOrder?.status}
          description={
            selectedOrder?.consumed_count
              ? 'Signed consumption receipt verified. Execution remains hospital-local.'
              : 'Local decision required; formal authorization remains unconsumed.'
          }
        />
        <Descriptions
          bordered
          size="small"
          column={{ xs: 1, md: 2 }}
          style={{ marginTop: 16 }}
        >
          <Descriptions.Item label="Mode">
            {selectedOrder?.order_mode}
          </Descriptions.Item>
          <Descriptions.Item label="Action">
            {selectedOrder?.requested_action}
          </Descriptions.Item>
          <Descriptions.Item label="Execution authorized">
            {String(selectedOrder?.execution_authorized ?? false)}
          </Descriptions.Item>
          <Descriptions.Item label="Execution started">
            {String(selectedOrder?.execution_started ?? false)}
          </Descriptions.Item>
          <Descriptions.Item label="Receipt">
            {selectedOrder?.receipt ? 'signed and verified' : 'pending'}
          </Descriptions.Item>
          <Descriptions.Item label="Local decision">
            {selectedOrder?.decision
              ? JSON.stringify(selectedOrder.decision)
              : 'pending'}
          </Descriptions.Item>
          <Descriptions.Item label="Consumption receipt">
            {selectedOrder?.consumption?.payload_digest || 'not consumed'}
          </Descriptions.Item>
          <Descriptions.Item label="Prebound execution">
            {selectedOrder?.consumption?.reference_execution_id || 'not created'}
          </Descriptions.Item>
        </Descriptions>
      </Modal>
    </div>
  )
}
