export type DemandAssistantCandidate = {
  product_id: string
  version_id: string
  product_code: string
  name: string
  provider: string
  disease_domain: string
  modality: string
  task_type: string
  version: string
  non_clinical: boolean
  score: number
  match_level: 'strong' | 'partial'
  recommendation_eligible: boolean
  reasons: string[]
  limitations: string[]
}

export type DemandAssistantPairGateStatus = 'pass' | 'hold' | 'fail'

export type DemandAssistantPairCandidate = {
  pair_key: string
  data_product_id: string
  data_product_code: string
  data_version_id: string
  data_name: string
  model_product_id: string
  model_product_code: string
  model_version_id: string
  model_name: string
  stage: 'catalog_only' | 'static_candidate' | 'application_candidate' | 'execution_ready' | 'verified_pair'
  workflow_role: 'incompatible' | 'training_required' | 'validation_ready' | 'metadata_review_required'
  hard_gate: {
    status: DemandAssistantPairGateStatus
    checks: Array<{
      code: string
      result: DemandAssistantPairGateStatus
      reason: string
      evidence: string
    }>
    overrides_score: boolean
  }
  score: {
    total: number
    max_total: number
    ruleset_version: string
    ranking_eligible: boolean
    components: Array<{
      code: string
      earned: number
      weight: number
      reason: string
    }>
  }
  reasons: string[]
  limitations: string[]
  evidence: {
    relation_id: string | null
    status: string
    level: string
    public_visible: boolean
  }
  actions: {
    can_compare: boolean
    can_select: boolean
    can_apply: boolean
    can_execute: boolean
  }
}

export type DemandAssistantResponse = {
  schema_version: string
  assistant_version: string
  pair_candidates_schema_version: string
  status: 'blocked' | 'needs_clarification' | 'catalog_gap' | 'ready'
  pair_matching_status: 'blocked' | 'needs_clarification' | 'catalog_gap' | 'ready' | 'on_hold' | 'incompatible'
  normalized_intent: {
    condition_code?: string
    condition_label?: string
    population_code?: string
    population_label?: string
    outcome_code?: string
    outcome_label?: string
    task_family?: string
    index_time_code?: string | null
    index_time_label?: string | null
    prediction_horizon?: string | null
    prediction_horizon_label?: string | null
    study_mode_code?: string
    study_mode_label?: string
    care_setting_code?: string
    care_setting_label?: string
    data_modality_code?: string
    data_modality_label?: string
    inclusion_criteria?: string[]
    exclusion_criteria?: string[]
    evaluation_outputs?: string[]
    research_definition_status?: 'defined' | 'needs_clarification'
    concept_mappings?: Array<{
      semantic_role: string
      text: string
      coding_system: string | null
      code: string | null
      mapping_status: 'not_mapped' | 'verified'
    }>
    study_definition?: {
      target_population: {
        label: string
        care_setting: { code: string; label: string; source: string }
        inclusion_criteria: string[]
        exclusion_criteria: string[]
      }
      index_time: { code: string | null; label: string | null }
      outcome: { code: string; label: string; task_family: string }
      prediction_window: { code: string | null; label: string | null }
      operation_mode: { code: string; label: string; source: string }
      modalities: Array<{ code: string; label: string; source: string }>
      terminology: Record<string, {
        display: string
        local_rule_code: string | null
        mapping_status: string
        standard_system: string | null
        standard_code: string | null
      }>
      evaluation_outputs: string[]
    }
    purpose_code?: string
  }
  clarifications: Array<{
    code: string
    required: boolean
    question: string
    options: string[]
  }>
  blocking_reasons: Array<{ code: string; message: string }>
  draft_patch: {
    profile?: Record<string, unknown>
    data_scope?: Record<string, unknown>
  }
  data_recommendations: DemandAssistantCandidate[]
  model_recommendations: DemandAssistantCandidate[]
  pair_candidates: DemandAssistantPairCandidate[]
  pair_summary: {
    total: number
    pass: number
    hold: number
    fail: number
  }
  catalog_gaps: Array<{ code: string; message: string; assessed_count: number }>
  method_suggestions: Array<{
    code: string
    name: string
    reason: string
    registered: boolean
    executable: boolean
    boundary: string
  }>
  can_apply_draft: boolean
  can_apply_catalog_selection: boolean
  can_apply_pair_selection: boolean
  boundary: {
    research_only: boolean
    recommendation_only: boolean
    clinical_use: boolean
    auto_approval: boolean
    auto_training: boolean
    creates_application: boolean
    creates_compute_job: boolean
    raw_data_access: boolean
    requires_pre_index_features: boolean
    temporal_leakage_check_enforced: boolean
    hard_isolation: boolean
    catalog_scope: string
  }
  disclaimer: string
}

export type DemandAssistantHandoff = {
  text: string
  result: DemandAssistantResponse
  selectedPairKey: string
  selectedPair: DemandAssistantPairCandidate
}

export function selectVisibleRecommendations(
  result: DemandAssistantResponse,
  dataOptions: Array<{ version_id: string }>,
  modelOptions: Array<{ version_id: string }>,
  selectedPairKey: string | null,
) {
  const visibleData = new Set(dataOptions.map((item) => item.version_id))
  const visibleModels = new Set(modelOptions.map((item) => item.version_id))
  if (!selectedPairKey || !result.can_apply_pair_selection) {
    return { dataVersionId: undefined, modelVersionId: undefined, canApplyPair: false }
  }
  const pair = result.pair_candidates.find(
    (item) => item.pair_key === selectedPairKey
      && item.actions.can_select
      && item.actions.can_apply
      && item.hard_gate.status !== 'fail'
      && item.workflow_role !== 'incompatible'
      && visibleData.has(item.data_version_id)
      && visibleModels.has(item.model_version_id),
  )
  if (pair) {
    return {
      dataVersionId: pair.data_version_id,
      modelVersionId: pair.model_version_id,
      canApplyPair: true,
    }
  }
  return { dataVersionId: undefined, modelVersionId: undefined, canApplyPair: false }
}
