import { responseErrorMessage } from '../api/errorDetail'
import type { DemoIdentity } from './types'

type Eip1193Request = {
  method: string
  params?: readonly unknown[] | Record<string, unknown>
}

type Eip1193Provider = {
  request: (request: Eip1193Request) => Promise<unknown>
}

type WalletReceipt = {
  transactionHash: string
  logs: unknown[]
}

export type WalletCapabilities = {
  enabled: boolean
  chain_id: number
  chain_name: string
  local_demo: boolean
  notice: string
}

export type Web3PreparedTransaction = {
  label: string
  to: string
  data: string
  value: string
}

export type Web3EscrowSnapshot = {
  escrow_binding_id: string
  order_id: string
  chain_id: number
  escrow_contract_address: string
  settlement_token_address: string
  escrow_order_key: string
  payer_wallet: string
  amount_token_units: string
  token_decimals: number
  status: 'prepared' | 'funding' | 'funded' | 'proving' | 'claimable' | 'refunded' | 'disputed' | 'orphaned'
  refund_eligible_at: string
  funding_tx_hash: string | null
  settlement_tx_hash: string | null
  distribution: Record<string, unknown>
}

export type Web3EscrowPreparation = Web3EscrowSnapshot & {
  transactions: Web3PreparedTransaction[]
  security_boundary: string
}

export type Web3SettlementProof = {
  proof_id: string
  proof_type: 'execution' | 'delivery'
  status: 'prepared' | 'submitted' | 'finalized' | 'orphaned'
  proof_digest: string
  transaction_hash: string | null
}

export type Web3ChainEvent = {
  event_name: 'EscrowFunded' | 'ExecutionAttested' | 'DeliveryAttested' | 'EscrowSettled'
  transaction_hash: string
  block_number: number
  confirmations: number
  status: 'observed' | 'finalized' | 'applied' | 'orphaned'
}

export type Web3EscrowStatusResponse = Web3EscrowSnapshot & {
  proofs: Web3SettlementProof[]
  events: Web3ChainEvent[]
}

export type Web3OrderEscrowLookup =
  | { available: false; order_id: string }
  | ({ available: true } & Web3EscrowSnapshot)

export type Web3LocalAutoSettlementResponse = Web3EscrowSnapshot & {
  automatic_settlement: 'completed' | 'already_completed'
  idempotent_replay: boolean
  compute_run_id?: string
  result_package_id?: string
  execution_transaction_hash?: string | null
  delivery_transaction_hash?: string
  settlement_chain_event_receipt_id?: string
  security_boundary?: string
}

export type Web3ReceiptSyncResponse = Web3EscrowSnapshot & {
  receipt_status: string
  confirmations: number
  required_confirmations?: number
  applied: boolean
  fulfillment_id?: string
  chain_event_receipt_id?: string
}

export type Web3FundingReference = {
  escrow_binding_id: string
  transaction_hash?: string
  log_index?: number
}

export type Web3FundingStage =
  | 'connecting_wallet'
  | 'approving_token'
  | 'waiting_for_approval'
  | 'opening_escrow'
  | 'waiting_for_funding'

export type Web3WalletFundingResult = {
  approval_transaction_hash: string
  transaction_hash: string
  log_index: number
}

export type PreparedTransactionExpectation = {
  chain_id: number
  wallet_address?: string
  expected_event?: {
    contract_address: string
    topic: string
  }
}

export type PreparedTransactionResult = {
  wallet_address: string
  chain_id: number
  transaction_hash: string
  log_index?: number
}

const configuredBase = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const baseUrl = configuredBase.replace(/\/$/, '')
const addressPattern = /^0x[0-9a-fA-F]{40}$/
const transactionHashPattern = /^0x[0-9a-fA-F]{64}$/
const eventTopicPattern = /^0x[0-9a-fA-F]{64}$/
const hexDataPattern = /^0x(?:[0-9a-fA-F]{2})*$/
const hexQuantityPattern = /^0x[0-9a-fA-F]+$/
export const WEB3_EVENT_TOPICS = {
  AgreementRegistered: '0x0b8e183ea38db41c28fee2ccae32aaa078ed7f8eb9b65c678caf95cb6daad60e',
  AgreementConfirmed: '0x73982bfca06dc575f3b259853877283e78acc5548f65c54a51a1dad20f4d15fa',
  AgreementFullyConfirmed: '0xfba0234770e8fc472ae7390357a4dcc6e4fb73024f523dc38d272e6dab552431',
  AgreementActivated: '0xde0c7ec1bbadfb28cfb64d45b81845de4a8b3dbd7346e07a9fe9166df5b9f6b4',
  EscrowFunded: '0xbf96dd82842c221dbd4985fc04f3da09da30badfc10151b6996644169f05bd4c',
  ExecutionAttested: '0x9fe62276c6c8f9fbf50db560f678f87dc5a6d1197e727bae3e8125eea46b5ae8',
  DeliveryAttested: '0xfad4eaff843f3c7debc54b2d856a28a4b35ccff3598fdc8efd0de5419a77e903',
  EscrowSettled: '0xcbd5b919c5b27c031a9a5a969839f37e2a5009b723faa4ae57164fc5f17187af',
} as const
const storagePrefix = 'medtrust:web3-commerce:'

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isAbortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === 'AbortError'
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

function assertCapabilities(value: WalletCapabilities): WalletCapabilities {
  if (
    !value
    || typeof value.enabled !== 'boolean'
    || !Number.isSafeInteger(value.chain_id)
    || value.chain_id <= 0
    || typeof value.chain_name !== 'string'
    || typeof value.local_demo !== 'boolean'
    || typeof value.notice !== 'string'
  ) {
    throw new Error('服务端返回了无法识别的钱包能力信息。')
  }
  return value
}

export async function getWalletCapabilities(signal?: AbortSignal): Promise<WalletCapabilities> {
  return assertCapabilities(await requestJson<WalletCapabilities>(
    '/auth/wallet/capabilities',
    { signal },
    '钱包能力检查失败',
  ))
}

export async function prepareWeb3Escrow(
  orderId: string,
  identity: DemoIdentity,
  orderStatus: string,
): Promise<Web3EscrowPreparation> {
  if (identity !== 'data_requester' || orderStatus !== 'awaiting_payment') {
    throw new Error('只有处于待付款状态的需求方订单可建立链上托管。')
  }
  if (!orderId) throw new Error('缺少商业订单编号。')
  return requestJson<Web3EscrowPreparation>(
    `/web3/commercial-orders/${encodeURIComponent(orderId)}/escrow/prepare`,
    { method: 'POST' },
    '链上托管准备失败',
  )
}

export async function getWeb3EscrowStatus(
  escrowBindingId: string,
  signal?: AbortSignal,
): Promise<Web3EscrowStatusResponse> {
  return requestJson<Web3EscrowStatusResponse>(
    `/web3/escrows/${encodeURIComponent(escrowBindingId)}`,
    { signal },
    '链上托管状态加载失败',
  )
}

export async function findWeb3EscrowForOrder(
  orderId: string,
  signal?: AbortSignal,
): Promise<Web3OrderEscrowLookup> {
  if (!orderId) throw new Error('缺少商业订单编号。')
  return requestJson<Web3OrderEscrowLookup>(
    `/web3/commercial-orders/${encodeURIComponent(orderId)}/escrow`,
    { signal },
    '链上托管记录查询失败',
  )
}

export async function autoSettleLocalDemoEscrow(
  escrowBindingId: string,
): Promise<Web3LocalAutoSettlementResponse> {
  if (!escrowBindingId) throw new Error('缺少链上托管记录编号。')
  return requestJson<Web3LocalAutoSettlementResponse>(
    `/web3/escrows/${encodeURIComponent(escrowBindingId)}/local-demo-auto-settle`,
    { method: 'POST' },
    '本地演示链自动结算失败',
  )
}

export async function synchronizeFundingReceipt(
  reference: Required<Web3FundingReference>,
): Promise<Web3ReceiptSyncResponse> {
  return requestJson<Web3ReceiptSyncResponse>(
    `/web3/escrows/${encodeURIComponent(reference.escrow_binding_id)}/receipts`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        event_name: 'EscrowFunded',
        transaction_hash: reference.transaction_hash,
        log_index: reference.log_index,
      }),
    },
    '可信链上回执核验失败',
  )
}

function storageKey(orderId: string): string {
  return `${storagePrefix}${orderId}`
}

export function loadWeb3FundingReference(orderId: string): Web3FundingReference | null {
  if (!orderId) return null
  try {
    const raw = window.sessionStorage.getItem(storageKey(orderId))
    if (!raw) return null
    const value: unknown = JSON.parse(raw)
    if (!isRecord(value) || typeof value.escrow_binding_id !== 'string') return null
    const transactionHash = value.transaction_hash
    const logIndex = value.log_index
    if (transactionHash === undefined && logIndex === undefined) {
      return { escrow_binding_id: value.escrow_binding_id }
    }
    if (typeof transactionHash !== 'string' || !transactionHashPattern.test(transactionHash)) {
      return null
    }
    if (logIndex === undefined) {
      return {
        escrow_binding_id: value.escrow_binding_id,
        transaction_hash: transactionHash,
      }
    }
    if (typeof logIndex !== 'number' || !Number.isSafeInteger(logIndex) || logIndex < 0) return null
    return {
      escrow_binding_id: value.escrow_binding_id,
      transaction_hash: transactionHash,
      log_index: logIndex,
    }
  } catch {
    return null
  }
}

export function saveWeb3FundingReference(orderId: string, reference: Web3FundingReference): void {
  if (!orderId || !reference.escrow_binding_id) return
  try {
    window.sessionStorage.setItem(storageKey(orderId), JSON.stringify(reference))
  } catch {
    // Storage is a convenience for page reloads; backend authorization remains authoritative.
  }
}

export function clearWeb3FundingReference(orderId: string): void {
  if (!orderId) return
  try {
    window.sessionStorage.removeItem(storageKey(orderId))
  } catch {
    // Recovery storage is best-effort and never an authorization source.
  }
}

function injectedProvider(): Eip1193Provider {
  const provider = (window as Window & { ethereum?: Eip1193Provider }).ethereum
  if (!provider?.request) {
    throw new Error('未检测到浏览器钱包。请安装并解锁支持 EIP-1193 的钱包扩展。')
  }
  return provider
}

function walletError(reason: unknown): Error {
  if (isAbortError(reason)) return reason
  if (isRecord(reason)) {
    if (reason.code === 4001) return new Error('你已在钱包中取消交易。')
    if (reason.code === -32002) return new Error('钱包中已有待处理请求，请先在钱包里完成。')
    if (typeof reason.message === 'string' && reason.message.trim()) {
      return new Error(reason.message.trim())
    }
  }
  return reason instanceof Error ? reason : new Error('钱包交易未完成。')
}

function parseAccounts(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error('钱包返回了无法识别的账户列表。')
  const accounts = value.filter((item): item is string => (
    typeof item === 'string' && addressPattern.test(item)
  ))
  if (!accounts.length) throw new Error('钱包没有提供可用账户。')
  return accounts
}

function parseChainId(value: unknown): number {
  if (typeof value !== 'string' || !hexQuantityPattern.test(value)) {
    throw new Error('钱包返回了无法识别的链 ID。')
  }
  const parsed = Number(BigInt(value))
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error('钱包链 ID 超出支持范围。')
  }
  return parsed
}

function parseHash(value: unknown, label: string): string {
  if (typeof value !== 'string' || !transactionHashPattern.test(value)) {
    throw new Error(`${label}返回了无法识别的交易哈希。`)
  }
  return value
}

function parseQuantity(value: unknown, label: string): number {
  if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return value
  if (typeof value !== 'string' || !hexQuantityPattern.test(value)) {
    throw new Error(`${label}不是有效的链上数值。`)
  }
  const parsed = Number(BigInt(value))
  if (!Number.isSafeInteger(parsed) || parsed < 0) {
    throw new Error(`${label}超出支持范围。`)
  }
  return parsed
}

function validatePreparedTransaction(
  value: Web3PreparedTransaction | undefined,
  expectedAddress: string,
  expectedSelector: string,
  label: string,
): Web3PreparedTransaction {
  const transaction = validateTransactionShape(value, label)
  if (
    transaction.to.toLowerCase() !== expectedAddress.toLowerCase()
    || !transaction.data.toLowerCase().startsWith(expectedSelector)
  ) {
    throw new Error(`服务端返回的${label}交易计划无效。`)
  }
  return transaction
}

function validateTransactionShape(
  value: Web3PreparedTransaction | undefined,
  label: string,
): Web3PreparedTransaction {
  if (
    !value
    || typeof value.label !== 'string'
    || typeof value.to !== 'string'
    || !addressPattern.test(value.to)
    || typeof value.data !== 'string'
    || !hexDataPattern.test(value.data)
    || typeof value.value !== 'string'
    || !hexQuantityPattern.test(value.value)
  ) {
    throw new Error(`服务端返回的${label}交易计划无效。`)
  }
  return value
}

async function sendTransaction(
  provider: Eip1193Provider,
  from: string,
  transaction: Web3PreparedTransaction,
): Promise<string> {
  return parseHash(await provider.request({
    method: 'eth_sendTransaction',
    params: [{
      from,
      to: transaction.to,
      data: transaction.data,
      value: transaction.value,
    }],
  }), transaction.label)
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

async function waitForSuccessfulReceipt(
  provider: Eip1193Provider,
  transactionHash: string,
): Promise<WalletReceipt> {
  const deadline = Date.now() + 180_000
  while (Date.now() < deadline) {
    const value = await provider.request({
      method: 'eth_getTransactionReceipt',
      params: [transactionHash],
    })
    if (value === null || value === undefined) {
      await delay(1_200)
      continue
    }
    if (!isRecord(value)) throw new Error('钱包返回了无法识别的交易回执。')
    const returnedHash = parseHash(value.transactionHash, '钱包回执')
    if (returnedHash.toLowerCase() !== transactionHash.toLowerCase()) {
      throw new Error('钱包回执与已发送交易不匹配。')
    }
    if (parseQuantity(value.status, '交易状态') !== 1) {
      throw new Error('链上交易执行失败，未继续后续托管步骤。')
    }
    if (!Array.isArray(value.logs)) throw new Error('钱包回执缺少日志列表。')
    return { transactionHash: returnedHash, logs: value.logs }
  }
  throw new Error('等待钱包交易确认超时，请稍后在链上状态中核对。')
}

function locateExpectedLogIndex(
  receipt: WalletReceipt,
  contractAddress: string,
  eventTopic: string,
): number {
  if (!addressPattern.test(contractAddress) || !eventTopicPattern.test(eventTopic)) {
    throw new Error('期望事件的合约地址或签名无效。')
  }
  const candidates: number[] = []
  for (const rawLog of receipt.logs) {
    if (!isRecord(rawLog) || typeof rawLog.address !== 'string' || !Array.isArray(rawLog.topics)) continue
    const firstTopic = rawLog.topics[0]
    if (
      rawLog.address.toLowerCase() !== contractAddress.toLowerCase()
      || typeof firstTopic !== 'string'
      || firstTopic.toLowerCase() !== eventTopic.toLowerCase()
    ) {
      continue
    }
    candidates.push(parseQuantity(rawLog.logIndex, '事件日志索引'))
  }
  if (candidates.length !== 1) {
    throw new Error('钱包回执中未找到唯一的预期事件位置。')
  }
  // This index is only an untrusted locator. The backend refetches and validates
  // the canonical receipt, contract, event signature, confirmations and payload.
  return candidates[0]
}

/**
 * Sends one backend-prepared EIP-1193 transaction and waits for a successful
 * wallet receipt. When an event is expected, the returned log index is only an
 * untrusted locator; callers must submit it to a backend that refetches and
 * validates the canonical receipt through its configured RPC.
 */
export async function executePreparedTransaction(
  transactionValue: Web3PreparedTransaction,
  expectation: PreparedTransactionExpectation,
  onSubmitted?: (transactionHash: string) => void,
): Promise<PreparedTransactionResult> {
  try {
    const transaction = validateTransactionShape(transactionValue, transactionValue?.label || '链上')
    if (!Number.isSafeInteger(expectation.chain_id) || expectation.chain_id <= 0) {
      throw new Error('期望链 ID 无效。')
    }
    if (expectation.wallet_address && !addressPattern.test(expectation.wallet_address)) {
      throw new Error('期望的钱包地址无效。')
    }
    const provider = injectedProvider()
    const walletAddress = parseAccounts(await provider.request({ method: 'eth_requestAccounts' }))[0]
    const chainId = parseChainId(await provider.request({ method: 'eth_chainId' }))
    if (chainId !== expectation.chain_id) {
      throw new Error(`钱包当前网络为 ${chainId}，请切换到演示链 ${expectation.chain_id} 后重试。`)
    }
    if (
      expectation.wallet_address
      && walletAddress.toLowerCase() !== expectation.wallet_address.toLowerCase()
    ) {
      throw new Error('当前钱包账户不是服务端已核验的钱包。')
    }
    const transactionHash = await sendTransaction(provider, walletAddress, transaction)
    onSubmitted?.(transactionHash)
    const receipt = await waitForSuccessfulReceipt(provider, transactionHash)
    const logIndex = expectation.expected_event
      ? locateExpectedLogIndex(
        receipt,
        expectation.expected_event.contract_address,
        expectation.expected_event.topic,
      )
      : undefined
    return {
      wallet_address: walletAddress,
      chain_id: chainId,
      transaction_hash: transactionHash,
      ...(logIndex === undefined ? {} : { log_index: logIndex }),
    }
  } catch (reason) {
    throw walletError(reason)
  }
}

export async function executePreparedWeb3Funding(
  preparation: Web3EscrowPreparation,
  onStage?: (stage: Web3FundingStage) => void,
  onFundingSubmitted?: (transactionHash: string) => void,
): Promise<Web3WalletFundingResult> {
  try {
    if (!addressPattern.test(preparation.payer_wallet)) {
      throw new Error('服务端返回的付款钱包地址无效。')
    }
    if (!addressPattern.test(preparation.settlement_token_address)) {
      throw new Error('服务端返回的演示结算币地址无效。')
    }
    if (!addressPattern.test(preparation.escrow_contract_address)) {
      throw new Error('服务端返回的托管合约地址无效。')
    }
    if (!Array.isArray(preparation.transactions) || preparation.transactions.length !== 2) {
      throw new Error('服务端必须返回授权与建立托管两笔交易。')
    }
    const approval = validatePreparedTransaction(
      preparation.transactions[0],
      preparation.settlement_token_address,
      '0x095ea7b3',
      '授权',
    )
    const opening = validatePreparedTransaction(
      preparation.transactions[1],
      preparation.escrow_contract_address,
      '0x9cd3d8d6',
      '建立托管',
    )

    onStage?.('connecting_wallet')
    onStage?.('approving_token')
    const approvalResult = await executePreparedTransaction(
      approval,
      {
        chain_id: preparation.chain_id,
        wallet_address: preparation.payer_wallet,
      },
      () => onStage?.('waiting_for_approval'),
    )

    onStage?.('opening_escrow')
    const fundingResult = await executePreparedTransaction(
      opening,
      {
        chain_id: preparation.chain_id,
        wallet_address: preparation.payer_wallet,
        expected_event: {
          contract_address: preparation.escrow_contract_address,
          topic: WEB3_EVENT_TOPICS.EscrowFunded,
        },
      },
      (transactionHash) => {
        onFundingSubmitted?.(transactionHash)
        onStage?.('waiting_for_funding')
      },
    )
    if (fundingResult.log_index === undefined) {
      throw new Error('钱包回执缺少托管付款事件位置。')
    }

    return {
      approval_transaction_hash: approvalResult.transaction_hash,
      transaction_hash: fundingResult.transaction_hash,
      log_index: fundingResult.log_index,
    }
  } catch (reason) {
    throw walletError(reason)
  }
}

export async function recoverSubmittedWeb3Funding(
  preparation: Web3EscrowPreparation,
  transactionHash: string,
  onStage?: (stage: Web3FundingStage) => void,
): Promise<Pick<Web3WalletFundingResult, 'transaction_hash' | 'log_index'>> {
  if (!transactionHashPattern.test(transactionHash)) {
    throw new Error('已保存的托管交易哈希无效。')
  }
  if (!addressPattern.test(preparation.escrow_contract_address)) {
    throw new Error('服务端返回的托管合约地址无效。')
  }
  const provider = injectedProvider()
  const chainId = parseChainId(await provider.request({ method: 'eth_chainId' }))
  if (chainId !== preparation.chain_id) {
    throw new Error(`钱包当前网络为 ${chainId}，请切换到演示链 ${preparation.chain_id} 后重试。`)
  }
  onStage?.('waiting_for_funding')
  const receipt = await waitForSuccessfulReceipt(provider, transactionHash)
  return {
    transaction_hash: transactionHash,
    log_index: locateExpectedLogIndex(
      receipt,
      preparation.escrow_contract_address,
      WEB3_EVENT_TOPICS.EscrowFunded,
    ),
  }
}
