export type ModelProductDraft = {
  basic: {
    name: string
    short_name: string
    team: string
    task_type: string
    task_description: string
    disease_domain: string
    modality: string
    description: string
    source_type: string
    model_owner: string
    contact_department: string
    is_demo: boolean
    clinical_use: boolean
  }
  runtime: {
    version_label: string
    version_notes: string
    framework: string
    runtime: string
    model_digest: string
    entrypoint_id: string
    input_schema_version: string
    output_schema_version: string
    device: 'cpu'
    cpu_limit: number
    memory_limit_mb: number
    timeout_seconds: number
    network_access: boolean
    input_read_only: boolean
    dynamic_dependencies: boolean
    arbitrary_code: boolean
    model_ready: boolean
    executor_type: 'local_builtin'
  }
  schema: {
    input_schema: Record<string, unknown>
    output_schema: Record<string, unknown>
    allowed_outputs: string[]
    prohibited_outputs: string[]
  }
  policy: {
    service_modes: Array<'controlled_compute' | 'model_artifact_license'>
    allowed_purposes: string[]
    prohibited_purposes: string[]
    max_runs: number
    valid_days: number
    multi_center_validation: boolean
    commercial_validation: boolean
    research_publication: boolean
    provider_result_confirmation: boolean
    model_download: boolean
    reverse_engineering: boolean
    redistribution: boolean
    dynamic_script_execution: boolean
    unauthorized_network: boolean
  }
}

export type ModelAsset = {
  asset_id: string
  name: string
  version: string
  model_digest: string
  registry_digest: string
  entrypoint_id: string
  runtime: string
  input_schema_version: string
  output_schema_version: string
  network_access: boolean
  cpu_limit: number
  memory_limit_mb: number
  timeout_seconds: number
  executor_type: 'local_builtin'
  runtime_status: string
  model_ready: boolean
  allowed_output_files: string[]
}

const forbiddenOutputs = new Set([
  'model_weights',
  'intermediate_features',
  'raw_input_images',
  'arbitrary_scripts',
  'unapproved_sample_predictions',
  'runtime_credentials',
])

export function validateModelBoundary(draft: ModelProductDraft): string[] {
  const errors: string[] = []
  if (!draft.basic?.is_demo || draft.basic?.clinical_use) errors.push('当前仅允许非临床工程演示模型')
  if (!/^sha256:[0-9a-f]{64}$/.test(draft.runtime?.model_digest || '')) errors.push('模型 digest 格式无效')
  if (draft.runtime?.entrypoint_id !== 'pathmnist_resnet18_v1') errors.push('必须使用固定白名单 entrypoint')
  if (draft.runtime?.network_access || draft.runtime?.dynamic_dependencies || draft.runtime?.arbitrary_code) errors.push('运行边界包含未授权能力')
  if (!draft.runtime?.input_read_only || !draft.runtime?.model_ready) errors.push('模型资产必须 ready 且输入只读')
  if ((draft.schema?.allowed_outputs || []).some((item) => forbiddenOutputs.has(item))) errors.push('允许输出包含平台禁止内容')
  if (!(draft.policy?.service_modes || []).length) errors.push('至少选择一种模型授权方式')
  if (draft.policy?.model_download || draft.policy?.redistribution || draft.policy?.reverse_engineering) errors.push('许可策略不能开放下载、二次分发或反编译')
  return errors
}

export function buildPathmnistModelDraft(asset: ModelAsset): ModelProductDraft {
  return {
    basic: {
      name: 'PathMNIST ResNet-18 病理分类模型',
      short_name: 'PathMNIST ResNet-18',
      team: '医学 AI 算法团队',
      task_type: 'image_classification',
      task_description: '九分类病理图像分类',
      disease_domain: '结直肠组织病理分类',
      modality: 'digital_pathology',
      description: '固定白名单、CPU 推理、九分类的非临床工程演示模型，不提供权重下载或任意代码执行。',
      source_type: 'platform_allowlisted',
      model_owner: '模型技术负责人（演示）',
      contact_department: '医学 AI 算法团队',
      is_demo: true,
      clinical_use: false,
    },
    runtime: {
      version_label: 'v1.0',
      version_notes: '首个固定白名单模型产品版本，用于验证模型上架、审核、发布和审计流程。',
      framework: 'PyTorch',
      runtime: asset.runtime,
      model_digest: asset.model_digest,
      entrypoint_id: asset.entrypoint_id,
      input_schema_version: asset.input_schema_version,
      output_schema_version: asset.output_schema_version,
      device: 'cpu',
      cpu_limit: asset.cpu_limit,
      memory_limit_mb: asset.memory_limit_mb,
      timeout_seconds: asset.timeout_seconds,
      network_access: false,
      input_read_only: true,
      dynamic_dependencies: false,
      arbitrary_code: false,
      model_ready: asset.model_ready,
      executor_type: 'local_builtin',
    },
    schema: {
      input_schema: {
        type: 'image',
        modality: 'digital_pathology',
        width: 28,
        height: 28,
        channels: 3,
        dtype: 'uint8',
        batch_supported: true,
        accepted_formats: ['NPZ registered input'],
        unsupported_formats: ['arbitrary file upload'],
      },
      output_schema: {
        class_probabilities: 'aggregate-only',
        aggregate_accuracy: true,
        mean_confidence: true,
        confusion_matrix: true,
        execution_summary: true,
      },
      allowed_outputs: ['aggregate_metrics', 'confusion_matrix', 'execution_summary'],
      prohibited_outputs: ['模型权重', '中间特征', '原始输入图像', '任意脚本', '未批准样本级预测', '运行环境凭据'],
    },
    policy: {
      service_modes: ['controlled_compute', 'model_artifact_license'],
      allowed_purposes: ['科研验证', '外部性能验证', '教学演示'],
      prohibited_purposes: ['临床诊断', '未授权训练', '患者识别', '超出合同范围使用'],
      max_runs: 5,
      valid_days: 30,
      multi_center_validation: false,
      commercial_validation: false,
      research_publication: true,
      provider_result_confirmation: true,
      model_download: false,
      reverse_engineering: false,
      redistribution: false,
      dynamic_script_execution: false,
      unauthorized_network: false,
    },
  }
}
