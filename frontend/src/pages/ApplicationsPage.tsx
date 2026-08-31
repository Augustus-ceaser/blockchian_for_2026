import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  FileSearchOutlined,
  PaperClipOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Form, Input, Select, Space, Steps, Tag } from 'antd'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { PageHeading } from '../components/PageHeading'
import { StatusPill } from '../components/StatusPill'
import { hasReachedStage, products } from '../mock/data'
import { useDemo } from '../mock/DemoContext'

export function ApplicationsPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { stage, setStage, setRole } = useDemo()
  const product = products[0]
  const isNew = params.get('new') === product.id && stage === 'catalog'
  const submitted = hasReachedStage(stage, 'application-submitted')
  const approved = hasReachedStage(stage, 'application-approved')

  if (isNew) {
    return (
      <div className="page-stack">
        <PageHeading
          eyebrow="创建使用申请"
          title="申请按约使用数据产品"
          description="提交用途、算法与预期输出；申请通过不等于获得原始数据下载权限。"
        />
        <Alert
          showIcon
          type="info"
          message="申请对象"
          description={`${product.name} · ${product.version} · ${product.classification}`}
        />
        <Card className="content-card form-card">
          <Form layout="vertical" initialValues={{ purpose: '鼻咽癌复发风险模型外部验证', duration: '90 天', output: '聚合评估指标', algorithm: 'NPC-RiskNet v0.8（演示算法）' }}>
            <div className="form-grid">
              <Form.Item label="项目名称" name="purpose" required><Input /></Form.Item>
              <Form.Item label="申请主体"><Input value="启明医疗智能（演示机构）" disabled /></Form.Item>
              <Form.Item label="算法或分析说明" name="algorithm" required><Input /></Form.Item>
              <Form.Item label="使用期限" name="duration" required><Select options={[{ label: '30 天', value: '30 天' }, { label: '90 天', value: '90 天' }]} /></Form.Item>
              <Form.Item label="预期出域结果" name="output" required><Select options={[{ label: '聚合评估指标', value: '聚合评估指标' }, { label: '模型制品（需人工审查）', value: '模型制品（需人工审查）' }]} /></Form.Item>
              <Form.Item label="预计运行次数"><Input value="不超过 10 次" disabled /></Form.Item>
            </div>
            <Form.Item label="用途说明" required>
              <Input.TextArea rows={4} defaultValue="使用预登记模型进行外部验证，输出 AUC、置信区间和分层性能；不查看个体级结果，不进行二次使用。" />
            </Form.Item>
            <div className="attachment-row"><PaperClipOutlined /> 伦理与使用依据说明.pdf <Tag bordered={false}>演示附件</Tag></div>
            <div className="form-actions">
              <Button onClick={() => navigate(`/products/${product.id}`)}>返回产品</Button>
              <Button
                type="primary"
                onClick={() => {
                  setRole('ai')
                  setStage('application-submitted')
                }}
              >
                提交使用申请 <ArrowRightOutlined />
              </Button>
            </div>
          </Form>
        </Card>
      </div>
    )
  }

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="申请与审批编排"
        title="使用申请"
        description="平台预审与数据提供方审批彼此独立，每一次决定都进入审计链。"
        actions={!submitted ? <Button type="primary" onClick={() => navigate(`/products/${product.id}`)}>从数据产品发起申请</Button> : undefined}
      />
      {!submitted ? (
        <Card className="empty-state-card">
          <FileSearchOutlined />
          <h3>当前主演示链路还没有使用申请</h3>
          <p>先打开鼻咽癌数字病理数据产品，阅读使用策略后发起申请。</p>
          <Button type="primary" onClick={() => navigate(`/products/${product.id}`)}>前往数据产品</Button>
        </Card>
      ) : (
        <>
          <Card className="content-card application-summary">
            <div className="application-summary__head">
              <div><span className="mono-id">APP-2026-0718-001</span><h3>鼻咽癌复发风险模型外部验证</h3></div>
              <StatusPill value={approved ? '已批准' : '已提交'} />
            </div>
            <Descriptions column={3} size="small">
              <Descriptions.Item label="申请方">启明医疗智能（演示机构）</Descriptions.Item>
              <Descriptions.Item label="数据产品">鼻咽癌数字病理 v1.2</Descriptions.Item>
              <Descriptions.Item label="申请期限">90 天</Descriptions.Item>
              <Descriptions.Item label="算法">NPC-RiskNet v0.8（演示）</Descriptions.Item>
              <Descriptions.Item label="预期输出">聚合评估指标</Descriptions.Item>
              <Descriptions.Item label="原始数据导出">禁止</Descriptions.Item>
            </Descriptions>
          </Card>
          <Card className="content-card" title="审核进度">
            <Steps
              current={approved ? 3 : 1}
              items={[
                { title: '申请已提交', description: '主体、用途和输出已登记' },
                { title: '平台预审', description: approved ? '材料完整、规则兼容' : '演示预审已完成' },
                { title: '提供方审核', description: approved ? '医院方已批准' : '等待医院数据管理员决定' },
                { title: '创建数字合约', description: approved ? '可以进入合约协商' : '审批通过后解锁' },
              ]}
            />
            <div className="review-note">
              <SafetyCertificateOutlined />
              <div><strong>提供方附加策略</strong><p>禁止模型制品直接出域；仅聚合指标可在人工审查后发布。</p></div>
            </div>
            <div className="form-actions">
              {!approved ? (
                <Button
                  type="primary"
                  onClick={() => {
                    setRole('hospital')
                    setStage('application-approved')
                  }}
                >
                  以医院管理员身份审批通过（演示） <CheckCircleOutlined />
                </Button>
              ) : (
                <Space>
                  <Tag color="success" bordered={false}><CheckCircleOutlined /> 提供方审批完成</Tag>
                  <Button type="primary" onClick={() => navigate('/contracts')}>查看并签署数字合约 <ArrowRightOutlined /></Button>
                </Space>
              )}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
