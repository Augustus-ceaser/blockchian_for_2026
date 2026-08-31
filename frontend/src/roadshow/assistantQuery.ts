export type CountMetric = {
  label: string
  count: number
  unit: string
  detail?: string
}

export function isCountQuestion(query: string) {
  return /(?:有|共|一共|总共)?多少(?:个|项|条|份|例)?|几(?:个|项|条|份|例)|数量|总数|合计/.test(query)
}

export function isPublicCatalogQuestion(query: string) {
  return /公共|公开|候选/.test(query)
}

export function isPublishedProductQuestion(query: string) {
  return /已发布|已上架|上架/.test(query)
}

export function countFromPayload(payload: unknown): number | null {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null
  const record = payload as Record<string, unknown>
  if (typeof record.total === 'number' && Number.isFinite(record.total) && record.total >= 0) {
    return Math.trunc(record.total)
  }
  return Array.isArray(record.items) ? record.items.length : null
}

export function formatCountAnswer(metrics: CountMetric[]) {
  const summary = metrics.map((metric) => (
    `${metric.label} ${metric.count.toLocaleString('zh-CN')} ${metric.unit}${metric.detail ? `（${metric.detail}）` : ''}`
  )).join('；')
  return summary ? `已实时读取当前账号可见目录：${summary}。` : ''
}
