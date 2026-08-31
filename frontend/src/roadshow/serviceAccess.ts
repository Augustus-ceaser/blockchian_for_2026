export type ProductKind = 'data' | 'model'

export type ServiceMode =
  | 'controlled_compute'
  | 'deidentified_data_delivery'
  | 'model_artifact_license'

export type ServiceOffering = {
  mode: ServiceMode
  label: string
  requestable: boolean
  fulfillment_status: string
  requires_contract: boolean
}

export const serviceModeLabels: Record<ServiceMode, string> = {
  controlled_compute: '受控调用计算',
  deidentified_data_delivery: '匿名化数据授权交付',
  model_artifact_license: '模型使用许可',
}

export function offeringLabel(offering: ServiceOffering): string {
  return offering.label || serviceModeLabels[offering.mode] || offering.mode
}

export function availableOfferings(
  offerings: ServiceOffering[] | null | undefined,
  kind: ProductKind,
  controlledComputeEligible: boolean,
): ServiceOffering[] {
  if (offerings !== undefined && offerings !== null) {
    return offerings.filter((offering) => modeAppliesToKind(offering.mode, kind))
  }
  if (!controlledComputeEligible) return []
  return [{
    mode: 'controlled_compute',
    label: serviceModeLabels.controlled_compute,
    requestable: true,
    fulfillment_status: 'controlled_ready',
    requires_contract: true,
  }]
}

export function modeAppliesToKind(mode: ServiceMode, kind: ProductKind): boolean {
  if (mode === 'controlled_compute') return true
  return kind === 'data'
    ? mode === 'deidentified_data_delivery'
    : mode === 'model_artifact_license'
}

export function offeringTagColor(offering: ServiceOffering): string {
  if (!offering.requestable) return 'default'
  if (offering.mode === 'controlled_compute') return 'cyan'
  return 'blue'
}
