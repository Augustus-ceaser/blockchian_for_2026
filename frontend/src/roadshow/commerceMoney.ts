export function formatCnyMinor(amountMinor: number): string {
  if (!Number.isSafeInteger(amountMinor)) return '¥--'
  return `¥${(amountMinor / 100).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

export function basisPointsAmount(amountMinor: number, rateBps: number): number {
  if (!Number.isSafeInteger(amountMinor) || !Number.isInteger(rateBps)) return 0
  return Math.floor((amountMinor * rateBps + 5000) / 10000)
}

export function simulatedChannelCostMinor(grossAmountMinor: number, explicitChannelFeeMinor?: number | null): number {
  return explicitChannelFeeMinor ?? basisPointsAmount(grossAmountMinor, 60)
}

