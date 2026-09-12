import {
  CheckCircleFilled,
  ClockCircleOutlined,
  IdcardOutlined,
  LinkOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
  WalletOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Divider,
  Empty,
  Modal,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRoadshow } from './RoadshowContext'
import type { DemoIdentity } from './types'
import {
  executePreparedTransaction,
} from './web3Commerce'
import {
  bootstrapLocalDemoOperatorCredential,
  listCredentialReviewQueue,
  listWalletBindings,
  prepareCredentialIssue,
  synchronizeCredentialReceipt,
  WalletIdentityApiError,
  type WalletBindingStatus,
} from './web3Identity'
import {
  bindCurrentAccountWithInjectedWallet,
  signInWithInjectedWallet,
  type WalletCapabilities,
} from './web3Wallet'

const { Text, Title } = Typography

const roleLabels: Record<DemoIdentity, string> = {
  space_operator: '空间运营方',
  data_provider: '医院数据方',
  model_provider: '模型提供方',
  data_requester: '需求企业',
}

type Props = {
  open: boolean
  capabilities: WalletCapabilities | null
  onClose: () => void
}

function isAbortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === 'AbortError'
}

function messageOf(reason: unknown, fallback: string): string {
  return reason instanceof Error && reason.message.trim() ? reason.message : fallback
}

function compact(value: string, head = 14, tail = 8): string {
  if (value.length <= head + tail + 3) return value
  return `${value.slice(0, head)}…${value.slice(-tail)}`
}

function formatTime(value: string | null): string {
  if (!value) return '待签发'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function credentialIsCurrent(binding: WalletBindingStatus): boolean {
  if (binding.status !== 'active' || !binding.credential_expires_at) return false
  const expiry = new Date(binding.credential_expires_at).getTime()
  return Number.isFinite(expiry) && expiry > Date.now()
}

function statusTag(binding: WalletBindingStatus) {
  if (credentialIsCurrent(binding)) {
    return <Tag color="success" icon={<CheckCircleFilled />}>资格有效</Tag>
  }
  if (binding.status === 'active') return <Tag color="warning">凭证已过期</Tag>
  if (binding.status === 'pending') return <Tag color="processing" icon={<ClockCircleOutlined />}>待运营签发</Tag>
  return <Tag color="error">已撤销</Tag>
}

function IdentityCard({ binding }: { binding: WalletBindingStatus }) {
  const did = `did:pkh:eip155:${binding.chain_id}:${binding.wallet_address}`
  return <div className="phase55-wallet-card">
    <div className="phase55-wallet-card__head">
      <div>
        <Text type="secondary">当前平台角色</Text>
        <strong>{roleLabels[binding.platform_role as DemoIdentity] || binding.platform_role}</strong>
      </div>
      {statusTag(binding)}
    </div>
    <div className="phase55-wallet-facts">
      <div><WalletOutlined /><span><small>钱包地址</small><Text copyable={{ text: binding.wallet_address }}>{compact(binding.wallet_address)}</Text></span></div>
      <div><IdcardOutlined /><span><small>DID 摘要</small><Text copyable={{ text: did }}>{compact(did, 22, 8)}</Text></span></div>
      <div><SafetyCertificateOutlined /><span><small>链上凭证</small><Text>{binding.credential_token_id ? `Token #${compact(binding.credential_token_id, 10, 6)}` : '尚未签发'}</Text></span></div>
      <div><ClockCircleOutlined /><span><small>有效期</small><Text>{formatTime(binding.credential_expires_at)}</Text></span></div>
    </div>
  </div>
}

export function WalletQualificationPanel({ open, capabilities, onClose }: Props) {
  const { context, identity } = useRoadshow()
  const [bindings, setBindings] = useState<WalletBindingStatus[]>([])
  const [reviewQueue, setReviewQueue] = useState<WalletBindingStatus[]>([])
  const [loading, setLoading] = useState(false)
  const [operatorMode, setOperatorMode] = useState(false)
  const [busyKey, setBusyKey] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const activeBinding = useMemo(
    () => bindings.find(credentialIsCurrent) || null,
    [bindings],
  )
  const pendingOperatorBinding = useMemo(
    () => identity === 'space_operator'
      ? bindings.find((binding) => binding.status === 'pending') || null
      : null,
    [bindings, identity],
  )

  const loadPanel = useCallback(async (signal?: AbortSignal) => {
    if (!capabilities?.enabled) return
    setLoading(true)
    setError('')
    try {
      const own = await listWalletBindings(signal)
      setBindings(own)
      if (identity === 'space_operator' && own.some(credentialIsCurrent)) {
        try {
          const queue = await listCredentialReviewQueue(signal)
          setReviewQueue(queue)
          setOperatorMode(true)
        } catch (reason) {
          if (isAbortError(reason)) return
          if (reason instanceof WalletIdentityApiError && [401, 403].includes(reason.status)) {
            setReviewQueue([])
            setOperatorMode(false)
          } else {
            throw reason
          }
        }
      } else {
        setReviewQueue([])
        setOperatorMode(false)
      }
    } catch (reason) {
      if (!isAbortError(reason)) setError(messageOf(reason, '钱包资格加载失败。'))
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [capabilities?.enabled, identity])

  useEffect(() => {
    if (!open || !capabilities?.enabled) return
    const controller = new AbortController()
    void loadPanel(controller.signal)
    return () => controller.abort()
  }, [open, capabilities?.enabled, loadPanel])

  const bindWallet = async () => {
    if (!capabilities?.enabled || !context?.space_id) return
    setBusyKey('bind')
    setError('')
    setSuccess('')
    try {
      const result = await bindCurrentAccountWithInjectedWallet(
        context.space_id,
        capabilities.chain_id,
      )
      setSuccess(`钱包 ${compact(result.address)} 已提交，等待空间运营方签发资格凭证。`)
      await loadPanel()
    } catch (reason) {
      setError(messageOf(reason, '钱包绑定未完成。'))
    } finally {
      setBusyKey('')
    }
  }

  const enterOperatorMode = async () => {
    setBusyKey('operator')
    setError('')
    setSuccess('')
    try {
      const result = await signInWithInjectedWallet()
      const queue = await listCredentialReviewQueue()
      setReviewQueue(queue)
      setOperatorMode(true)
      setSuccess(`运营钱包 ${compact(result.address)} 已通过签名验证，可处理待签发资格。`)
    } catch (reason) {
      setError(messageOf(reason, '运营钱包验证未完成。'))
    } finally {
      setBusyKey('')
    }
  }

  const bootstrapOperator = async () => {
    if (!pendingOperatorBinding || !capabilities?.local_demo) return
    setBusyKey('bootstrap')
    setError('')
    setSuccess('')
    try {
      await bootstrapLocalDemoOperatorCredential(pendingOperatorBinding.binding_id)
      setSuccess('本地演示链的运营资格已初始化。请再用该钱包签名进入资格签发模式。')
      await loadPanel()
    } catch (reason) {
      setError(messageOf(reason, '本地运营资格初始化未完成。'))
    } finally {
      setBusyKey('')
    }
  }

  const issueCredential = async (binding: WalletBindingStatus) => {
    setBusyKey(binding.binding_id)
    setError('')
    setSuccess('')
    try {
      const prepared = await prepareCredentialIssue(binding.binding_id)
      if (
        prepared.binding_id !== binding.binding_id
        || prepared.chain_id !== binding.chain_id
        || prepared.holder_wallet.toLowerCase() !== binding.wallet_address.toLowerCase()
        || prepared.expected_event !== 'CredentialIssued'
      ) {
        throw new Error('服务端返回的资格签发计划与所选绑定不一致。')
      }
      const transaction = await executePreparedTransaction(
        prepared.transaction,
        {
          chain_id: prepared.chain_id,
          wallet_address: prepared.transaction.from || activeBinding?.wallet_address,
          expected_event: {
            contract_address: prepared.transaction.to,
            topic: prepared.expected_event_topic,
          },
        },
      )
      if (transaction.log_index === undefined) {
        throw new Error('链上回执缺少资格签发事件位置。')
      }
      const receipt = await synchronizeCredentialReceipt(
        binding.binding_id,
        transaction.transaction_hash,
        transaction.log_index,
      )
      if (receipt.applied) {
        setSuccess(`${roleLabels[binding.platform_role as DemoIdentity] || binding.platform_role}资格已签发并通过平台可信 RPC 核验。`)
      } else {
        setSuccess(`交易已提交，当前 ${receipt.confirmations}/${receipt.required_confirmations || '?'} 次确认；资格尚未激活。`)
      }
      await loadPanel()
    } catch (reason) {
      if (reason instanceof WalletIdentityApiError && [401, 403].includes(reason.status)) {
        setOperatorMode(false)
      }
      setError(messageOf(reason, '资格凭证签发未完成。'))
    } finally {
      setBusyKey('')
    }
  }

  return <Modal
    className="phase55-wallet-modal"
    open={open}
    onCancel={onClose}
    footer={null}
    width={720}
    title={null}
    destroyOnHidden
  >
    <div className="phase55-wallet-hero">
      <div className="phase55-wallet-hero__icon"><WalletOutlined /></div>
      <div>
        <Text type="secondary">PHASE 5.5 · WALLET IDENTITY</Text>
        <Title level={3}>钱包与平台资格</Title>
        <Text type="secondary">地址签名证明控制权，链上凭证承载已审核的角色范围。</Text>
      </div>
      {capabilities && <Tag color="cyan"><LinkOutlined /> {capabilities.chain_name} · {capabilities.chain_id}</Tag>}
    </div>

    {error && <Alert className="phase55-wallet-alert" type="error" showIcon closable onClose={() => setError('')} title="操作未完成" description={error} />}
    {success && <Alert className="phase55-wallet-alert" type="success" showIcon closable onClose={() => setSuccess('')} title="操作成功" description={success} />}

    <Spin spinning={loading}>
      <div className="phase55-wallet-section-head">
        <div><strong>我的资格</strong><Text type="secondary">由当前登录账号确定角色，不允许钱包自行选择。</Text></div>
        <Button type="text" size="small" icon={<ReloadOutlined />} onClick={() => void loadPanel()} disabled={Boolean(busyKey)}>刷新</Button>
      </div>

      {bindings.length ? <div className="phase55-wallet-binding-list">
        {bindings.map((binding) => <IdentityCard key={binding.binding_id} binding={binding} />)}
      </div> : <div className="phase55-wallet-empty">
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前账号尚未绑定钱包" />
        <Button
          type="primary"
          icon={<WalletOutlined />}
          loading={busyKey === 'bind'}
          disabled={!context?.space_id || !capabilities?.enabled}
          onClick={() => void bindWallet()}
        >连接钱包并签名绑定</Button>
        <Text type="secondary">平台会从当前账号读取机构与角色，钱包签名不会直接授予权限。</Text>
      </div>}

      {capabilities?.local_demo && pendingOperatorBinding && <div className="phase55-wallet-bootstrap">
        <ThunderboltOutlined />
        <div>
          <strong>首次初始化运营资格</strong>
          <Text type="secondary">仅用于一次性的本地 Hardhat 演示自举；生产环境必须走独立审核和受控签发。</Text>
        </div>
        <Button
          type="primary"
          loading={busyKey === 'bootstrap'}
          disabled={Boolean(busyKey) && busyKey !== 'bootstrap'}
          onClick={() => void bootstrapOperator()}
        >初始化运营资格（仅本地演示）</Button>
      </div>}

      {identity === 'space_operator' && <>
        <Divider />
        <div className="phase55-wallet-section-head">
          <div><strong>资格签发</strong><Text type="secondary">运营钱包发起交易，平台通过固定 RPC 复核事件后才激活资格。</Text></div>
          {operatorMode && <Tag color="success">签发模式已验证</Tag>}
        </div>

        {!activeBinding ? <Alert
          type="warning"
          showIcon
          title="运营钱包尚未具备有效资格"
          description="本地演示需先完成运营钱包初始化，随后才能为其他参与方签发凭证。"
        /> : !operatorMode ? <div className="phase55-wallet-operator-gate">
          <SafetyCertificateOutlined />
          <div><strong>需要运营钱包签名</strong><Text type="secondary">这是一次管理会话验证，不会发起链上交易或扣费。</Text></div>
          <Button type="primary" loading={busyKey === 'operator'} onClick={() => void enterOperatorMode()}>验证并查看待签发</Button>
        </div> : reviewQueue.length ? <div className="phase55-wallet-queue">
          {reviewQueue.map((binding) => <div className="phase55-wallet-queue__item" key={binding.binding_id}>
            <div className="phase55-wallet-queue__avatar"><IdcardOutlined /></div>
            <div className="phase55-wallet-queue__main">
              <Space size={8} wrap>
                <strong>{roleLabels[binding.platform_role as DemoIdentity] || binding.platform_role}</strong>
                {statusTag(binding)}
              </Space>
              <Text type="secondary" copyable={{ text: binding.wallet_address }}>{compact(binding.wallet_address, 18, 8)}</Text>
            </div>
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={busyKey === binding.binding_id}
              disabled={Boolean(busyKey) && busyKey !== binding.binding_id}
              onClick={() => void issueCredential(binding)}
            >签发链上资格</Button>
          </div>)}
        </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有待签发的钱包资格" />}
      </>}
    </Spin>

    <div className="phase55-wallet-boundary">
      <SafetyCertificateOutlined />
      <span><strong>资格边界：</strong>SBT 仅证明平台已完成指定机构与角色范围的审核，不替代法定 KYC、医疗资质核验或线下尽调。</span>
    </div>
  </Modal>
}
