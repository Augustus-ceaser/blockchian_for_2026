import { CloudServerOutlined, LinkOutlined, SafetyCertificateOutlined, SyncOutlined } from '@ant-design/icons'
import { Card, Col, Descriptions, Progress, Row, Space, Tag } from 'antd'
import { PageHeading } from '../components/PageHeading'
import { StatusPill } from '../components/StatusPill'
import { connectors } from '../mock/data'

export function ConnectorsPage() {
  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="节点连接与能力"
        title="节点中心"
        description="查看已登记节点的身份、能力与运行状态。"
        actions={<Tag color="blue" bordered={false}>3 个连接器</Tag>}
      />
      <div className="connector-map">
        <div className="connector-map__node"><CloudServerOutlined /><strong>医院提供方</strong><span>本地资源与产品封装</span></div>
        <div className="connector-map__link"><span /><LinkOutlined /><span /></div>
        <div className="connector-map__platform"><SafetyCertificateOutlined /><strong>MedTrust 服务平台</strong><span>目录 · 合约 · 使用控制 · 审计</span></div>
        <div className="connector-map__link"><span /><LinkOutlined /><span /></div>
        <div className="connector-map__node"><CloudServerOutlined /><strong>数据使用方</strong><span>算法登记与履约回执</span></div>
      </div>
      <Row gutter={[16, 16]}>
        {connectors.map((connector) => (
          <Col xs={24} xl={8} key={connector.id}>
            <Card className="connector-card">
              <div className="connector-card__head">
                <div className="connector-card__icon"><CloudServerOutlined /></div>
                <StatusPill value={connector.status} />
              </div>
              <span className="mono-id">{connector.id}</span>
              <h3>{connector.name}</h3>
              <p>{connector.organization}</p>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="生态角色">{connector.role}</Descriptions.Item>
                <Descriptions.Item label="最近心跳">{connector.lastHeartbeat}</Descriptions.Item>
                <Descriptions.Item label="凭证状态">{connector.certificate}</Descriptions.Item>
              </Descriptions>
              <div className="tag-list">{connector.capabilities.map((item) => <Tag key={item}>{item}</Tag>)}</div>
              <div className="connector-health">
                <Space><SyncOutlined spin={connector.status === '在线'} /> 运行健康度</Space>
                <Progress percent={connector.status === '在线' ? 100 : 68} showInfo={false} strokeColor={connector.status === '在线' ? '#16856c' : '#d48806'} />
              </div>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  )
}
