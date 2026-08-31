import { BankOutlined, CheckOutlined, RobotOutlined, SafetyCertificateOutlined, TeamOutlined } from '@ant-design/icons'
import { Alert, Button, Form, Input } from 'antd'
import type { InputRef } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Brand } from '../components/Brand'
import { roleProfiles, useRoadshow } from './RoadshowContext'
import type { DemoIdentity } from './types'

const icons: Record<DemoIdentity, React.ReactNode> = {
  space_operator: <SafetyCertificateOutlined />, data_provider: <BankOutlined />,
  model_provider: <RobotOutlined />, data_requester: <TeamOutlined />,
}

const usernames: Record<DemoIdentity, string> = {
  space_operator: 'operator.demo',
  data_provider: 'hospital.demo',
  model_provider: 'model.demo',
  data_requester: 'requester.demo',
}
const localDemoPassword = String(import.meta.env.VITE_MEDTRUST_DEMO_PASSWORD || '')

export function RoadshowLoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { identity, login } = useRoadshow()
  const [selected, setSelected] = useState(identity)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [form] = Form.useForm()
  const passwordInputRef = useRef<InputRef>(null)
  const roles = Object.entries(roleProfiles) as Array<[DemoIdentity, typeof roleProfiles[DemoIdentity]]>
  useEffect(() => {
    form.setFieldsValue({ username: usernames[selected], password: localDemoPassword })
  }, [form, selected])
  return <main className="login-page phase4-login">
    <section className="login-hero"><div className="login-hero__inner">
      <Brand />
      <div className="login-hero__eyebrow">MULTI-PARTY TRUSTED PATHOLOGY COLLABORATION</div>
      <h1>目录公开能力，<br />合约约束使用。</h1>
      <p>医院可开放受控计算或去标识化数据授权，模型企业可开放受控调用或模型使用许可。需求方从商城选择服务方式，平台编排申请、审批、合约与审计。</p>
      <div className="trust-principles">
        <div><SafetyCertificateOutlined /><span><strong>原始数据不直接交付</strong>仅允许受控计算，或经独立审批与合约约束的去标识化副本授权。</span></div>
        <div><SafetyCertificateOutlined /><span><strong>各方权责彼此独立</strong>申请方不能审批自己，模型确认不能替代医院的数据审批。</span></div>
        <div><SafetyCertificateOutlined /><span><strong>全流程可追溯</strong>关键操作与审核结果完整留痕。</span></div>
      </div>
    </div></section>
    <section className="login-panel"><div className="login-panel__inner">
      <h2>选择身份登录</h2>
      <p>请选择参与方，使用对应账号进入工作台。</p>
      <div className="role-grid">{roles.map(([key, profile]) => <button type="button" key={key} className={`role-card ${selected === key ? 'is-selected' : ''}`} onClick={() => {
        setSelected(key)
        window.requestAnimationFrame(() => passwordInputRef.current?.focus())
      }}>
        <span className="role-card__icon">{icons[key]}</span><span className="role-card__content"><strong>{profile.label}</strong><span>{profile.description}</span></span><span className="role-card__check"><CheckOutlined /></span>
      </button>)}</div>
      {error && <Alert type="error" showIcon title="登录失败" description={error} />}
      <Form
        form={form}
        layout="vertical"
        initialValues={{ username: usernames[selected], password: localDemoPassword }}
        onFinish={async ({ username, password }) => {
          setBusy(true); setError('')
          try {
            await login(username, password)
            const requested = (location.state as { from?: string } | null)?.from
            navigate(requested || '/overview', { replace: true })
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : '账号或密码无效')
          } finally {
            setBusy(false)
          }
        }}
      >
        <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
          <Input autoComplete="username" />
        </Form.Item>
        <Form.Item name="password" label="登录密码" rules={[{ required: true, min: localDemoPassword ? 3 : 12 }]}>
          <Input.Password
            ref={passwordInputRef}
            autoFocus
            autoComplete="current-password"
            onPressEnter={() => form.submit()}
          />
        </Form.Item>
        <Button type="primary" htmlType="submit" size="large" block loading={busy}>
          进入{roleProfiles[selected].shortLabel}门户
        </Button>
      </Form>
    </div></section>
  </main>
}
