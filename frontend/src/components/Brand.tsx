export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? 'brand--compact' : ''}`}>
      <div className="brand__mark" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      {!compact && (
        <div>
          <div className="brand__name">MedTrust Space</div>
          <div className="brand__caption">医疗可信数据空间</div>
        </div>
      )}
    </div>
  )
}
