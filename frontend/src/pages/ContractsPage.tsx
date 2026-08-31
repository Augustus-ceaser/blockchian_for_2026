import { ArrowRightOutlined, FileProtectOutlined, LockOutlined, SafetyCertificateOutlined, SignatureOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Divider, Space, Tag } from 'antd'
import { useNavigate } from 'react-router-dom'
import { PageHeading } from '../components/PageHeading'
import { StatusPill } from '../components/StatusPill'
import { hasReachedStage } from '../mock/data'
import { useDemo } from '../mock/DemoContext'

const policyItems = [
  ['使用主体', '启明医疗智能（演示机构）· 指定项目成员'],
  ['使用产品', '鼻咽癌数字病理多模态研究数据产品 v1.2'],
  ['允许操作', '预登记算法运行、聚合统计、模型外部验证'],
  ['使用环境', '指定受控计算环境 · 外网禁用 · 输入只读'],
  ['期限与次数', '90 天 · 最多 10 次任务'],
  ['输出控制', '聚合指标人工审查后发布；模型制品禁止直接出域'],
  ['到期处置', '临时工作区销毁，保留履约证据'],
]

export function ContractsPage() {
  const navigate = useNavigate()
  const { stage, setStage, setRole } = useDemo()
  const approved = hasReachedStage(stage, 'application-approved')
  const active = hasReachedStage(stage, 'contract-active')

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="规则共识与机器可读策略"
        title="数字合约"
        description="合约描述谁能在什么环境、以什么方式、使用多少次以及允许哪些结果出域。"
      />
      {!approved ? (
        <Card className="empty-state-card">
          <LockOutlined />
          <h3>数字合约尚未解锁</h3>
          <p>只有使用申请通过平台预审和数据提供方审核后，才能创建数字合约。</p>
          <Button type="primary" onClick={() => navigate('/applications')}>返回使用申请</Button>
        </Card>
      ) : (
        <>
          <div className="contract-banner">
            <div><FileProtectOutlined /><span><small>合约编号</small><strong>MTS-DC-2026-00018</strong></span></div>
            <StatusPill value={active ? '已生效' : '待签署'} />
          </div>
          <Card className="content-card contract-card">
            <Descriptions column={2} bordered size="small">
              <Descriptions.Item label="数据提供方">华南肿瘤研究中心（演示机构）</Descriptions.Item>
              <Descriptions.Item label="数据使用方">启明医疗智能（演示机构）</Descriptions.Item>
              <Descriptions.Item label="关联申请">APP-2026-0718-001</Descriptions.Item>
              <Descriptions.Item label="合约版本">v1 · 内容不可原地覆盖</Descriptions.Item>
              <Descriptions.Item label="生效时间">{active ? '2026-07-21 10:02（演示）' : '双方签署后生效'}</Descriptions.Item>
              <Descriptions.Item label="摘要哈希">SHA-256 · 4AD2…6F10（演示）</Descriptions.Item>
            </Descriptions>
            <Divider orientation="left">结构化使用策略</Divider>
            <div className="policy-table">
              {policyItems.map(([label, value]) => (
                <div key={label}><span>{label}</span><strong>{value}</strong></div>
              ))}
            </div>
            <Alert
              showIcon
              type="info"
              message="这不是区块链智能合约"
              description="本页用于演示数字合约的规则表达与策略下发。签署和哈希均为模拟，不具有真实电子签名法律效力。"
            />
            <div className="signature-row">
              <div className={active ? 'is-signed' : ''}><SignatureOutlined /><span>数据提供方签署</span><strong>{active ? '已签署 · 演示' : '等待签署'}</strong></div>
              <div className={active ? 'is-signed' : ''}><SignatureOutlined /><span>数据使用方签署</span><strong>{active ? '已签署 · 演示' : '等待签署'}</strong></div>
            </div>
            <div className="form-actions">
              {!active ? (
                <Button
                  type="primary"
                  onClick={() => {
                    setRole('ai')
                    setStage('contract-active')
                  }}
                >
                  模拟双方签署并下发策略 <SafetyCertificateOutlined />
                </Button>
              ) : (
                <Space>
                  <Tag color="success" bordered={false}>策略已下发至双方连接器</Tag>
                  <Button type="primary" onClick={() => navigate('/compute')}>进入可信计算 <ArrowRightOutlined /></Button>
                </Space>
              )}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
