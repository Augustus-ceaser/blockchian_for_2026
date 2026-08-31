import type { DemandAssistantResponse } from './demandAssistant'

export type AssistantSearchKind = 'contract' | 'data' | 'model' | 'service' | 'application' | 'execution' | 'result' | 'lifecycle' | 'workflow'

export type AssistantResult = {
  key: string
  kind: AssistantSearchKind
  label: string
  title: string
  subtitle: string
  status?: string | null
  path: string
}

export type AssistantToolTrace = {
  tool: string
  label: string
  status: 'success' | 'empty' | 'error'
  result_count: number
  source: string
  risk_class: 'read' | 'propose' | 'commit'
  authorization_result: 'allowed' | 'denied'
  requires_confirmation: boolean
  duration_ms: number | null
  error_code: string | null
}

export type AssistantCompatibilityEvidence = {
  relation_id: string | null
  data_name: string | null
  data_version: string | null
  model_name: string | null
  model_version: string | null
  status: string
  status_label: string
  evidence_level: string
  evidence_type: string | null
  outcome: string | null
  evidence_note: string | null
  blocking_reasons: string[]
  warning_reasons: string[]
  transformation_requirements: string[]
  assessed_at: string | null
  path: string | null
}

export type AssistantLineageNode = {
  key: string
  label: string
  number: string | null
  status: string
  complete: boolean
  state: 'completed' | 'active' | 'pending' | 'blocked'
  responsible_role: string | null
}

export type AssistantExecutionLineage = {
  application_id: string
  application_number: string
  scenario_name: string
  status: string
  completed_nodes: number
  total_nodes: number
  next_role: string | null
  next_action: string | null
  path: string
  nodes: AssistantLineageNode[]
}

export type RoleAssistantQueryResponse = {
  schema_version: 'medtrust.role-assistant-query/v1'
  conversation_id: string | null
  turn_id: string | null
  context_applied: boolean
  runtime: 'legacy' | 'pydantic_ai'
  retrieval_mode: 'structured' | 'lexical' | 'hybrid'
  plan_source: 'openai' | 'deepseek' | 'local'
  intent: 'analyze_research_demand' | 'search_resources' | 'open_workflow'
  answer: string
  route_hint: string | null
  results: AssistantResult[]
  metrics: Array<{ label: string; count: number; unit: string }>
  demand_result: DemandAssistantResponse | null
  compatibility_evidence: AssistantCompatibilityEvidence[]
  lineage: AssistantExecutionLineage[]
  tool_trace: AssistantToolTrace[]
  source_of_truth: 'medtrust_platform'
  read_only: true
}
