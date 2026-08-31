export type CapabilityBoundary = {
  demo: boolean
  simulated: boolean
  hard_isolation: boolean
  clinical_use: boolean
  artifact_download_enabled: boolean
}

export type CollectionResponse<T> = {
  items: T[]
  total: number
  capability: CapabilityBoundary
}

export type OverviewResponse = {
  space_id: string
  counts: Record<string, number>
  latest_run: Record<string, unknown> | null
  latest_artifact: Record<string, unknown> | null
  verified_baseline_metrics: {
    source: string
    sample_count: number
    accuracy: string
    mean_confidence: string
    artifact_status: string
  }
  outbox: { total: number; published: number }
  inbox: {
    consumer_total: number
    consumer_completed: number
    callback_total: number
    callback_completed: number
  }
  audit_chain_valid: boolean
  capability: CapabilityBoundary
}

export type ApiRecord = Record<string, any>

export type DemoRunResponse = {
  job_id: string
  run_id: string
  job_status: string
  run_status: string
  replayed: boolean
  run_count: { ordinal: number; limit: number }
  status_url: string
  capability: CapabilityBoundary
}
