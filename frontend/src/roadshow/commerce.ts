import { useEffect, useMemo, useState } from 'react'
import { responseErrorMessage } from '../api/errorDetail'
import type { DemoIdentity } from './types'
import type { ProductKind, ServiceMode } from './serviceAccess'
import { formatCnyMinor, simulatedChannelCostMinor } from './commerceMoney'

export { basisPointsAmount, formatCnyMinor } from './commerceMoney'

const configuredBase = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const baseUrl = configuredBase.replace(/\/$/, '')

export type CommercialOffer = {
  schema_version: string
  product_kind: ProductKind
  version_id: string
  service_mode: ServiceMode
  label: string
  currency: 'CNY'
  unit_amount_minor: number
  platform_fee_rate_bps?: number
  provider_share_rate_bps?: number
  includes_platform_fee?: boolean
  channel_fee_rate_bps?: number
  fulfillment_type: string
  delivery_boundary: string
  pricing_source?: string
}

export type CommercialOrderLine = {
  id: string
  line_no: number
  product_kind: ProductKind
  version_id: string
  product_name: string
  provider_organization_id: string
  provider_name?: string
  service_mode: ServiceMode
  unit_amount_minor: number
  gross_amount_minor: number
  platform_fee_minor?: number
  provider_net_minor?: number
  offer_snapshot: Record<string, unknown>
}

export type CommercialPayment = {
  method?: DemoPaymentMethod
  status: string
  transaction_number?: string
  amount_minor?: number
  channel_fee_minor?: number
  provider_settlement_amount_minor?: number
  paid_at: string
}

export type CommercialFulfillment = {
  id: string
  kind: 'data_document_package' | 'model_license_package' | 'execution_entitlement'
  status: string
  downloadable: boolean
  contract_id: string | null
  download_grant_status?: 'active' | 'consumed' | 'expired' | null
}

export type CommercialOrder = {
  id?: string
  order_id: string
  order_number: string
  source_type: 'service_access' | 'contract'
  source_id: string
  status: 'agreement_pending' | 'awaiting_payment' | 'paid' | string
  currency: 'CNY'
  gross_amount_minor?: number
  subtotal_amount_minor?: number
  platform_fee_minor?: number
  provider_net_minor?: number
  agreement: {
    digest: string | null
    snapshot: Record<string, unknown> | null
    accepted_at: string | null
  }
  lines: CommercialOrderLine[]
  payment: CommercialPayment | null
  fulfillments: CommercialFulfillment[]
  next_action: 'accept_agreement' | 'pay' | 'create_download_grant' | 'proceed_to_execution' | 'complete' | string
  allowed_actions: string[]
}

export type CommercialDownloadGrant = {
  grant_id: string
  token: string
  filename: string
  status: string
  expires_at: string
  max_downloads: number
  download_count: number
}

export type DemoPaymentMethod = 'wechat_demo' | 'alipay_demo' | 'bank_card_demo'

export type CommercialProviderSettlement = {
  provider_organization_id: string
  provider_name: string
  currency: 'CNY'
  gross_amount_minor: number
  platform_fee_minor?: number
  provider_net_minor: number
  paid_order_count: number
}

export type CommercialSettlementProjection = {
  items: CommercialProviderSettlement[]
  total: number
  summary: {
    currency: 'CNY'
    gross_amount_minor: number
    platform_fee_minor?: number
    provider_net_minor: number
    channel_fee_minor?: number
    real_funds_moved: false
  }
}

type CommercialOfferList = { items: CommercialOffer[]; total: number }
type CommercialOrderList = { items: CommercialOrder[]; total: number }

export class CommerceApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function decodeJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new CommerceApiError(
      response.status,
      await responseErrorMessage(response, `请求失败（${response.status}）`),
    )
  }
  return response.json() as Promise<T>
}

function commerceHeaders(_identity: DemoIdentity, idempotencyKey?: string): HeadersInit {
  return {
    'Content-Type': 'application/json',
    ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
  }
}

export async function commerceGet<T>(
  path: string,
  identity: DemoIdentity,
  signal?: AbortSignal,
): Promise<T> {
  return decodeJson<T>(await fetch(`${baseUrl}${path}`, {
    credentials: 'include',
    headers: commerceHeaders(identity),
    signal,
  }))
}

export async function commerceCommand<T>(
  path: string,
  identity: DemoIdentity,
  idempotencyKey: string,
  body?: unknown,
): Promise<T> {
  return decodeJson<T>(await fetch(`${baseUrl}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: commerceHeaders(identity, idempotencyKey),
    body: body === undefined ? undefined : JSON.stringify(body),
  }))
}

export async function getCommercialOffers(
  kind: ProductKind,
  versionId: string,
  identity: DemoIdentity,
  signal?: AbortSignal,
): Promise<CommercialOfferList> {
  return commerceGet(`/commercial-offers/version/${kind}/${versionId}`, identity, signal)
}

export async function listCommercialOrders(
  identity: DemoIdentity,
  signal?: AbortSignal,
): Promise<CommercialOrderList> {
  return commerceGet('/commercial-orders', identity, signal)
}

export async function getCommercialProviderSettlements(
  identity: DemoIdentity,
  signal?: AbortSignal,
): Promise<CommercialSettlementProjection> {
  return commerceGet('/commercial-provider-settlements', identity, signal)
}

export async function getCommercialOrder(
  orderId: string,
  identity: DemoIdentity,
  signal?: AbortSignal,
): Promise<CommercialOrder> {
  return commerceGet(`/commercial-orders/${orderId}`, identity, signal)
}

export async function createOrderFromServiceAccess(
  requestId: string,
  identity: DemoIdentity,
  idempotencyKey: string,
): Promise<CommercialOrder> {
  return commerceCommand(`/commercial-orders/from-service-access/${requestId}`, identity, idempotencyKey)
}

export async function createOrderFromContract(
  contractId: string,
  identity: DemoIdentity,
  idempotencyKey: string,
): Promise<CommercialOrder> {
  return commerceCommand(`/commercial-orders/from-contract/${contractId}`, identity, idempotencyKey)
}

export async function acceptCommercialAgreement(
  orderId: string,
  identity: DemoIdentity,
  idempotencyKey: string,
): Promise<CommercialOrder> {
  return commerceCommand(`/commercial-orders/${orderId}/accept-agreement`, identity, idempotencyKey)
}

export async function completeDemoPayment(
  orderId: string,
  method: DemoPaymentMethod,
  identity: DemoIdentity,
  idempotencyKey: string,
): Promise<CommercialOrder> {
  return commerceCommand(`/commercial-orders/${orderId}/pay`, identity, idempotencyKey, { method })
}

export async function createCommercialDownloadGrant(
  orderId: string,
  identity: DemoIdentity,
  idempotencyKey: string,
): Promise<CommercialDownloadGrant> {
  return commerceCommand(`/commercial-orders/${orderId}/download-grants`, identity, idempotencyKey)
}

export async function downloadCommercialPackage(
  token: string,
  identity: DemoIdentity,
  idempotencyKey: string,
): Promise<Blob> {
  const response = await fetch(`${baseUrl}/commercial-downloads`, {
    method: 'POST',
    credentials: 'include',
    headers: commerceHeaders(identity, idempotencyKey),
    body: JSON.stringify({ token }),
  })
  if (!response.ok) {
    throw new CommerceApiError(
      response.status,
      await responseErrorMessage(response, `下载失败（${response.status}）`),
    )
  }
  return response.blob()
}

export function channelCostMinor(order: CommercialOrder): number {
  return simulatedChannelCostMinor(order.gross_amount_minor ?? 0, order.payment?.channel_fee_minor)
}

export function platformNetAfterChannelMinor(order: CommercialOrder): number {
  return Math.max(0, (order.platform_fee_minor ?? 0) - channelCostMinor(order))
}

export function offerDisplayLabel(
  offer: Pick<CommercialOffer, 'product_kind' | 'service_mode' | 'unit_amount_minor'>,
): string {
  if (offer.product_kind === 'data'
    && offer.service_mode === 'deidentified_data_delivery'
    && offer.unit_amount_minor === 0) {
    return '公开数据授权交付（许可¥0）'
  }
  if (offer.service_mode === 'deidentified_data_delivery') return '匿名化数据授权交付'
  if (offer.service_mode === 'model_artifact_license') return '模型使用许可'
  return '受控调用计算'
}

const deliveryBoundaryLabels: Record<string, string> = {
  controlled_execution_only_no_raw_data_or_model_weights: '仅在受控环境完成本次计算，不交付原始数据或模型权重',
  public_manifest_and_authorization_documents_only: '交付公开数据清单、许可与授权文件',
  model_card_manifest_and_license_only_no_weights: '交付模型卡、清单与使用许可，不包含模型权重',
}

export function offerUnitLabel(offer: CommercialOffer): string {
  const candidate = (offer as CommercialOffer & { unit_label?: unknown }).unit_label
    ?? offer.delivery_boundary
  if (typeof candidate !== 'string' || !candidate.trim()) return '按当前固定服务范围'
  return deliveryBoundaryLabels[candidate] || candidate
}

export function orderLineUnitLabel(line: CommercialOrderLine): string {
  const value = line.offer_snapshot.unit_label
  return typeof value === 'string' && value.trim() ? value : '固定服务范围'
}

export function useCommercialOffers(
  productKind: ProductKind,
  versionId: string,
  identity: DemoIdentity,
) {
  const [items, setItems] = useState<CommercialOffer[]>([])
  const [loading, setLoading] = useState(Boolean(versionId))
  const [error, setError] = useState('')

  useEffect(() => {
    if (!versionId) {
      setItems([])
      setLoading(false)
      setError('')
      return
    }
    const controller = new AbortController()
    setLoading(true)
    setError('')
    getCommercialOffers(productKind, versionId, identity, controller.signal)
      .then((result) => setItems(result.items || []))
      .catch((reason) => {
        if ((reason as Error).name !== 'AbortError') {
          setError(reason instanceof Error ? reason.message : '报价未加载')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [identity, productKind, versionId])

  return useMemo(() => ({ items, loading, error }), [error, items, loading])
}
