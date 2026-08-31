import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { Button, Card, Descriptions, Progress, Space, Statistic, Tabs, Tag, Timeline } from 'antd'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { PageHeading } from '../components/PageHeading'
import { products } from '../mock/data'

export function ProductDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const product = products.find((item) => item.id === id)
  if (!product) return <Navigate to="/products" replace />

  const isPrimary = product.id === 'npc-pathology-v1'
  const tabs = [
    {
      key: 'overview',
      label: '产品概览',
      children: (
        <div className="detail-grid">
          <Card className="inner-card" title="产品价值与边界">
            <p className="detail-lead">{product.summary}</p>
            <Descriptions column={2} size="small">
              <Descriptions.Item label="适用癌种">{product.disease}</Descriptions.Item>
              <Descriptions.Item label="使用模式"><Tag color="blue">{product.useMode}</Tag></Descriptions.Item>
              <Descriptions.Item label="产品版本">{product.version}</Descriptions.Item>
              <Descriptions.Item label="最近更新">{product.updatedAt}</Descriptions.Item>
              <Descriptions.Item label="分类分级" span={2}>{product.classification}</Descriptions.Item>
            </Descriptions>
          </Card>
          <Card className="inner-card" title="产品规模">
            <div className="statistics-row">
              <Statistic title="病例索引" value={product.caseCount} suffix="例" />
              <Statistic title="切片索引" value={product.slideCount} suffix="张" />
              <Statistic title="数据模态" value={product.modalities.length} suffix="类" />
            </div>
            <div className="data-boundary-note"><SafetyCertificateOutlined /> 仅提供产品级统计。原始数据保留在提供方控制域内。</div>
          </Card>
        </div>
      ),
    },
    {
      key: 'composition',
      label: '数据构成',
      children: product.composition.length ? (
        <div className="composition-grid">
          {product.composition.map((item) => (
            <div className="composition-item" key={item.label}>
              <DatabaseOutlined />
              <div><span>{item.label}</span><strong>{item.value}</strong><p>{item.detail}</p></div>
            </div>
          ))}
        </div>
      ) : <div className="empty-panel">该演示产品的详细数据构成将在后续版本补充。</div>,
    },
    {
      key: 'quality',
      label: '质量报告',
      children: product.quality.length ? (
        <div className="quality-grid">
          {product.quality.map((item) => (
            <div className="quality-item" key={item.label}>
              <Progress type="dashboard" percent={item.value} size={94} strokeColor="#1769aa" />
              <strong>{item.label}</strong>
              <span>{item.display} · 演示评分</span>
            </div>
          ))}
          <div className="quality-caveat">
            <strong>已知偏倚与适用边界</strong>
            <p>单一区域来源、治疗方案分布不均；仅用于展示质量元数据结构，不可用于真实临床推断。</p>
          </div>
        </div>
      ) : <div className="empty-panel">质量报告正在生成。</div>,
    },
    {
      key: 'policy',
      label: '使用策略',
      children: (
        <div className="policy-columns">
          <Card className="policy-card policy-card--allow" title="允许的使用">
            {product.allowedUses.map((item) => <div key={item}><CheckCircleOutlined /> {item}</div>)}
          </Card>
          <Card className="policy-card policy-card--deny" title="禁止的操作">
            {product.prohibitedUses.map((item) => <div key={item}><CloseCircleOutlined /> {item}</div>)}
          </Card>
          <Card className="policy-card" title="强制控制策略">
            <div><SafetyCertificateOutlined /> 指定受控计算环境</div>
            <div><SafetyCertificateOutlined /> 使用期限 90 天</div>
            <div><SafetyCertificateOutlined /> 最多运行 10 次</div>
            <div><SafetyCertificateOutlined /> 输出制品人工审查</div>
          </Card>
        </div>
      ),
    },
    {
      key: 'source',
      label: '来源节点',
      children: (
        <Card className="connector-preview">
          <CloudServerOutlined className="connector-preview__icon" />
          <div>
            <Tag color="success" bordered={false}>在线 · 演示</Tag>
            <h3>病理数据提供方连接器</h3>
            <p>{product.provider}</p>
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="连接器标识">{product.connectorId}</Descriptions.Item>
              <Descriptions.Item label="能力">产品封装、策略执行、使用存证</Descriptions.Item>
              <Descriptions.Item label="目录同步">已同步</Descriptions.Item>
              <Descriptions.Item label="证书状态">演示证书 · 有效</Descriptions.Item>
            </Descriptions>
          </div>
        </Card>
      ),
    },
    {
      key: 'versions',
      label: '版本记录',
      children: (
        <Timeline
          items={[
            { color: 'blue', children: <><strong>{product.version} · 当前版本</strong><p>补充输出审查规则和产品质量摘要 · {product.updatedAt}</p></> },
            { color: 'gray', children: <><strong>v1.1</strong><p>更新随访覆盖与允许用途范围 · 2026-06-28</p></> },
            { color: 'gray', children: <><strong>v1.0</strong><p>首次登记并纳入空间目录 · 2026-06-12</p></> },
          ]}
        />
      ),
    },
  ]

  return (
    <div className="page-stack">
      <Button type="text" icon={<ArrowLeftOutlined />} className="back-button" onClick={() => navigate('/products')}>返回数据产品目录</Button>
      <PageHeading
        eyebrow={`数据产品标识 · DP-${product.id.toUpperCase()}`}
        title={product.name}
        description={product.provider}
        actions={
          <Space>
            <Tag color="blue" bordered={false}>{product.useMode}</Tag>
            <Button type="primary" disabled={!isPrimary} onClick={() => navigate('/applications?new=npc-pathology-v1')}>
              申请使用 <ArrowRightOutlined />
            </Button>
          </Space>
        }
      />
      <div className="product-trust-strip">
        <span><SafetyCertificateOutlined /> 原始数据不默认离开提供方控制域</span>
        <span><FilePolicyIcon /> 合约策略约束使用主体、环境、次数与输出</span>
        <span><CheckCircleOutlined /> 结果需审查后发布</span>
      </div>
      <Card className="content-card detail-tabs-card">
        <Tabs items={tabs} defaultActiveKey="overview" />
      </Card>
    </div>
  )
}

function FilePolicyIcon() {
  return <SafetyCertificateOutlined />
}
