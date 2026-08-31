import { ApartmentOutlined, CheckCircleOutlined, SafetyCertificateOutlined, TeamOutlined } from '@ant-design/icons'
import { Button, Card, Col, Row, Space, Table, Tag } from 'antd'
import { PageHeading } from '../components/PageHeading'
import { demoRoles } from '../mock/data'

const members = demoRoles.map((role, index) => ({
  key: role.id,
  organization: role.organization,
  role: role.shortName,
  status: index === 2 ? '条件准入' : '已准入',
  resources: index === 0 ? '3 个数据产品' : index === 2 ? '2 个算法服务' : '—',
}))

export function GovernancePage() {
  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="逻辑空间与共识规则"
        title="空间治理"
        description="管理参与主体、可见资源与默认使用策略；当前仅展示一个数字病理 AI 协作空间。"
        actions={<Button type="primary" icon={<TeamOutlined />}>邀请演示成员</Button>}
      />
      <Card className="space-overview-card">
        <div className="space-overview-card__icon"><ApartmentOutlined /></div>
        <div>
          <Tag color="blue" bordered={false}>运行中 · 演示空间</Tag>
          <h2>数字病理 AI 协作空间</h2>
          <p>面向数字病理数据产品提供、受控使用和 AI 协作的逻辑可信数据空间。</p>
          <Space wrap>
            <Tag>4 个参与组织</Tag><Tag>3 个数据产品</Tag><Tag>2 个策略模板</Tag><Tag>3 个连接器</Tag>
          </Space>
        </div>
        <div className="space-overview-card__rule"><SafetyCertificateOutlined /><span><small>默认规则</small><strong>高敏感数据仅允许受控计算</strong></span></div>
      </Card>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card className="content-card" title="空间成员">
            <Table
              dataSource={members}
              pagination={false}
              columns={[
                { title: '参与组织', dataIndex: 'organization', render: (value) => <strong>{value}</strong> },
                { title: '生态角色', dataIndex: 'role' },
                { title: '空间资源', dataIndex: 'resources' },
                { title: '准入状态', dataIndex: 'status', render: (value) => <Tag color={value === '已准入' ? 'success' : 'warning'} bordered={false}>{value}</Tag> },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card className="content-card" title="共识规则模板">
            <div className="rule-template">
              <CheckCircleOutlined /><div><strong>科研受控计算模板</strong><p>限定科研用途、90 天、10 次任务、聚合结果审查。</p><Tag bordered={false}>默认模板</Tag></div>
            </div>
            <div className="rule-template">
              <CheckCircleOutlined /><div><strong>模型外部验证模板</strong><p>限定算法版本、输入只读、禁止模型制品直接出域。</p><Tag bordered={false}>AI 场景</Tag></div>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
