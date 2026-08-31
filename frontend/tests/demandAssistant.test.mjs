import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { stripTypeScriptTypes } from 'node:module'
import test from 'node:test'

const source = (path) => readFile(new URL(path, import.meta.url), 'utf8')

const loadDemandAssistantModule = async () => {
  const contract = await source('../src/roadshow/demandAssistant.ts')
  const javascript = stripTypeScriptTypes(contract, { mode: 'transform' })
  return import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)
}

test('global requester assistant uses one governed backend agent query', async () => {
  const assistant = await source('../src/roadshow/RoleAssistant.tsx')

  assert.match(assistant, /'\/role-assistant\/query'/)
  assert.match(assistant, /response\.demand_result/)
  assert.match(assistant, /response\.tool_trace/)
  assert.doesNotMatch(assistant, /platformGet|Promise\.allSettled|\/application-assistant\/recommend/)
  for (const forbidden of [
    '/application-drafts',
    '/submit',
    '/approve',
    '/compute-runs',
  ]) assert.ok(!assistant.includes(forbidden), forbidden)
})

test('recommendations enter the wizard only after an explicit handoff', async () => {
  const [assistant, wizard, contract] = await Promise.all([
    source('../src/roadshow/RoleAssistant.tsx'),
    source('../src/roadshow/ApplicationLifecyclePages.tsx'),
    source('../src/roadshow/demandAssistant.ts'),
  ])

  assert.match(assistant, /const handoffDemand = \(\) =>/)
  assert.match(assistant, /demandResult\?\.can_apply_draft/)
  assert.match(assistant, /const demandAssistant: DemandAssistantHandoff/)
  assert.match(assistant, /selectedPairKey:/)
  assert.match(assistant, /selectedPair,/)
  assert.match(assistant, /navigate\('\/applications\/new', \{ state: \{ demandAssistant \} \}\)/)
  assert.match(assistant, />\s*确认组合并带入申请\s*</)
  assert.match(contract, /export type DemandAssistantHandoff/)
  assert.match(contract, /selectedPairKey: string/)
  assert.match(contract, /selectedPair: DemandAssistantPairCandidate/)
  assert.match(contract, /selectVisibleRecommendations/)
  assert.match(wizard, /demandAssistantHandoff/)
  assert.match(wizard, /selectVisibleRecommendations\([\s\S]*?result,[\s\S]*?options\.data\.data_products,[\s\S]*?options\.data\.model_products,[\s\S]*?demandAssistantHandoff\.selectedPairKey/)
  assert.match(wizard, /const pair = result\.pair_candidates\.find\([\s\S]*?demandAssistantHandoff\.selectedPairKey/)
  assert.match(wizard, /const snapshotMatches = Boolean\(pair/)
  assert.match(wizard, /form\.setFieldsValue/)
  assert.match(wizard, /replace: true, state: null/)
  assert.doesNotMatch(wizard, /title="自然语言研究需求助手"/)
})

test('assistant exposes actionable clarification and real-catalog outcomes without static demo chrome', async () => {
  const assistant = await source('../src/roadshow/RoleAssistant.tsx')

  for (const required of [
    'blocking_reasons',
    'clarifications',
    'catalog_gaps',
    'data_recommendations',
    'model_recommendations',
    '请勿输入姓名、病历号、联系方式或其他患者身份信息',
  ]) assert.ok(assistant.includes(required), required)
  assert.doesNotMatch(assistant, /hard_isolation=false|本机演示模式|前端切换不等于授权/)
})

test('assistant keeps the OHDSI-style definition compact and never presents a guessed medical code', async () => {
  const [assistant, contract] = await Promise.all([
    source('../src/roadshow/RoleAssistant.tsx'),
    source('../src/roadshow/demandAssistant.ts'),
  ])

  assert.match(assistant, /<StudyDefinition result=\{demandResult\}/)
  assert.match(assistant, /查看完整研究定义/)
  assert.match(assistant, /尚未映射标准医学编码/)
  assert.match(contract, /study_definition\?:/)
  assert.match(contract, /standard_system: string \| null/)
  assert.match(contract, /standard_code: string \| null/)
})

test('assistant response contract remains fail-closed even though boundary prose is not rendered repeatedly', async () => {
  const contract = await source('../src/roadshow/demandAssistant.ts')

  for (const field of [
    'research_only: boolean',
    'recommendation_only: boolean',
    'clinical_use: boolean',
    'auto_approval: boolean',
    'auto_training: boolean',
    'creates_application: boolean',
    'creates_compute_job: boolean',
    'raw_data_access: boolean',
    'requires_pre_index_features: boolean',
    'temporal_leakage_check_enforced: boolean',
    'hard_isolation: boolean',
  ]) assert.ok(contract.includes(field), field)
})

test('assistant prioritizes governed data-model pairs and keeps single candidates secondary', async () => {
  const [assistant, contract, styles] = await Promise.all([
    source('../src/roadshow/RoleAssistant.tsx'),
    source('../src/roadshow/demandAssistant.ts'),
    source('../src/styles.css'),
  ])

  for (const field of [
    'pair_candidates_schema_version',
    'pair_matching_status',
    'pair_candidates: DemandAssistantPairCandidate[]',
    'pair_summary:',
    'can_apply_pair_selection: boolean',
    "status: DemandAssistantPairGateStatus",
    'can_select: boolean',
    'can_execute: boolean',
  ]) assert.ok(contract.includes(field), field)

  assert.match(assistant, /<PairCandidateList[\s\S]*?items=\{demandResult\.pair_candidates\}[\s\S]*?selectedPairKey=\{selectedPairKey\}[\s\S]*?onSelect=\{setSelectedPairKey\}/)
  assert.match(assistant, /demandResult\.pair_candidates\.length === 0[\s\S]*?demandResult\.catalog_gaps\.map/)
  for (const label of ['数据 × 模型组合', '硬门通过', '条件待补', '硬门失败', '匹配理由', '限制', '不可选择', '不可执行']) {
    assert.ok(assistant.includes(label), label)
  }
  assert.match(assistant, /item\.score\.total/)
  assert.match(assistant, /pairWorkflowLabels\[item\.workflow_role\]/)
  assert.match(assistant, /item\.actions\.can_select/)
  assert.match(assistant, /item\.actions\.can_execute/)
  assert.match(assistant, /<details className="role-assistant-single-candidates">/)
  assert.ok(styles.includes('.role-assistant-pair-card'), 'role-assistant-pair-card')
})

test('assistant pair handoff is fail-closed and data candidate navigation stays requester-safe', async () => {
  const [assistant, contract] = await Promise.all([
    source('../src/roadshow/RoleAssistant.tsx'),
    source('../src/roadshow/demandAssistant.ts'),
  ])

  assert.match(contract, /selectedPairKey: string \| null/)
  assert.match(contract, /if \(!selectedPairKey \|\| !result\.can_apply_pair_selection\)/)
  assert.match(contract, /item\.pair_key === selectedPairKey/)
  assert.match(contract, /item\.actions\.can_select/)
  assert.match(contract, /item\.actions\.can_apply/)
  assert.match(contract, /item\.hard_gate\.status !== 'fail'/)
  assert.match(contract, /item\.workflow_role !== 'incompatible'/)
  assert.match(contract, /visibleData\.has\(item\.data_version_id\)/)
  assert.match(contract, /visibleModels\.has\(item\.model_version_id\)/)
  assert.doesNotMatch(contract, /data_recommendations\.find/)
  assert.doesNotMatch(contract, /model_recommendations\.find/)
  assert.match(assistant, /kind === 'data' \? '\/data-catalog' : `\/model-products\/\$\{item\.version_id\}`/)
  assert.doesNotMatch(assistant, /`\/data-products\/\$\{item\.version_id\}`/)
})

test('visible recommendation selection requires the exact user-confirmed applicable pair', async () => {
  const { selectVisibleRecommendations } = await loadDemandAssistantModule()
  const pair = {
    pair_key: 'pair-1',
    data_version_id: 'data-v1',
    model_version_id: 'model-v1',
    workflow_role: 'validation_ready',
    hard_gate: { status: 'pass' },
    actions: { can_select: true, can_apply: true },
  }
  const result = { can_apply_pair_selection: true, pair_candidates: [pair] }
  const dataOptions = [{ version_id: 'data-v1' }]
  const modelOptions = [{ version_id: 'model-v1' }]

  assert.deepEqual(
    selectVisibleRecommendations(result, dataOptions, modelOptions, 'pair-1'),
    { dataVersionId: 'data-v1', modelVersionId: 'model-v1', canApplyPair: true },
  )
  assert.equal(selectVisibleRecommendations(result, dataOptions, modelOptions, null).canApplyPair, false)
  assert.equal(selectVisibleRecommendations(result, dataOptions, modelOptions, 'unknown-pair').canApplyPair, false)
  assert.equal(selectVisibleRecommendations({ ...result, pair_candidates: [{
    ...pair,
    hard_gate: { status: 'fail' },
  }] }, dataOptions, modelOptions, 'pair-1').canApplyPair, false)
})

test('the five-step application workflow remains intact after moving the assistant into a drawer', async () => {
  const wizard = await source('../src/roadshow/ApplicationLifecyclePages.tsx')
  for (const step of [
    '选择数据产品',
    '选择模型产品',
    '兼容性检查',
    '填写计算需求',
    '预览与提交',
  ]) assert.ok(wizard.includes(`{ title: '${step}' }`), step)
})
