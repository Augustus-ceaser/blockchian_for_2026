export const demoIdentities = [
  'space_operator',
  'data_provider',
  'model_provider',
  'data_requester',
] as const

export type DemoIdentity = (typeof demoIdentities)[number]

export function isDemoIdentity(value: unknown): value is DemoIdentity {
  return typeof value === 'string' && (demoIdentities as readonly string[]).includes(value)
}

export type ContractSecurityResult = 'PASS' | 'PENDING' | 'BLOCKER'

export type ContractSecurityValidation = {
  schema_version: string
  profile_version: string
  overall: ContractSecurityResult
  snapshot_digest: string
  checked_at: string
  summary: {
    purpose_code: string
    run_count: number
    effective_until: string
    allowed_outputs: string[]
    network_allowed: boolean
    output_review_required: boolean
    prohibited_actions: string[]
    identity_assurance: string
  }
  checks: Array<{
    code: string
    result: ContractSecurityResult
    message: string
  }>
}

export type RoadshowContext = {
  identity: DemoIdentity
  organization: string
  user: string
  space_id: string
  notice: string
  assurance: {
    hard_isolation: boolean
    clinical_validation: boolean
    production_privacy_compute: boolean
    national_certification: boolean
  }
}

export type RoadshowOverview = {
  role: DemoIdentity
  data_listing: string | null
  model_listing: string | null
  application: string | null
  contract: string | null
  execution_ready: boolean
  run: string | null
  artifact: string | null
  result_package: string | null
  my_pending_reviews: number
}

export type WorkflowState = {
  application: null | { id: string; number: string; status: string; purpose: string }
  reviews: Array<{ id: string; type: string; status: string; mine: boolean }>
  contract: null | { id: string; number: string; status: string; content_digest: string }
  signatures: Array<{ party_role: string; signed_at: string }>
  readiness: Array<{ type: string; confirmed_at: string }>
  run: null | { id: string; status: string; ordinal: number }
  artifact: null | { id: string; status: string; digest: string }
  artifact_reviews: Array<{ id: string; type: string; status: string; required: boolean; mine: boolean }>
  result_package: null | { id: string; status: string; files: Array<string | { name?: string; path?: string }> }
  audit: Array<{ sequence: number; type: string; result: string; occurred_at: string }>
}

export type CatalogItem = Record<string, unknown> & {
  id: string
  version_id: string
  name: string
  description: string
  version: string
  status: string
  published: boolean
  restrictions: string[]
}

export type RoadshowMode = '8min' | '15min'
export type RoadshowEventView = 'critical' | 'all'

export type RoadshowChainSummary = {
  application_id: string
  application_number: string
  scenario_name: string
  status: 'active' | 'completed'
  completed_nodes: number
  total_nodes: number
  next_role: DemoIdentity | null
  next_action: string
}

export type RoadshowChainNode = {
  key: string
  label: string
  object_id: string | null
  number: string | null
  status: string
  complete: boolean
  responsible_role: DemoIdentity | null
  href: string | null
}

export type RoadshowChainDetail = RoadshowChainSummary & {
  nodes: RoadshowChainNode[]
  facts: {
    data_product: { name: string | null; version: string | null; digest: string | null }
    model_product: { name: string | null; version: string | null; digest: string | null }
    contract: {
      number: string | null
      status: string | null
      digest: string | null
      signatures: number
      required_signatures: number
    }
    execution: {
      readiness: string[]
      eligibility: boolean
      job_status: string | null
      run_status: string | null
      sample_count: number | null
    }
    result: {
      artifact_status: string | null
      artifact_digest: string | null
      approved_reviews: number
      required_reviews: number
      package_status: string | null
      package_files: string[]
      grant_status: string | null
      download_count: number
      max_downloads: number
    }
  }
  hard_isolation: false
}

export type RoadshowChainEvents = {
  view: RoadshowEventView
  audit_chain_valid: boolean
  invalid_sequence: number | null
  total: number
  items: Array<{
    event_id: string
    sequence: number
    event_type: string
    result: string
    occurred_at: string
    actor: string
    subject_type: string
    subject_id: string
    state_before: string | null
    state_after: string | null
    evidence_digest: string | null
    event_digest: string | null
  }>
}

export type RoadshowHealth = {
  status: 'ok' | 'not_ready'
  audit_chain_valid: boolean
  invalid_sequence: number | null
  hard_isolation: false
  services: Array<{
    key: string
    label: string
    status: 'ok' | 'not_ready' | 'unknown'
  }>
}
