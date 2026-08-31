import {
  ArrowRightOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import { Alert, Button, Typography } from 'antd'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { platformGet } from './api'
import { useRoadshow } from './RoadshowContext'

type SealState = {
  counts: Record<string, number>
  status_counts: Record<string, number>
}

function ResourceCard({ icon, eyebrow, title, value, description, onClick }: {
  icon: ReactNode
  eyebrow: string
  title: string
  value: number
  description: string
  onClick: () => void
}) {
  return <button type="button" className="roadshow-seal-resource" onClick={onClick}>
    <span className="roadshow-seal-resource__icon">{icon}</span>
    <span className="roadshow-seal-resource__body">
      <small>{eyebrow}</small>
      <strong>{title}</strong>
      <span>{description}</span>
    </span>
    <span className="roadshow-seal-resource__value">{value}</span>
    <ArrowRightOutlined />
  </button>
}

export function RoadshowSealPage() {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const [state, setState] = useState<SealState | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    platformGet<SealState>('/roadshow-seal/overview', identity, controller.signal)
      .then(setState)
      .catch((reason: Error) => {
        if (reason.name !== 'AbortError') setError(reason.message)
      })
    return () => controller.abort()
  }, [identity])

  if (error) return <Alert type="error" showIcon title="总览读取失败" description={error} />
  if (!state) return <div className="roadshow-seal-loading">正在加载空间资源…</div>

  const dataPath = identity === 'space_operator' ? '/portal/operator/external-catalog' : '/external-catalog/datasets'
  const modelPath = identity === 'space_operator' ? '/portal/operator/external-model-catalog' : '/external-catalog/models'
  const openAssistant = () => window.dispatchEvent(new Event('medtrust:open-assistant'))

  return <div className="page-stack roadshow-seal-page">
    <section className="roadshow-seal-hero">
      <div className="roadshow-seal-hero__content">
        <span className="roadshow-seal-eyebrow">MEDTRUST INTELLIGENT COLLABORATION</span>
        <Typography.Title level={1}>一句话提出任务，<br />平台组织数据、模型与流程。</Typography.Title>
        <Typography.Paragraph>
          智能助手理解任务，并从当前账号可见的真实资源中定位数据、模型与协作记录。
        </Typography.Paragraph>
        <div className="roadshow-seal-hero__actions">
          <Button type="primary" size="large" icon={<FileSearchOutlined />} onClick={openAssistant}>打开智能助手</Button>
          <Button size="large" onClick={() => navigate(dataPath)}>浏览数据资源</Button>
        </div>
      </div>
      <div className="roadshow-seal-orbit" aria-hidden="true">
        <span /><span /><span />
        <div><DatabaseOutlined /><RobotOutlined /><FileSearchOutlined /></div>
      </div>
    </section>

    <section className="roadshow-seal-resources" aria-label="平台资源入口">
      <ResourceCard
        icon={<DatabaseOutlined />}
        eyebrow="DATA"
        title="数据资源"
        value={state.counts.external_dataset_records || 0}
        description={`${state.status_counts.published_external_data_products || 0} 个已发布公共数据产品`}
        onClick={() => navigate(dataPath)}
      />
      <ResourceCard
        icon={<RobotOutlined />}
        eyebrow="MODEL"
        title="模型资源"
        value={state.counts.external_model_records || 0}
        description={`${state.status_counts.published_external_model_products || 0} 个已发布公共模型产品`}
        onClick={() => navigate(modelPath)}
      />
      <ResourceCard
        icon={<FileSearchOutlined />}
        eyebrow="WORKFLOW"
        title="协作需求"
        value={state.counts.applications || 0}
        description="查看需求、审批、执行与结果"
        onClick={() => navigate('/applications')}
      />
    </section>

    <section className="roadshow-seal-flow" aria-label="可信协作流程">
      <div><span>01</span><strong>发现资源</strong><small>数据与模型目录</small></div>
      <ArrowRightOutlined />
      <div><span>02</span><strong>提出需求</strong><small>自然语言任务入口</small></div>
      <ArrowRightOutlined />
      <div><span>03</span><strong>多方审批</strong><small>规则、合约与责任确认</small></div>
      <ArrowRightOutlined />
      <div><span>04</span><strong>结果交付</strong><small>审核后的结果包</small></div>
    </section>
  </div>
}
