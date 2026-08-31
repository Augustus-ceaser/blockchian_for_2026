import { AuditOutlined, CheckCircleOutlined, FileDoneOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { Button, Card, Descriptions, Input, Select, Space, Table, Tag, Timeline, type TableProps } from 'antd'
import { PageHeading } from '../components/PageHeading'
import { StatusPill } from '../components/StatusPill'
import { auditEvents, hasReachedStage } from '../mock/data'
import { useDemo } from '../mock/DemoContext'
import type { AuditEvent } from '../mock/types'

export function AuditPage() {
  const { stage } = useDemo()
  const visibleEvents = auditEvents.filter((event) => hasReachedStage(stage, event.minStage)).reverse()
  const columns: TableProps<AuditEvent>['columns'] = [
    { title: '事件标识', dataIndex: 'id', width: 120, render: (value) => <span className="mono-id">{value}</span> },
    { title: '时间', dataIndex: 'time', width: 90 },
    { title: '主体', dataIndex: 'actor', width: 180 },
    { title: '行为', dataIndex: 'action', width: 170, render: (value) => <strong>{value}</strong> },
    { title: '业务客体', dataIndex: 'object' },
    { title: '结果', dataIndex: 'result', width: 90, render: (value) => <StatusPill value={value} /> },
    { title: '证据摘要', dataIndex: 'hash', width: 110, render: (value) => <span className="mono-id">{value}</span> },
  ]

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="主体—行为—客体—结果—证据"
        title="审计中心"
        description="统一查看产品、申请、合约、任务和结果审查事件，验证完整可信协作链路。"
        actions={<Button icon={<FileDoneOutlined />}>导出演示审计报告</Button>}
      />
      <div className="audit-proof-banner">
        <div><SafetyCertificateOutlined /><span><small>证据链状态</small><strong>连续 · 可核验（模拟）</strong></span></div>
        <div><span className="audit-proof-banner__number">{visibleEvents.length}</span><span><small>已记录事件</small><strong>覆盖当前演示阶段</strong></span></div>
        <Tag color="success" bordered={false}><CheckCircleOutlined /> 哈希链验证通过</Tag>
      </div>
      <Card className="content-card">
        <div className="table-toolbar">
          <Input.Search placeholder="搜索事件标识、主体或业务客体" allowClear />
          <Select value="全部行为" options={[{ label: '全部行为', value: '全部行为' }]} />
          <Select value="全部结果" options={[{ label: '全部结果', value: '全部结果' }]} />
        </div>
        <Table columns={columns} dataSource={visibleEvents} rowKey="id" pagination={false} scroll={{ x: 1100 }} />
      </Card>
      <div className="audit-detail-grid">
        <Card className="content-card" title="当前链路时间线">
          <Timeline
            items={visibleEvents.slice().reverse().map((event) => ({
              color: event.result === '待处理' ? 'orange' : 'blue',
              dot: <AuditOutlined />,
              children: <div className="timeline-event"><strong>{event.action}</strong><p>{event.actor} · {event.time}</p><span>{event.object}</span></div>,
            }))}
          />
        </Card>
        <Card className="content-card" title="证据查验示例">
          {visibleEvents[0] ? (
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="事件标识">{visibleEvents[0].id}</Descriptions.Item>
              <Descriptions.Item label="当前摘要">{visibleEvents[0].hash}</Descriptions.Item>
              <Descriptions.Item label="前序摘要">B71F…42A0（模拟）</Descriptions.Item>
              <Descriptions.Item label="时间来源">平台可信时钟（模拟）</Descriptions.Item>
              <Descriptions.Item label="验证结果"><Space><CheckCircleOutlined className="text-success" />事件结构与链路连续</Space></Descriptions.Item>
            </Descriptions>
          ) : <p>暂无审计事件。</p>}
          <div className="hash-explainer">哈希链用于解释防篡改思想，不代表本原型已经接入区块链或第三方权威存证。</div>
        </Card>
      </div>
    </div>
  )
}
