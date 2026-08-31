import {
  Alert, Button, Descriptions, Form, Input, Modal, Select, Space, Statistic,
  Table, Tag, Typography, message,
} from 'antd'
import {
  CheckOutlined, CloseOutlined, PauseOutlined, PlayCircleOutlined,
  ReloadOutlined, SafetyCertificateOutlined, StopOutlined,
} from '@ant-design/icons'
import { useCallback, useEffect, useState } from 'react'
import { platformCommand, platformGet } from './api'
import { useRoadshow } from './RoadshowContext'

type Connector = {
  id: string
  organization_id: string
  connector_instance_id: string
  display_name: string
  environment: string
  connector_version: string
  operating_system: string
  architecture: string
  status: string
  heartbeat_status: string
  last_heartbeat_at?: string | null
  last_heartbeat_sequence: number
  hard_isolation: false
  execution_enabled: false
  data_transfer_enabled: false
  model_transfer_enabled: false
  local_asset_registry_enabled: boolean
  artifact_egress_enabled: false
  certificate?: { fingerprint: string; issuer: string; valid_from: string; valid_to: string; status: string } | null
  capability_manifest?: { manifest_version: string; sequence: number; digest: string; payload: Record<string, unknown> } | null
}

type Registration = {
  id: string
  organization_id: string
  connector_instance_id: string
  display_name: string
  connector_version: string
  operating_system: string
  architecture: string
  csr_fingerprint: string
  bootstrap_manifest_digest: string
  status: string
  created_at: string
}

type ConnectorAsset = {
  id: string
  connector_id: string
  local_asset_key: string
  display_name: string
  asset_kind: string
  modality: string
  source_category: string
  sensitivity_classification: string
  status: string
  metadata_only: true
  requestable: false
  execution_permitted: false
  materialized: false
  version?: {
    version_label: string
    bundle_sequence: number
    disclosure_summary: Record<string, unknown>
    quality_summary: Record<string, unknown>
    deidentification_summary: Record<string, unknown>
    known_limitations: string[]
    warning_flags: string[]
    metadata_digest: string
    quality_digest: string
  } | null
}

type ConnectorAssetDetail = ConnectorAsset & {
  versions: Array<NonNullable<ConnectorAsset['version']> & {
    id: string
    metadata_summary: Record<string, unknown>
    schema_digest: string
    created_at: string
  }>
}

type HospitalExecutor = {
  id: string
  connector_id: string
  organization_id: string
  executor_instance_id: string
  executor_version: string
  architecture: string
  status: string
  certificate_fingerprint: string
  capability_manifest_digest: string
  runtime_digest: string
  image_digest: string
  security_status: string
  last_status_sequence: number
  last_heartbeat_sequence: number
  last_heartbeat_at?: string | null
  execution_enabled: false
  hard_isolation: false
  control_only: true
  fixed_reference_readiness_status?: 'ready' | 'not_ready' | null
  fixed_reference_readiness_reason?: string | null
  latest_verified_readiness_at?: string | null
  readiness_valid_until?: string | null
  latest_status_event_sequence?: number | null
  latest_verified_readiness_digest?: string | null
  attested_image_digest?: string | null
  attested_security_profile_digest?: string | null
  attested_resource_policy_digest?: string | null
  attested_admission_digest?: string | null
  readiness_statement?: string
}

type HospitalEvidenceReceipt = {
  id: string
  bundle_id: string
  connector_id: string
  schema_version: string
  bundle_version: number
  artifact_digest: string
  reference_execution_id: string
  bundle_digest: string
  review_digest: string
  causal_validation_digest: string
  local_audit_head: string
  verification_status: 'verified'
  result_summary: {
    sample_count: number
    correct_count: number
    accuracy: string
    non_clinical: boolean
    hard_isolation: false
  }
  security_boundaries: {
    network_access: false
    raw_data_transfer: false
    model_transfer: false
    artifact_auto_egress: false
    hard_isolation: false
  }
  received_at: string
  artifact_received: false
  raw_data_received: false
  local_path_received: false
  hard_isolation: false
}

const statusColor: Record<string, string> = {
  active: 'green', paused: 'orange', offline: 'default', revoked: 'red',
  submitted: 'processing', certificate_issued: 'green', rejected: 'red',
}

export function ConnectorControlPage() {
  const { identity } = useRoadshow()
  const operator = identity === 'space_operator'
  const [connectors, setConnectors] = useState<Connector[]>([])
  const [registrations, setRegistrations] = useState<Registration[]>([])
  const [assets, setAssets] = useState<ConnectorAsset[]>([])
  const [executors, setExecutors] = useState<HospitalExecutor[]>([])
  const [evidenceReceipts, setEvidenceReceipts] = useState<HospitalEvidenceReceipt[]>([])
  const [selected, setSelected] = useState<Connector | null>(null)
  const [selectedAsset, setSelectedAsset] = useState<ConnectorAssetDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [tokenOpen, setTokenOpen] = useState(false)
  const [token, setToken] = useState<string | null>(null)
  const [options, setOptions] = useState<{ id: string; name: string }[]>([])
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [connectorResult, assetResult, executorResult, evidenceResult] = await Promise.all([
        platformGet<{ items: Connector[] }>('/connector-control/connectors', identity),
        platformGet<{ items: ConnectorAsset[] }>('/connector-control/assets', identity),
        platformGet<{ items: HospitalExecutor[] }>('/connector-control/executors', identity),
        platformGet<{ items: HospitalEvidenceReceipt[] }>('/connector-control/evidence-bundles', identity),
      ])
      setConnectors(connectorResult.items)
      setAssets(assetResult.items)
      setExecutors(executorResult.items)
      setEvidenceReceipts(evidenceResult.items)
      setSelected((current) => connectorResult.items.find((item) => item.id === current?.id) || current)
      if (operator) {
        const [registrationResult, optionResult] = await Promise.all([
          platformGet<{ items: Registration[] }>('/connector-control/registrations', identity),
          platformGet<{ items: { id: string; name: string }[] }>('/connector-control/enrollment-options', identity),
        ])
        setRegistrations(registrationResult.items)
        setOptions(optionResult.items)
      }
    } finally {
      setLoading(false)
    }
  }, [identity, operator])

  useEffect(() => { void load() }, [load])

  const decide = async (row: Registration, decision: 'approve' | 'reject') => {
    await platformCommand(`/connector-control/registrations/${row.id}/decision`, identity, crypto.randomUUID(), {
      decision,
      reason: decision === 'reject' ? 'Registration evidence did not pass operator review.' : undefined,
    })
    message.success(decision === 'approve' ? '注册已批准并签发本地证书' : '注册已拒绝')
    await load()
  }

  const transition = async (row: Connector, action: 'pause' | 'resume' | 'revoke') => {
    await platformCommand(`/connector-control/connectors/${row.id}/${action}`, identity, crypto.randomUUID(), {
      reason: `Operator ${action} control-alpha connector.`,
    })
    message.success(`Connector ${action} 已记录`)
    await load()
  }

  const createToken = async () => {
    const values = await form.validateFields()
    const result = await platformCommand<{ enrollment_token: string }>(
      '/connector-control/enrollment-tokens', identity, crypto.randomUUID(), values,
    )
    setToken(result.enrollment_token)
    form.resetFields()
  }

  const counts = {
    total: connectors.length,
    active: connectors.filter((item) => item.status === 'active').length,
    paused: connectors.filter((item) => item.status === 'paused').length,
    offline: connectors.filter((item) => item.status === 'offline').length,
    revoked: connectors.filter((item) => item.status === 'revoked').length,
    metadata_assets: assets.length,
    executors: executors.length,
    verified_evidence: evidenceReceipts.length,
  }

  return <div className="page-stack connector-control-page">
    <div className="external-governance-heading">
      <div>
        <Typography.Title level={3}>Hospital Connector 控制与证据中心</Typography.Title>
        <Typography.Text type="secondary">统一查看节点身份、生命周期、资产、Executor 就绪状态与证据摘要。</Typography.Text>
      </div>
      <Space>
        {operator && <Button icon={<SafetyCertificateOutlined />} onClick={() => { setToken(null); setTokenOpen(true) }}>创建 Enrollment Token</Button>}
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button>
      </Space>
    </div>
    <Space wrap size={24}>
      {Object.entries(counts).map(([key, value]) => <Statistic key={key} title={key} value={value} />)}
    </Space>
    {operator && <Table rowKey="id" dataSource={registrations} pagination={false} scroll={{ x: 900 }}
      columns={[
        { title: '注册请求', dataIndex: 'display_name' },
        { title: '环境', render: (_: unknown, row: Registration) => `${row.operating_system} / ${row.architecture}` },
        { title: 'CSR fingerprint', dataIndex: 'csr_fingerprint', ellipsis: true },
        { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={statusColor[value]}>{value}</Tag> },
        { title: '操作', render: (_: unknown, row: Registration) => row.status === 'submitted' && <Space>
          <Button title="批准" icon={<CheckOutlined />} onClick={() => void decide(row, 'approve')} />
          <Button danger title="拒绝" icon={<CloseOutlined />} onClick={() => void decide(row, 'reject')} />
        </Space> },
      ]} />}
    <Table rowKey="id" dataSource={connectors} pagination={false} scroll={{ x: 920 }}
      columns={[
        { title: 'Connector', dataIndex: 'display_name' },
        { title: '版本', dataIndex: 'connector_version' },
        { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={statusColor[value]}>{value}</Tag> },
        { title: 'Heartbeat', dataIndex: 'heartbeat_status' },
        { title: '能力', render: () => <Space wrap><Tag>general execution disabled</Tag><Tag>data transfer disabled</Tag></Space> },
        { title: '操作', render: (_: unknown, row: Connector) => <Space>
          <Button type="link" onClick={async () => setSelected(await platformGet<Connector>(`/connector-control/connectors/${row.id}`, identity))}>详情</Button>
          {operator && row.status === 'active' && <Button title="暂停" icon={<PauseOutlined />} onClick={() => void transition(row, 'pause')} />}
          {operator && row.status === 'paused' && <Button title="恢复" icon={<PlayCircleOutlined />} onClick={() => void transition(row, 'resume')} />}
          {operator && row.status !== 'revoked' && <Button danger title="撤销" icon={<StopOutlined />} onClick={() => void transition(row, 'revoke')} />}
        </Space> },
      ]} />
    <Typography.Title level={4}>Hospital Local Executors</Typography.Title>
    <Table rowKey="id" dataSource={executors} pagination={false} scroll={{ x: 1100 }}
      columns={[
        { title: 'Executor', dataIndex: 'executor_instance_id' },
        { title: 'Version', dataIndex: 'executor_version' },
        { title: 'Architecture', dataIndex: 'architecture' },
        { title: 'Status', dataIndex: 'status', render: (value: string) => <Tag color={statusColor[value]}>{value}</Tag> },
        { title: 'Security', dataIndex: 'security_status', render: (value: string) => <Tag color={value === 'passed' ? 'green' : 'orange'}>{value}</Tag> },
        { title: 'Heartbeat', render: (_: unknown, row: HospitalExecutor) => `${row.last_heartbeat_sequence} / ${row.last_heartbeat_at || 'never'}` },
        { title: 'Capability digest', dataIndex: 'capability_manifest_digest', ellipsis: true },
        { title: 'Fixed-reference readiness', render: (_: unknown, row: HospitalExecutor) =>
          <Space direction="vertical" size={0}>
            <Tag color={row.fixed_reference_readiness_status === 'ready' ? 'green' : 'default'}>
              {row.fixed_reference_readiness_status || 'not attested'}
            </Tag>
            <Typography.Text type="secondary">
              {row.fixed_reference_readiness_status === 'ready'
                ? 'Central signature verified · policy compilation only · not executed'
                : row.fixed_reference_readiness_reason || 'No verified v2 source'}
            </Typography.Text>
          </Space> },
        { title: 'Boundary', render: () => <Space wrap>
          <Tag>control only</Tag>
          <Tag>general execution disabled</Tag>
          <Tag color="blue">PATHMNIST_REFERENCE_V1 readiness only</Tag>
        </Space> },
      ]} />
    <Typography.Title level={4}>Hospital Evidence Registry</Typography.Title>
    <Table rowKey="id" dataSource={evidenceReceipts} pagination={false} scroll={{ x: 1180 }}
      columns={[
        { title: 'Bundle', dataIndex: 'bundle_id', ellipsis: true },
        { title: '验证', dataIndex: 'verification_status', render: (value: string) => <Tag color="green">{value}</Tag> },
        { title: '固定结果', render: (_: unknown, row: HospitalEvidenceReceipt) =>
          `${row.result_summary.sample_count} / ${row.result_summary.correct_count} / ${row.result_summary.accuracy}` },
        { title: 'Bundle digest', dataIndex: 'bundle_digest', ellipsis: true },
        { title: 'Causal validation', dataIndex: 'causal_validation_digest', ellipsis: true },
        { title: 'Hospital audit head', dataIndex: 'local_audit_head', ellipsis: true },
        { title: '中央边界', render: (_: unknown, row: HospitalEvidenceReceipt) => <Space wrap>
          <Tag color={row.artifact_received ? 'red' : 'green'}>Artifact received: {String(row.artifact_received)}</Tag>
          <Tag color={row.raw_data_received ? 'red' : 'green'}>Raw data: {String(row.raw_data_received)}</Tag>
          <Tag color={row.local_path_received ? 'red' : 'green'}>Local path: {String(row.local_path_received)}</Tag>
        </Space> },
      ]} />
    <Typography.Title level={4}>Local assets</Typography.Title>
    <Table rowKey="id" dataSource={assets} pagination={false} scroll={{ x: 980 }}
      columns={[
        { title: 'Asset', dataIndex: 'display_name', render: (value: string, row: ConnectorAsset) =>
          <Button type="link" onClick={async () => setSelectedAsset(
            await platformGet<ConnectorAssetDetail>(`/connector-control/assets/${row.id}`, identity),
          )}>{value}</Button> },
        { title: 'Version', render: (_: unknown, row: ConnectorAsset) => row.version?.version_label || '-' },
        { title: 'Modality', dataIndex: 'modality' },
        { title: 'Disclosure', render: (_: unknown, row: ConnectorAsset) => Object.keys(row.version?.disclosure_summary || {}).join(', ') },
        { title: 'Quality boundary', render: (_: unknown, row: ConnectorAsset) => <Space wrap>
          <Tag color="blue">metadata only</Tag>
          <Tag>not requestable</Tag>
          <Tag>execution disabled</Tag>
        </Space> },
        { title: 'Warnings', render: (_: unknown, row: ConnectorAsset) => <Space wrap>{row.version?.warning_flags.map((flag) => <Tag key={flag} color="orange">{flag}</Tag>)}</Space> },
      ]} />
    {selected && <div className="evidence-review-panel">
      <Typography.Title level={4}>{selected.display_name}</Typography.Title>
      <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="Connector ID">{selected.connector_instance_id}</Descriptions.Item>
        <Descriptions.Item label="状态">{selected.status}</Descriptions.Item>
        <Descriptions.Item label="证书">{selected.certificate?.status || 'pending'}</Descriptions.Item>
        <Descriptions.Item label="证书签发方">{selected.certificate?.issuer || 'Local Test CA'}</Descriptions.Item>
        <Descriptions.Item label="Heartbeat sequence">{selected.last_heartbeat_sequence}</Descriptions.Item>
        <Descriptions.Item label="Capability">{selected.capability_manifest?.manifest_version || 'not received'}</Descriptions.Item>
        <Descriptions.Item label="执行">general execution disabled</Descriptions.Item>
        <Descriptions.Item label="数据访问">disabled</Descriptions.Item>
      </Descriptions>
    </div>}
    <Modal title={selectedAsset?.display_name || 'Connector Asset metadata mirror'}
      open={Boolean(selectedAsset)} onCancel={() => setSelectedAsset(null)} footer={null} width={920}>
      <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="Local Asset Key">{selectedAsset?.local_asset_key}</Descriptions.Item>
        <Descriptions.Item label="边界">metadata only / not requestable</Descriptions.Item>
        <Descriptions.Item label="执行">disabled</Descriptions.Item>
        <Descriptions.Item label="物化">false</Descriptions.Item>
      </Descriptions>
      <Typography.Title level={5}>版本与 Quality Snapshot 历史</Typography.Title>
      <Table rowKey="id" dataSource={selectedAsset?.versions || []} pagination={false} scroll={{ x: 760 }}
        columns={[
          { title: '版本', dataIndex: 'version_label' },
          { title: 'Bundle sequence', dataIndex: 'bundle_sequence' },
          { title: 'Quality snapshot', render: (_: unknown, row) => JSON.stringify(row.quality_summary) },
          { title: 'Warnings', render: (_: unknown, row) => <Space wrap>{row.warning_flags.map((flag) => <Tag key={flag}>{flag}</Tag>)}</Space> },
          { title: 'Metadata digest', dataIndex: 'metadata_digest', ellipsis: true },
        ]} />
    </Modal>
    <Modal title="一次性 Enrollment Token" open={tokenOpen} onCancel={() => setTokenOpen(false)} footer={null} destroyOnHidden>
      {token ? <Alert type="warning" showIcon title="仅显示一次" description={<Typography.Text copyable code>{token}</Typography.Text>} /> :
        <Form form={form} layout="vertical" onFinish={() => void createToken()}>
          <Form.Item name="organization_id" label="医院组织" rules={[{ required: true }]}>
            <Select options={options.map((item) => ({ value: item.id, label: item.name }))} />
          </Form.Item>
          <Form.Item name="connector_name" label="Connector 名称" initialValue="Hospital Connector" rules={[{ required: true, min: 3 }]}>
            <Input />
          </Form.Item>
          <Form.Item name="lifetime_minutes" initialValue={15} hidden><Input /></Form.Item>
          <Button htmlType="submit" type="primary" block>生成一次性 Token</Button>
        </Form>}
    </Modal>
  </div>
}
