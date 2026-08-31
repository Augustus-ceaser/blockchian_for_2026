import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) => readFile(new URL(path, import.meta.url), 'utf8')

test('authenticated shell is compact and mounts one global role assistant', async () => {
  const [shell, brand, html] = await Promise.all([
    source('../src/roadshow/RoadshowShell.tsx'),
    source('../src/components/Brand.tsx'),
    source('../index.html'),
  ])

  assert.match(shell, /import \{ RoleAssistant \} from '\.\/RoleAssistant'/)
  assert.match(shell, /<RoleAssistant \/>/)
  assert.doesNotMatch(shell, /deploymentStatus|DeploymentStatus|本机演示模式|hard_isolation=false/)
  assert.doesNotMatch(shell, /app-header__crumb|space-badge|演示环境/)
  assert.doesNotMatch(brand, /原型/)
  assert.doesNotMatch(html, /演示原型|可信数据空间原型/)
})

test('homepage has a concise hero, three resource entrances, and one four-step flow', async () => {
  const page = await source('../src/roadshow/RoadshowSealPage.tsx')

  assert.match(page, /roadshow-seal-hero/)
  assert.match(page, /roadshow-seal-resources/)
  assert.match(page, /roadshow-seal-flow/)
  assert.equal((page.match(/<ResourceCard/g) || []).length, 3)
  for (const step of ['发现资源', '提出需求', '多方审批', '结果交付']) assert.ok(page.includes(step), step)
  assert.doesNotMatch(page, /roadshow-seal-metrics|roadshow-seal-band|roadshow-seal-boundaries/)
  assert.doesNotMatch(page, /Alembic|ComputeRun|ReleasePackage|hard_isolation|只读展示|工程原型/)
})

test('floating assistant is role-aware and delegates authorized retrieval to one backend agent endpoint', async () => {
  const assistant = await source('../src/roadshow/RoleAssistant.tsx')

  assert.match(assistant, /medtrust-ai-assistant\.png/)
  assert.match(assistant, /<Drawer/)
  assert.match(assistant, /placement="right"/)
  for (const role of ['space_operator', 'data_provider', 'model_provider', 'data_requester']) {
    assert.ok(assistant.includes(`${role}:`), role)
  }
  assert.match(assistant, /'\/role-assistant\/query'/)
  assert.doesNotMatch(assistant, /platformGet|Promise\.allSettled|\/digital-contracts|\/execution-readiness/)
  assert.match(assistant, /openPath\(item\.path\)/)
  assert.doesNotMatch(assistant, /catalog_curator/)
})

test('requester assistant keeps server analysis and explicitly hands a draft into the wizard', async () => {
  const [assistant, demandContract, wizard] = await Promise.all([
    source('../src/roadshow/RoleAssistant.tsx'),
    source('../src/roadshow/demandAssistant.ts'),
    source('../src/roadshow/ApplicationLifecyclePages.tsx'),
  ])

  assert.match(assistant, /response\.demand_result/)
  assert.match(assistant, /const demandAssistant: DemandAssistantHandoff/)
  assert.match(assistant, /navigate\('\/applications\/new', \{ state: \{ demandAssistant \} \}\)/)
  assert.match(assistant, /kind === 'data' \? '\/data-catalog' : `\/model-products\/\$\{item\.version_id\}`/)
  assert.doesNotMatch(assistant, /`\/data-products\/\$\{item\.version_id\}`/)
  assert.match(assistant, /结直肠组织病理图像验证一个分类模型/)
  assert.doesNotMatch(assistant, /setInput\(query\)/)
  assert.match(assistant, /const query = \(suggested \?\? input\)\.trim\(\)[\s\S]*?setInput\(''\)[\s\S]*?setLastQuery\(query\)/)
  assert.match(demandContract, /export type DemandAssistantHandoff/)
  assert.match(demandContract, /selectVisibleRecommendations/)
  assert.match(wizard, /useLocation/)
  assert.match(wizard, /demandAssistantHandoff/)
  assert.match(wizard, /replace: true, state: null/)
  assert.match(demandContract, /recommendation_eligible/)
  assert.doesNotMatch(wizard, /title="自然语言研究需求助手"/)
  assert.doesNotMatch(wizard, /aria-label="自然语言研究需求"/)
  assert.match(assistant, /请勿输入姓名、病历号、联系方式或其他患者身份信息/)
})

test('assistant renders compact evidence returned by the authoritative backend tool layer', async () => {
  const [assistant, contract] = await Promise.all([
    source('../src/roadshow/RoleAssistant.tsx'),
    source('../src/roadshow/roleAssistantContract.ts'),
  ])

  assert.match(assistant, /<ToolTrace items=\{toolTrace\}/)
  assert.match(assistant, /已查询平台资源/)
  assert.match(contract, /source_of_truth: 'medtrust_platform'/)
  assert.match(contract, /tool_trace: AssistantToolTrace\[\]/)
  assert.match(contract, /result_count: number/)
  assert.match(assistant, /<CompatibilityEvidenceList items=\{compatibilityEvidence\}/)
  assert.match(assistant, /<ExecutionLineageList items=\{lineage\}/)
  assert.match(contract, /compatibility_evidence: AssistantCompatibilityEvidence\[\]/)
  assert.match(contract, /lineage: AssistantExecutionLineage\[\]/)
  assert.match(contract, /state: 'completed' \| 'active' \| 'pending' \| 'blocked'/)
  assert.match(contract, /conversation_id: string \| null/)
  assert.match(contract, /runtime: 'legacy' \| 'pydantic_ai'/)
  assert.match(contract, /retrieval_mode: 'structured' \| 'lexical' \| 'hybrid'/)
  assert.match(assistant, /\{ message: query, conversation_id: conversationId \}/)
  assert.match(assistant, /setConversationId\(response\.conversation_id\)/)
  assert.match(assistant, /已结合本次会话中的资源/)
})

test('assistant navigation names the destination and hides an equivalent current-page action', async () => {
  const assistant = await source('../src/roadshow/RoleAssistant.tsx')

  assert.match(assistant, /useLocation/)
  assert.match(assistant, /canonicalAssistantRoute/)
  assert.match(assistant, /查看公共候选数据目录/)
  assert.match(assistant, /查看数据产品审核/)
  assert.match(assistant, /查看模型产品审核/)
  assert.match(assistant, /assistantRouteActionLabel\(routeHint, identity\)/)
  assert.match(assistant, /canonicalAssistantRoute\(location\.pathname\) !== canonicalAssistantRoute\(routeHint\)/)
  assert.doesNotMatch(assistant, /打开相关功能/)
})

test('assistant and premium shell have responsive, accessible presentation styles', async () => {
  const [assistant, styles] = await Promise.all([
    source('../src/roadshow/RoleAssistant.tsx'),
    source('../src/styles.css'),
  ])

  for (const selector of [
    '.role-assistant-launcher',
    '.role-assistant-avatar',
    '.role-assistant-drawer',
    '.role-assistant-result',
    '.role-assistant-trace',
    '.roadshow-seal-hero',
    '.roadshow-seal-resources',
    '.roadshow-seal-flow',
  ]) assert.ok(styles.includes(selector), selector)
  assert.match(styles, /@media \(max-width: 640px\)[\s\S]*role-assistant-launcher/)
  assert.match(assistant, /aria-expanded=\{open\}/)
  assert.match(assistant, /setPointerCapture\(event\.pointerId\)/)
  for (const handler of ['onPointerDown', 'onPointerMove', 'onPointerUp', 'onPointerCancel']) {
    assert.ok(assistant.includes(handler), handler)
  }
  assert.match(assistant, /suppressLauncherClick/)
  assert.match(assistant, /nativeEvent\.isComposing/)
  assert.match(styles, /@media \(hover: hover\) and \(pointer: fine\) and \(min-width: 641px\) \{[\s\S]*?\.role-assistant-launcher \{ right: 0; transform: translateX\(48px\); \}/)
  assert.match(styles, /touch-action: none;/)
  assert.match(styles, /\.role-assistant-launcher\.is-dragging/)
  assert.match(styles, /\.role-assistant-launcher:hover,\s*\.role-assistant-launcher:focus-visible,\s*\.role-assistant-launcher\[aria-expanded="true"\],\s*\.role-assistant-launcher\.is-dragging \{[\s\S]*?transform: translateX\(0\);/)
  assert.match(styles, /prefers-reduced-motion/)
})
