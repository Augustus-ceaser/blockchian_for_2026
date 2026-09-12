import { responseErrorMessage } from '../api/errorDetail'

type Eip1193Request = {
  method: string
  params?: readonly unknown[] | Record<string, unknown>
}

type Eip1193Provider = {
  request: (request: Eip1193Request) => Promise<unknown>
}

type WalletChallenge = {
  challenge_id: string
  purpose: 'login' | 'bind'
  message: string
  expires_at: string
}

type WalletVerification = {
  authenticated: true
  auth_method: 'siwe'
}

export type WalletCapabilities = {
  enabled: boolean
  chain_id: number
  chain_name: string
  local_demo: boolean
  notice: string
}

export type WalletLoginResult = {
  address: string
  chainId: number
}

export type WalletBindingResult = WalletLoginResult & {
  bindingId: string
  status: string
}

const configuredBase = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const baseUrl = configuredBase.replace(/\/$/, '')

function injectedProvider(): Eip1193Provider {
  const provider = (window as Window & { ethereum?: Eip1193Provider }).ethereum
  if (!provider?.request) {
    throw new Error('未检测到浏览器钱包。请安装并解锁支持 EIP-1193 的钱包扩展。')
  }
  return provider
}

function walletError(reason: unknown): Error {
  if (reason && typeof reason === 'object') {
    const record = reason as { code?: unknown; message?: unknown }
    if (record.code === 4001) return new Error('你已取消钱包授权或签名。')
    if (record.code === -32002) return new Error('钱包中已有待处理请求，请先在钱包里完成。')
    if (typeof record.message === 'string' && record.message.trim()) {
      return new Error(record.message.trim())
    }
  }
  return new Error('钱包签名登录未完成。')
}

function parseAccounts(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error('钱包返回了无法识别的账户列表。')
  const accounts = value.filter((account): account is string => (
    typeof account === 'string' && /^0x[0-9a-fA-F]{40}$/.test(account)
  ))
  if (!accounts.length) throw new Error('钱包没有提供可用账户。')
  return accounts
}

function parseChainId(value: unknown): number {
  if (typeof value !== 'string' || !/^0x[0-9a-fA-F]+$/.test(value)) {
    throw new Error('钱包返回了无法识别的链 ID。')
  }
  const parsed = Number(BigInt(value))
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error('钱包链 ID 超出支持范围。')
  }
  return parsed
}

async function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, `钱包登录请求失败（${response.status}）`))
  }
  return response.json() as Promise<T>
}

export async function getWalletCapabilities(signal?: AbortSignal): Promise<WalletCapabilities> {
  const response = await fetch(`${baseUrl}/auth/wallet/capabilities`, {
    credentials: 'include',
    signal,
  })
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, `钱包能力探测失败（${response.status}）`))
  }
  const value = await response.json() as Partial<WalletCapabilities>
  if (
    typeof value.enabled !== 'boolean'
    || typeof value.chain_id !== 'number'
    || typeof value.chain_name !== 'string'
    || typeof value.local_demo !== 'boolean'
    || typeof value.notice !== 'string'
  ) {
    throw new Error('服务端返回了无法识别的钱包能力信息。')
  }
  return value as WalletCapabilities
}

function assertChallenge(
  value: WalletChallenge,
  expectedPurpose: WalletChallenge['purpose'],
): WalletChallenge {
  if (
    !value
    || typeof value.challenge_id !== 'string'
    || value.purpose !== expectedPurpose
    || typeof value.message !== 'string'
    || !value.message.trim()
    || typeof value.expires_at !== 'string'
  ) {
    throw new Error('服务端返回了无法识别的钱包登录挑战。')
  }
  return value
}

/**
 * Uses the injected wallet only to prove address control. Authorization and role
 * selection remain server-side through the pre-approved wallet binding.
 */
export async function signInWithInjectedWallet(
  onWalletConnected?: (wallet: WalletLoginResult) => void,
): Promise<WalletLoginResult> {
  const provider = injectedProvider()
  try {
    const address = parseAccounts(await provider.request({ method: 'eth_requestAccounts' }))[0]
    const chainId = parseChainId(await provider.request({ method: 'eth_chainId' }))
    onWalletConnected?.({ address, chainId })
    const challenge = assertChallenge(await postJson<WalletChallenge>('/auth/wallet/challenge', {
      chain_id: chainId,
      wallet_address: address,
    }), 'login')

    const signature = await provider.request({
      method: 'personal_sign',
      params: [challenge.message, address],
    })
    if (typeof signature !== 'string' || !/^0x[0-9a-fA-F]+$/.test(signature)) {
      throw new Error('钱包返回了无法识别的签名。')
    }

    const currentChainId = parseChainId(await provider.request({ method: 'eth_chainId' }))
    if (currentChainId !== chainId) {
      throw new Error('签名过程中钱包网络发生变化，请重新发起登录。')
    }

    const verification = await postJson<WalletVerification>('/auth/wallet/verify', {
      challenge_id: challenge.challenge_id,
      message: challenge.message,
      signature,
    })
    if (verification.authenticated !== true || verification.auth_method !== 'siwe') {
      throw new Error('服务端未确认钱包登录会话。')
    }
    return { address, chainId }
  } catch (reason) {
    throw walletError(reason)
  }
}

/**
 * Binds the injected wallet to the already-authenticated platform account.
 * The server derives the organization and role from the HttpOnly session; the
 * browser only supplies a proof that the user controls this address.
 */
export async function bindCurrentAccountWithInjectedWallet(
  spaceId: string,
  expectedChainId: number,
  onWalletConnected?: (wallet: WalletLoginResult) => void,
): Promise<WalletBindingResult> {
  if (!spaceId) throw new Error('协作空间信息尚未加载，请稍后重试。')
  const provider = injectedProvider()
  try {
    const address = parseAccounts(await provider.request({ method: 'eth_requestAccounts' }))[0]
    const chainId = parseChainId(await provider.request({ method: 'eth_chainId' }))
    onWalletConnected?.({ address, chainId })
    if (chainId !== expectedChainId) {
      throw new Error(`钱包当前网络为 ${chainId}，请切换到演示链 ${expectedChainId} 后重试。`)
    }

    const challenge = assertChallenge(await postJson<WalletChallenge>('/auth/wallet/bind/challenge', {
      space_id: spaceId,
      chain_id: chainId,
      wallet_address: address,
    }), 'bind')
    const signature = await provider.request({
      method: 'personal_sign',
      params: [challenge.message, address],
    })
    if (typeof signature !== 'string' || !/^0x[0-9a-fA-F]{130}$/.test(signature)) {
      throw new Error('钱包返回了无法识别的签名。')
    }
    const currentChainId = parseChainId(await provider.request({ method: 'eth_chainId' }))
    if (currentChainId !== chainId) {
      throw new Error('签名过程中钱包网络发生变化，请重新绑定。')
    }

    const binding = await postJson<{ binding_id: string; status: string }>('/auth/wallet/bind/verify', {
      challenge_id: challenge.challenge_id,
      message: challenge.message,
      signature,
    })
    if (typeof binding.binding_id !== 'string' || typeof binding.status !== 'string') {
      throw new Error('服务端返回了无法识别的钱包绑定结果。')
    }
    return {
      address,
      chainId,
      bindingId: binding.binding_id,
      status: binding.status,
    }
  } catch (reason) {
    throw walletError(reason)
  }
}
