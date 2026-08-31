import type { ReactNode } from 'react'

export function MetricCard({
  label,
  value,
  detail,
  icon,
  tone = 'blue',
}: {
  label: string
  value: string | number
  detail: string
  icon: ReactNode
  tone?: 'blue' | 'teal' | 'amber' | 'purple'
}) {
  return (
    <div className="metric-card">
      <div className={`metric-card__icon metric-card__icon--${tone}`}>{icon}</div>
      <div>
        <div className="metric-card__label">{label}</div>
        <div className="metric-card__value">{value}</div>
        <div className="metric-card__detail">{detail}</div>
      </div>
    </div>
  )
}
