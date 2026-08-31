export type DataProductDraft = {
  basic: {
    name: string
    short_name: string
    department: string
    disease_domain: string
    modality: string
    source_type: 'public_demo_dataset' | 'hospital_research_data' | 'multicenter_collaboration' | 'other'
    description: string
    data_owner: string
    contact_department: string
    is_demo: boolean
  }
  composition: {
    case_count: number
    slide_count: number
    image_count: number
    data_format: string
    image_specification: string
    annotation_type: string
    annotation_coverage: number
    completeness_rate: number
    quality_status: 'pending' | 'passed' | 'conditional'
    data_version: string
    version_notes: string
    resource_summary: string
  }
  policy: {
    service_modes: Array<'controlled_compute' | 'deidentified_data_delivery'>
    allowed_purposes: string[]
    prohibited_purposes: string[]
    max_runs: number
    valid_days: number
    fixed_model_version: boolean
    requires_egress_review: boolean
    internet_allowed: boolean
    input_read_only: boolean
    allowed_outputs: string[]
    prohibited_outputs: string[]
    hard_isolation: boolean
  }
  binding: {
    connector_id: string
    resource_identifier: string
    data_ready: boolean
  }
}

const forbiddenAllowedOutputs = new Set(['raw_images', 'model_weights', 'connector_credentials'])

export function validateDraftBoundary(draft: DataProductDraft): string[] {
  const errors: string[] = []
  if (!draft.basic?.is_demo) errors.push('Phase 5.1 仅允许公开或合成演示元数据')
  if (draft.policy?.hard_isolation) errors.push('当前工程边界必须保持 hard_isolation=false')
  if ((draft.policy?.allowed_outputs || []).some((item) => forbiddenAllowedOutputs.has(item))) {
    errors.push('允许输出包含被平台禁止的敏感类型')
  }
  if (!(draft.policy?.service_modes || []).length) errors.push('至少选择一种数据授权方式')
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$/.test(draft.binding?.resource_identifier || '')) {
    errors.push('资源标识只能使用 3-64 位字母、数字、点、下划线或短横线')
  }
  if (!draft.binding?.connector_id) errors.push('必须选择医院 Connector')
  if (!draft.binding?.data_ready) errors.push('提交审核前必须确认数据已就绪')
  return errors
}

export function buildPublicDemoDraft(connectorId = ''): DataProductDraft {
  return {
    basic: {
      name: 'PathMNIST 病理图像分类验证数据产品',
      short_name: 'PathMNIST 验证集',
      department: '病理科研中心',
      disease_domain: '结直肠组织病理分类',
      modality: '数字病理图像',
      source_type: 'public_demo_dataset',
      description: '基于公开 PathMNIST 演示数据的受控计算验证产品，仅登记元数据，不上传原始图像或患者信息。',
      data_owner: '医院数据管理负责人（演示）',
      contact_department: '病理科研中心',
      is_demo: true,
    },
    composition: {
      case_count: 20,
      slide_count: 0,
      image_count: 20,
      data_format: 'NPZ 元数据登记',
      image_specification: '28 x 28 RGB',
      annotation_type: '九分类公开基准标签',
      annotation_coverage: 100,
      completeness_rate: 100,
      quality_status: 'passed',
      data_version: 'v1.0',
      version_notes: '首个公开演示版本，用于验证数据产品创建、审核、发布与审计流程。',
      resource_summary: '20 张授权公开演示图像的元数据范围，不包含图像文件或样本级输出。',
    },
    policy: {
      service_modes: ['controlled_compute', 'deidentified_data_delivery'],
      allowed_purposes: ['research_analysis', 'model_validation', 'external_performance_validation', 'teaching_demo'],
      prohibited_purposes: ['临床诊断', '未授权模型训练', '二次分发', '患者识别', '超出合同范围使用'],
      max_runs: 5,
      valid_days: 30,
      fixed_model_version: true,
      requires_egress_review: true,
      internet_allowed: false,
      input_read_only: true,
      allowed_outputs: ['aggregate_metrics', 'confusion_matrix', 'execution_summary'],
      prohibited_outputs: ['原始图像', '样本级预测', '原始特征', '模型权重', '执行脚本', 'Connector 凭据'],
      hard_isolation: false,
    },
    binding: {
      connector_id: connectorId,
      resource_identifier: 'PATHMNIST-DEMO-20',
      data_ready: true,
    },
  }
}
