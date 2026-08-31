type ApiErrorPayload = { detail?: unknown }

const validationFieldLabels: Record<string, string> = {
  project_lead: '项目负责人',
  contact: '联系方式或联系部门',
  ethics_or_approval_statement: '伦理或审批状态说明',
  output_recipient: '输出接收负责人',
}

function formatValidationMessage(message: string, context: unknown): string {
  if (message === 'Field required') return '未填写'
  if (message.startsWith('String should have at least')) {
    const minimum = context && typeof context === 'object' && 'min_length' in context
      ? Number((context as Record<string, unknown>).min_length)
      : 1
    return `至少填写 ${Number.isFinite(minimum) ? minimum : 1} 个字符`
  }
  return message
}

export function formatApiDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail.trim()
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item === 'string') return item.trim()
      if (!item || typeof item !== 'object') return ''
      const record = item as Record<string, unknown>
      const location = Array.isArray(record.loc)
        ? record.loc.filter((part) => part !== 'body').map(String)
        : []
      const field = location.length
        ? validationFieldLabels[location.at(-1) || ''] || location.join('.')
        : ''
      const message = typeof record.msg === 'string'
        ? formatValidationMessage(record.msg, record.ctx)
        : ''
      return field && message ? `${field}：${message}` : field || message
    }).filter(Boolean).join('；')
  }
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    if (typeof record.message === 'string') return record.message.trim()
  }
  return ''
}

export async function responseErrorMessage(response: Response, fallback: string): Promise<string> {
  const payload = await response.json().catch(() => null) as ApiErrorPayload | null
  return formatApiDetail(payload?.detail) || fallback
}
