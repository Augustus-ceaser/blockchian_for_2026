export type RoleId = 'hospital' | 'research' | 'ai' | 'operator'

export type DemoStage =
  | 'catalog'
  | 'application-submitted'
  | 'application-approved'
  | 'contract-active'
  | 'compute-running'
  | 'output-review'
  | 'result-released'

export interface DemoRole {
  id: RoleId
  name: string
  shortName: string
  organization: string
  description: string
  responsibilities: string[]
}

export interface DataComposition {
  label: string
  value: string
  detail: string
}

export interface QualityMetric {
  label: string
  value: number
  display: string
}

export interface DataProduct {
  id: string
  name: string
  provider: string
  summary: string
  disease: string
  modalities: string[]
  useMode: string
  classification: string
  caseCount: number
  slideCount: number
  allowedUses: string[]
  prohibitedUses: string[]
  composition: DataComposition[]
  quality: QualityMetric[]
  connectorId: string
  version: string
  updatedAt: string
}

export interface AuditEvent {
  id: string
  time: string
  actor: string
  action: string
  object: string
  result: '成功' | '待处理' | '受控'
  hash: string
  minStage: DemoStage
}

export interface ConnectorNode {
  id: string
  name: string
  organization: string
  role: string
  status: '在线' | '维护中'
  capabilities: string[]
  lastHeartbeat: string
  certificate: string
}
