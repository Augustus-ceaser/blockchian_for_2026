import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CodeSandboxOutlined,
  EyeInvisibleOutlined,
  LockOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { Alert, Button, Card, Col, Descriptions, Progress, Row, Space, Statistic, Steps, Tag } from 'antd'
import { useNavigate } from 'react-router-dom'
import { PageHeading } from '../components/PageHeading'
import { StatusPill } from '../components/StatusPill'
import { hasReachedStage } from '../mock/data'
import { useDemo } from '../mock/DemoContext'

export function ComputePage() {
  const navigate = useNavigate()
  const { role, stage, setStage, setRole } = useDemo()
  const active = hasReachedStage(stage, 'contract-active')
  const running = hasReachedStage(stage, 'compute-running')
  const review = hasReachedStage(stage, 'output-review')
  const released = hasReachedStage(stage, 'result-released')
  const status = released ? '已发布' : review ? '待结果审查' : running ? '运行中' : '待启动'
  const currentStep = released ? 4 : review ? 3 : running ? 2 : active ? 1 : 0

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="受控任务编排演示"
        title="可信计算"
        description="原始数据不提供下载入口；任务必须通过合约策略校验，输出经审查后才能发布。"
        actions={<Tag color="orange" bordered={false}>模拟执行环境 · 非真实隐私计算</Tag>}
      />
      {!active ? (
        <Card className="empty-state-card">
          <LockOutlined />
          <h3>有效数字合约是任务准入条件</h3>
          <p>申请通过并完成双方签署后，平台才会允许创建受控计算任务。</p>
          <Button type="primary" onClick={() => navigate('/contracts')}>查看数字合约</Button>
        </Card>
      ) : (
        <>
          <Card className="content-card compute-hero">
            <div className="compute-hero__main">
              <div className="compute-hero__icon"><CodeSandboxOutlined /></div>
              <div>
                <span className="mono-id">JOB-NPC-0007</span>
                <h3>NPC-RiskNet v0.8 外部验证任务（演示）</h3>
                <p>关联合约 MTS-DC-2026-00018 · 数据产品 v1.2</p>
              </div>
            </div>
            <StatusPill value={status} />
          </Card>
          <Card className="content-card" title="策略执行与任务进度">
            <Steps
              current={currentStep}
              items={[
                { title: '合约校验', icon: <SafetyCertificateOutlined />, description: '主体、产品与策略匹配' },
                { title: '环境准备', icon: <LockOutlined />, description: '输入只读、外网禁用' },
                { title: '模拟运行', icon: <CodeSandboxOutlined />, description: running ? '内置任务执行器' : '等待启动' },
                { title: '结果审查', icon: <EyeInvisibleOutlined />, description: review ? '仅聚合指标待批准' : '运行后进入审查' },
                { title: '结果发布', icon: <CheckCircleOutlined />, description: released ? '已向申请方发布' : '审批后可见' },
              ]}
            />
            <div className="compute-controls">
              {!running && (
                <Button type="primary" onClick={() => setStage('compute-running')}>启动内置模拟任务</Button>
              )}
              {running && !review && (
                <Space>
                  <Progress percent={72} status="active" style={{ width: 240 }} />
                  <Button type="primary" onClick={() => setStage('output-review')}>模拟任务完成</Button>
                </Space>
              )}
              {review && !released && role.id !== 'hospital' && (
                <Button
                  type="primary"
                  onClick={() => setRole('hospital')}
                >
                  切换为医院管理员进入结果审查
                </Button>
              )}
              {review && !released && role.id === 'hospital' && (
                <Button type="primary" onClick={() => setStage('result-released')}>
                  批准聚合结果出域
                </Button>
              )}
              {released && <Button type="primary" onClick={() => navigate('/audit')}>查看完整审计链 <ArrowRightOutlined /></Button>}
            </div>
          </Card>
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={10}>
              <Card className="content-card" title="强制环境策略">
                <div className="control-list">
                  <div><CheckCircleOutlined /><span><strong>网络访问</strong>默认禁用外网访问</span></div>
                  <div><CheckCircleOutlined /><span><strong>数据挂载</strong>输入目录只读</span></div>
                  <div><CheckCircleOutlined /><span><strong>运行额度</strong>4 vCPU · 16 GB · 2 小时</span></div>
                  <div><CheckCircleOutlined /><span><strong>输出规则</strong>仅聚合指标进入审查区</span></div>
                </div>
                <Alert showIcon type="warning" message="以上策略仅为前端模拟" description="Phase 1 不创建容器、不执行上传代码，也不证明安全隔离能力。" />
              </Card>
            </Col>
            <Col xs={24} xl={14}>
              <Card className="content-card" title="结果制品与出域审查">
                {!review ? (
                  <div className="waiting-result"><ClockCircleOutlined /><span>任务完成后，结果先进入隔离审查区，不会自动提供下载。</span></div>
                ) : (
                  <>
                    <Descriptions column={2} size="small">
                      <Descriptions.Item label="制品标识">ART-AGG-0007</Descriptions.Item>
                      <Descriptions.Item label="制品类型">聚合评估报告</Descriptions.Item>
                      <Descriptions.Item label="审查状态">{released ? '已批准' : '等待医院方审查'}</Descriptions.Item>
                      <Descriptions.Item label="个体级记录">0 条</Descriptions.Item>
                    </Descriptions>
                    {(released || role.id === 'hospital') ? (
                      <div className="result-metrics">
                        <Statistic title="AUC（演示）" value={0.824} precision={3} />
                        <Statistic title="95% CI（演示）" value="0.791—0.857" />
                        <Statistic title="样本覆盖（演示）" value={1112} suffix="例" />
                      </div>
                    ) : (
                      <div className="restricted-result"><EyeInvisibleOutlined /> 待审聚合指标仅对数据提供方审查人员可见</div>
                    )}
                    <div className={`release-status ${released ? 'is-released' : ''}`}>
                      {released ? <CheckCircleOutlined /> : <EyeInvisibleOutlined />}
                      <span><strong>{released ? '聚合指标已批准发布' : '原始结果仍在审查区'}</strong>{released ? '使用方可以查看本页聚合指标，模型制品仍禁止出域。' : '使用方暂时不可查看或下载任何结果。'}</span>
                    </div>
                  </>
                )}
              </Card>
            </Col>
          </Row>
        </>
      )}
    </div>
  )
}
