import {
  ArrowRightOutlined,
  AuditOutlined,
  CodeSandboxOutlined,
  DatabaseOutlined,
  FileProtectOutlined,
} from '@ant-design/icons'
import { Button, Card, Col, List, Row, Space, Tag } from 'antd'
import { useNavigate } from 'react-router-dom'
import { FlowProgress } from '../components/FlowProgress'
import { MetricCard } from '../components/MetricCard'
import { MiniTrendChart } from '../components/MiniTrendChart'
import { PageHeading } from '../components/PageHeading'
import { StatusPill } from '../components/StatusPill'
import { auditEvents, hasReachedStage, products, stageLabels, stageOrder } from '../mock/data'
import { useDemo } from '../mock/DemoContext'

const nextRouteByStage = {
  catalog: '/products/npc-pathology-v1',
  'application-submitted': '/applications',
  'application-approved': '/contracts',
  'contract-active': '/compute',
  'compute-running': '/compute',
  'output-review': '/compute',
  'result-released': '/audit',
}

export function OverviewPage() {
  const navigate = useNavigate()
  const { role, stage } = useDemo()
  const visibleEvents = auditEvents.filter((event) => hasReachedStage(stage, event.minStage)).slice(-4).reverse()
  const currentIndex = stageOrder.indexOf(stage)
  const taskStatus = hasReachedStage(stage, 'result-released')
    ? '已发布'
    : hasReachedStage(stage, 'output-review')
      ? '待结果审查'
      : hasReachedStage(stage, 'compute-running')
        ? '运行中'
        : '待合约生效'

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="数字病理 AI 协作空间"
        title={`${role.name}工作台`}
        description={`${role.organization} · ${role.description}`}
        actions={<Button type="primary" onClick={() => navigate(nextRouteByStage[stage])}>继续演示链路 <ArrowRightOutlined /></Button>}
      />

      <div className="context-strip">
        <div>
          <span className="context-strip__label">当前案例</span>
          <strong>鼻咽癌复发风险模型外部验证</strong>
        </div>
        <div>
          <span className="context-strip__label">链路进度</span>
          <strong>{currentIndex + 1} / {stageOrder.length} · {stageLabels[stage]}</strong>
        </div>
        <Tag color="blue" bordered={false}>合约驱动 · 受控使用 · 全程留痕</Tag>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard label="有效数据产品" value={products.length} detail="全部为受控计算型" icon={<DatabaseOutlined />} tone="blue" />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard label="有效数字合约" value={hasReachedStage(stage, 'contract-active') ? 1 : 0} detail={hasReachedStage(stage, 'contract-active') ? '策略已下发至连接器' : '等待申请完成'} icon={<FileProtectOutlined />} tone="teal" />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard label="受控计算任务" value={hasReachedStage(stage, 'compute-running') ? 1 : 0} detail={taskStatus} icon={<CodeSandboxOutlined />} tone="purple" />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard label="履约证据事件" value={visibleEvents.length} detail="统一审计链可核验" icon={<AuditOutlined />} tone="amber" />
        </Col>
      </Row>

      <Card className="content-card" title="可信流通主链路" extra={<Button type="link" onClick={() => navigate('/audit')}>查看证据链</Button>}>
        <FlowProgress />
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card className="content-card" title="待办与履约事件" extra={<Tag bordered={false}>按当前身份过滤</Tag>}>
            <List
              dataSource={visibleEvents}
              locale={{ emptyText: '开始申请后将生成第一条履约事件' }}
              renderItem={(item) => (
                <List.Item actions={[<StatusPill key="status" value={item.result} />]}>
                  <List.Item.Meta
                    avatar={<div className="event-icon"><AuditOutlined /></div>}
                    title={<Space><span>{item.action}</span><span className="mono-id">{item.id}</span></Space>}
                    description={`${item.actor} · ${item.object} · ${item.time}`}
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card className="content-card" title="可信流通活动" extra={<span className="card-subtle">近 2 小时 · 演示</span>}>
            <MiniTrendChart />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
