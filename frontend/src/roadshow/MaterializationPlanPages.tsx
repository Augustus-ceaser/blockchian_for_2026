import {
  Button,
  Descriptions,
  Empty,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useState } from 'react'
import { platformCommand, platformGet } from './api'
import { useRoadshow } from './RoadshowContext'

type Plan = {
  id: string
  relation_id: string
  plan_status: string
  data_plan: Record<string, unknown>
  model_plan: Record<string, unknown>
  transformation_plan: Record<string, unknown>
  data_estimated_bytes: number
  model_estimated_bytes: number
  total_estimated_bytes: number
  hardware_requirements: Record<string, unknown>
  license_snapshot: { result?: string }
  access_snapshot: { result?: string; gated?: boolean; private_token_required?: boolean }
  security_preflight: { result?: string }
  blocking_reasons: string[]
  rejection_reasons: string[]
  plan_digest: string
  submitted_by?: string | null
  approved_by?: string | null
  asset_downloaded: false
  data_materialized: false
  model_materialized: false
  executor_registered: false
  execution_ready: false
}

const bytes = (value: number) => {
  if (!value) return '0 B'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 2)} ${units[index]}`
}

const statusColor: Record<string, string> = {
  draft: 'default',
  submitted: 'processing',
  approved: 'green',
  rejected: 'red',
  cancelled: 'orange',
}

export function MaterializationPlanPage() {
  const { identity } = useRoadshow()
  const [plans, setPlans] = useState<Plan[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<Plan | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await platformGet<{ items: Plan[] }>('/materialization-plans', identity)
      setPlans(result.items)
      setSelected((current) => result.items.find((item) => item.id === current?.id) || null)
    } finally {
      setLoading(false)
    }
  }, [identity])

  useEffect(() => { void load() }, [load])

  const decide = async (plan: Plan, decision: 'approve' | 'reject') => {
    const endpoint = `/materialization-plans/${plan.id}/${decision}`
    const payload = decision === 'reject'
      ? { decision: 'reject', reasons: ['Mandatory access or security evidence did not pass.'] }
      : undefined
    await platformCommand(endpoint, identity, crypto.randomUUID(), payload)
    message.success(decision === 'approve' ? '计划已批准' : '计划已拒绝')
    await load()
  }

  const columns = [
    { title: '关系', dataIndex: 'relation_id', width: 220, ellipsis: true },
    {
      title: '状态',
      dataIndex: 'plan_status',
      width: 110,
      render: (value: string) => <Tag color={statusColor[value]}>{value}</Tag>,
    },
    {
      title: '计划容量',
      dataIndex: 'total_estimated_bytes',
      width: 130,
      render: bytes,
    },
    {
      title: '许可 / 访问 / 安全',
      width: 220,
      render: (_: unknown, item: Plan) => <Space wrap size={4}>
        <Tag color={item.license_snapshot.result === 'pass' ? 'green' : 'red'}>许可</Tag>
        <Tag color={item.access_snapshot.result === 'pass' ? 'green' : 'red'}>访问</Tag>
        <Tag color={item.security_preflight.result === 'pass' ? 'green' : 'red'}>安全</Tag>
      </Space>,
    },
    {
      title: '操作',
      width: 210,
      render: (_: unknown, item: Plan) => <Space>
        <Button type="link" onClick={() => setSelected(item)}>查看</Button>
        {identity === 'space_operator' && item.plan_status === 'submitted' && <>
          <Button
            title="批准计划"
            icon={<CheckOutlined />}
            onClick={() => void decide(item, 'approve')}
          />
          <Button
            danger
            title="拒绝计划"
            icon={<CloseOutlined />}
            onClick={() => void decide(item, 'reject')}
          />
        </>}
      </Space>,
    },
  ]

  return <div className="page-stack materialization-plan-page">
    <div className="external-governance-heading">
      <div>
        <Typography.Title level={3}>资产物化计划</Typography.Title>
        <Typography.Text type="secondary">查看资产准备计划与审批状态。</Typography.Text>
      </div>
      <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button>
    </div>
    <Table
      rowKey="id"
      loading={loading}
      columns={columns}
      dataSource={plans}
      pagination={false}
      scroll={{ x: 920 }}
      locale={{ emptyText: <Empty description="0 个物化计划，0 个获批计划" /> }}
    />
    {selected && <div className="evidence-review-panel">
      <Typography.Title level={4}>计划详情</Typography.Title>
      <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="计划状态">{selected.plan_status}</Descriptions.Item>
        <Descriptions.Item label="计划摘要">{selected.plan_digest}</Descriptions.Item>
        <Descriptions.Item label="数据容量">{bytes(selected.data_estimated_bytes)}</Descriptions.Item>
        <Descriptions.Item label="模型容量">{bytes(selected.model_estimated_bytes)}</Descriptions.Item>
        <Descriptions.Item label="总容量">{bytes(selected.total_estimated_bytes)}</Descriptions.Item>
        <Descriptions.Item label="安全结果">{selected.security_preflight.result || 'unknown'}</Descriptions.Item>
        <Descriptions.Item label="提交人">{selected.submitted_by || '-'}</Descriptions.Item>
        <Descriptions.Item label="审批人">{selected.approved_by || '-'}</Descriptions.Item>
      </Descriptions>
    </div>}
  </div>
}
