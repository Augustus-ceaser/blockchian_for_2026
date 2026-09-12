import { responseErrorMessage } from '../api/errorDetail'
import type { Web3PreparedTransaction } from './web3Commerce'

export type WalletBindingStatus = {
  binding_id: string
  status: 'pending' | 'active' | 'revoked'
  chain_id: number
  wallet_address: string
  platform_role: string
  credential_token_id: string | null
  credential_expires_at: string | null
}

export type CredentialIssuePreparation = WalletBindingStatus & {
  holder_wallet: string
  credential_scope_digest: string
  expected_event: 'CredentialIssued'
  expected_event_topic: string
  transaction: Web3PreparedTransaction & { from?: string }
  security_boundary: string
}

export type CredentialReceiptResult = WalletBindingStatus & {
  chain_event_receipt_id?: string
  receipt_status: string
  confirmations: number
  required_confirmations?: number
  applied: boolean
  idempotent_replay: boolean
}

export class WalletIdentityApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
  }
}

const configuredBase = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const baseUrl = configuredBase.replace(/\/$/, '')

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
    throw new WalletIdentityApiError(
      response.status,
      await responseErrorMessage(response, `${fallbackMessage}（${response.status}）`),
    )
  }
  return response.json() as Promise<T>
}

export function listWalletBindings(signal?: AbortSignal): Promise<WalletBindingStatus[]> {
  return requestJson<WalletBindingStatus[]>(
    '/auth/wallet/bindings',
    { signal },
    '钱包资格加载失败',
  )
}

export function listCredentialReviewQueue(signal?: AbortSignal): Promise<WalletBindingStatus[]> {
  return requestJson<WalletBindingStatus[]>(
    '/auth/wallet/bindings/review-queue',
    { signal },
    '待签发资格加载失败',
  )
}

export function prepareCredentialIssue(bindingId: string): Promise<CredentialIssuePreparation> {
  return requestJson<CredentialIssuePreparation>(
    `/auth/wallet/bindings/${encodeURIComponent(bindingId)}/credential/prepare`,
    { method: 'POST' },
    '资格凭证签发准备失败',
  )
}

export function bootstrapLocalDemoOperatorCredential(
  bindingId: string,
): Promise<WalletBindingStatus> {
  return requestJson<WalletBindingStatus>(
    `/auth/wallet/bindings/${encodeURIComponent(bindingId)}/credential/local-demo-bootstrap`,
    { method: 'POST' },
    '本地运营资格初始化失败',
  )
}

export function synchronizeCredentialReceipt(
  bindingId: string,
  transactionHash: string,
  logIndex: number,
): Promise<CredentialReceiptResult> {
  return requestJson<CredentialReceiptResult>(
    `/auth/wallet/bindings/${encodeURIComponent(bindingId)}/credential/receipts`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        transaction_hash: transactionHash,
        log_index: logIndex,
      }),
    },
    '资格凭证链上回执核验失败',
  )
}
