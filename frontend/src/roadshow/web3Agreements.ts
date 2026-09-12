import { responseErrorMessage } from '../api/errorDetail'
import {
  WEB3_EVENT_TOPICS,
  type Web3PreparedTransaction,
} from './web3Commerce'

export type AgreementEventName =
  | 'AgreementRegistered'
  | 'AgreementConfirmed'
  | 'AgreementActivated'

export type Web3AgreementAnchor = {
  anchored: true
  chain_anchor_id: string
  contract_id: string
  contract_revision_id: string
  chain_id: number
  registry_address: string
  agreement_key: string
  content_digest: string
  status: 'prepared' | 'registered' | 'confirming' | 'active' | 'suspended' | 'ended' | 'orphaned'
  confirmation_bitmap: number
  required_confirmation_bitmap: number
  confirmation_progress: { completed: number; required: number }
  registration_tx_hash: string | null
  activation_tx_hash: string | null
}

export type Web3AgreementState = Web3AgreementAnchor | {
  anchored: false
  contract_revision_id: string
  status: 'not_prepared'
}

export type Web3AgreementCall = {
  method: 'registerAgreement' | 'confirm' | 'activate'
  expected_event: AgreementEventName
  needs_submission: boolean
  transaction: Web3PreparedTransaction & { from?: string }
}

export type Web3AgreementPreparation = Web3AgreementAnchor & {
  call: Web3AgreementCall
  participant_wallets?: Record<string, string>
  party_role?: string
  expected_confirmation_bitmap?: number
  security_boundary?: string
}

export type Web3AgreementReceiptResult = Web3AgreementAnchor & {
  receipt_status: string
  confirmations: number
  required_confirmations?: number
  applied: boolean
  idempotent_replay?: boolean
}

const configuredBase = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const baseUrl = configuredBase.replace(/\/$/, '')
const addressPattern = /^0x[0-9a-fA-F]{40}$/
const transactionHashPattern = /^0x[0-9a-fA-F]{64}$/
const hexQuantityPattern = /^0x[0-9a-fA-F]+$/

type Eip1193Provider = {
  request: (request: { method: string; params?: readonly unknown[] }) => Promise<unknown>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

async function requestJson<T>(
  path: string,
  init: RequestInit,
  fallbackMessage: string,
): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    credentials: 'include',
    ...init,
  })
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, `${fallbackMessage}（${response.status}）`))
  }
  return response.json() as Promise<T>
}

export function getWeb3Agreement(
  contractRevisionId: string,
  signal?: AbortSignal,
): Promise<Web3AgreementState> {
  return requestJson<Web3AgreementState>(
    `/web3/contract-revisions/${encodeURIComponent(contractRevisionId)}/agreement`,
    { signal },
    '链上合约状态读取失败',
  )
}

export function prepareWeb3Agreement(
  contractRevisionId: string,
): Promise<Web3AgreementPreparation> {
  return requestJson<Web3AgreementPreparation>(
    `/web3/contract-revisions/${encodeURIComponent(contractRevisionId)}/agreement/prepare`,
    { method: 'POST' },
    '链上合约登记准备失败',
  )
}

export function prepareWeb3AgreementConfirmation(
  chainAnchorId: string,
): Promise<Web3AgreementPreparation> {
  return requestJson<Web3AgreementPreparation>(
    `/web3/agreement-anchors/${encodeURIComponent(chainAnchorId)}/confirmation/prepare`,
    { method: 'POST' },
    '链上确认准备失败',
  )
}

export function prepareWeb3AgreementActivation(
  chainAnchorId: string,
): Promise<Web3AgreementPreparation> {
  return requestJson<Web3AgreementPreparation>(
    `/web3/agreement-anchors/${encodeURIComponent(chainAnchorId)}/activation/prepare`,
    { method: 'POST' },
    '链上生效准备失败',
  )
}

export function synchronizeWeb3AgreementReceipt(
  chainAnchorId: string,
  eventName: AgreementEventName,
  transactionHash: string,
  logIndex: number,
): Promise<Web3AgreementReceiptResult> {
  return requestJson<Web3AgreementReceiptResult>(
    `/web3/agreement-anchors/${encodeURIComponent(chainAnchorId)}/receipts`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        event_name: eventName,
        transaction_hash: transactionHash,
        log_index: logIndex,
      }),
    },
    '可信链上回执核验失败',
  )
}

function parseLogIndex(value: unknown): number | null {
  if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return value
  if (typeof value !== 'string' || !hexQuantityPattern.test(value)) return null
  const parsed = Number(BigInt(value))
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : null
}

/**
 * Finds the optional activation event emitted by the fourth confirmation.
 * This browser receipt is only an untrusted locator: the backend refetches the
 * canonical receipt and validates its contract, topic, payload and finality.
 */
export async function findAutomaticActivationLogIndex(
  transactionHash: string,
  registryAddress: string,
): Promise<number | null> {
  if (!transactionHashPattern.test(transactionHash) || !addressPattern.test(registryAddress)) {
    throw new Error('无法识别待核验的链上交易。')
  }
  const provider = (window as Window & { ethereum?: Eip1193Provider }).ethereum
  if (!provider?.request) throw new Error('未检测到浏览器钱包。')
  const receipt = await provider.request({
    method: 'eth_getTransactionReceipt',
    params: [transactionHash],
  })
  if (!isRecord(receipt) || !Array.isArray(receipt.logs)) {
    throw new Error('钱包未返回可定位的交易日志。')
  }
  const candidates = receipt.logs.flatMap((rawLog) => {
    if (!isRecord(rawLog) || typeof rawLog.address !== 'string' || !Array.isArray(rawLog.topics)) return []
    if (
      rawLog.address.toLowerCase() !== registryAddress.toLowerCase()
      || typeof rawLog.topics[0] !== 'string'
      || rawLog.topics[0].toLowerCase() !== WEB3_EVENT_TOPICS.AgreementActivated.toLowerCase()
    ) {
      return []
    }
    const index = parseLogIndex(rawLog.logIndex)
    return index === null ? [] : [index]
  })
  if (candidates.length > 1) {
    throw new Error('同一交易出现多个合约生效事件，已停止自动同步。')
  }
  return candidates[0] ?? null
}
