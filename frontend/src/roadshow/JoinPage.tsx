import { BankOutlined, LinkOutlined, RobotOutlined, SafetyCertificateOutlined, TeamOutlined } from '@ant-design/icons'
import { Button, Result, Spin } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { useNavigate } from 'react-router-dom'
import { deploymentStatus, type DeploymentStatus } from './api'

const portals = [
  { path: '/portal/hospital', label: '医院数据端', icon: <BankOutlined /> },
  { path: '/portal/model-provider', label: '模型提供方端', icon: <RobotOutlined /> },
  { path: '/portal/requester', label: '需求企业端', icon: <TeamOutlined /> },
  { path: '/portal/operator', label: '平台运营端', icon: <SafetyCertificateOutlined /> },
]

export function JoinPage() {
  const navigate = useNavigate()
  const [status, setStatus] = useState<DeploymentStatus | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    deploymentStatus(controller.signal).then(setStatus).catch((reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取部署状态')
    })
    return () => controller.abort()
  }, [])
  const origin = useMemo(() => status?.public_origin || window.location.origin, [status])
  if (error) return <Result status="error" title="无法读取访问入口" subTitle={error} />
  if (!status) return <main className="join-page"><Spin size="large" /></main>
  if (!status.join_enabled) {
    return <Result status="403" title="局域网入口未启用" subTitle="当前未开放局域网访问。" extra={<Button onClick={() => navigate('/demo-login')}>返回登录</Button>} />
  }
  return <main className="join-page">
    <header className="join-page__header">
      <div>
        <h1>MedTrust Space 局域网访问入口</h1>
      </div>
      <div className="join-page__health"><span className="status-dot" /> 统一入口已连接</div>
    </header>
    <section className="join-page__grid">
      {portals.map((portal) => {
        const url = `${origin}${portal.path}`
        return <article className="join-portal" key={portal.path}>
          <div className="join-portal__title">{portal.icon}<strong>{portal.label}</strong></div>
          <QRCodeSVG value={url} size={168} level="M" />
          <code>{url}</code>
          <Button icon={<LinkOutlined />} type="primary" onClick={() => navigate(portal.path)}>打开门户</Button>
        </article>
      })}
    </section>
  </main>
}

