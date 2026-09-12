import { BankOutlined, CheckOutlined, RobotOutlined, SafetyCertificateOutlined, TeamOutlined, WalletOutlined } from '@ant-design/icons'
import { Alert, Button, Form, Input, Tag } from 'antd'
import type { InputRef } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Brand } from '../components/Brand'
import { roleProfiles, useRoadshow } from './RoadshowContext'
import { getWalletCapabilities, signInWithInjectedWallet, type WalletCapabilities } from './web3Wallet'
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
  const [walletBusy, setWalletBusy] = useState(false)
  const [walletError, setWalletError] = useState('')
  const [walletAddress, setWalletAddress] = useState('')
  const [walletChainId, setWalletChainId] = useState<number | null>(null)
  const [walletCapabilities, setWalletCapabilities] = useState<WalletCapabilities | null>(null)
  const [form] = Form.useForm()
  const passwordInputRef = useRef<InputRef>(null)
  const roles = Object.entries(roleProfiles) as Array<[DemoIdentity, typeof roleProfiles[DemoIdentity]]>
  useEffect(() => {
    form.setFieldsValue({ username: usernames[selected], password: localDemoPassword })
  }, [form, selected])
  useEffect(() => {
    const controller = new AbortController()
    getWalletCapabilities(controller.signal)
      .then(setWalletCapabilities)
      .catch(() => setWalletCapabilities(null))
    return () => controller.abort()
  }, [])
  const destination = (() => {
    const requested = (location.state as { from?: string } | null)?.from
    return requested?.startsWith('/') && !requested.startsWith('//') ? requested : '/overview'
  })()
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
            navigate(destination, { replace: true })
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
        <Button type="primary" htmlType="submit" size="large" block loading={busy} disabled={walletBusy}>
          进入{roleProfiles[selected].shortLabel}门户
        </Button>
      </Form>
      {walletCapabilities?.enabled && <div style={{ marginTop: 18, padding: 16, border: '1px solid #d7e4ec', borderRadius: 12, background: '#f8fbfd' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 8 }}>
          <strong>钱包签名登录</strong>
          <Tag color="cyan" style={{ marginInlineEnd: 0 }}>{walletCapabilities.chain_name}</Tag>
        </div>
        <div style={{ color: '#637888', fontSize: 13, lineHeight: 1.65, marginBottom: 12 }}>
          钱包只证明地址控制权；身份和角色由服务端已审核绑定决定，与上方角色选择无关。
        </div>
        {walletError && <Alert type="error" showIcon title="钱包登录失败" description={walletError} style={{ marginBottom: 12 }} />}
        {(walletAddress || walletChainId !== null) && <div style={{ marginBottom: 12, padding: '9px 11px', borderRadius: 8, background: '#eef5f8', color: '#415969', fontSize: 12, lineHeight: 1.6 }}>
          {walletChainId !== null && <div>Chain ID：{walletChainId}</div>}
          {walletAddress && <div style={{ wordBreak: 'break-all' }}>地址：{walletAddress}</div>}
        </div>}
        <Button
          icon={<WalletOutlined />}
          block
          size="large"
          loading={walletBusy}
          disabled={busy}
          onClick={async () => {
            setWalletBusy(true)
            setWalletError('')
            setError('')
            try {
              await signInWithInjectedWallet((wallet) => {
                setWalletAddress(wallet.address)
                setWalletChainId(wallet.chainId)
              })
              // A full navigation lets RoadshowProvider bootstrap from the new
              // HttpOnly session without exposing the session secret to JS.
              window.location.replace(destination)
            } catch (reason) {
              setWalletError(reason instanceof Error ? reason.message : '钱包签名登录未完成。')
            } finally {
              setWalletBusy(false)
            }
          }}
        >
          连接钱包并签名
        </Button>
        <div style={{ marginTop: 9, color: '#8798a4', fontSize: 12, lineHeight: 1.55 }}>
          {walletCapabilities.notice}
        </div>
      </div>}
    </div></section>
  </main>
}
