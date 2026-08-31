import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transformWithOxc } from 'vite'

const pagePath = new URL('../src/roadshow/ApplicationLifecyclePages.tsx', import.meta.url)
const apiPath = new URL('../src/roadshow/api.ts', import.meta.url)
const legacyApiPath = new URL('../src/api/client.ts', import.meta.url)
const errorDetailPath = new URL('../src/api/errorDetail.ts', import.meta.url)
const routerPath = new URL('../src/router/index.tsx', import.meta.url)
const shellPath = new URL('../src/roadshow/RoadshowShell.tsx', import.meta.url)

async function source(path) {
  return readFile(path, 'utf8')
}

test('application lifecycle exposes exactly five wizard steps', async () => {
  const page = await source(pagePath)
  const titles = [
    '选择数据产品',
    '选择模型产品',
    '填写计算需求',
    '兼容性检查',
    '预览与提交',
  ]
  const stepsStart = page.indexOf('<Steps current={step}')
  const stepsEnd = page.indexOf(']} />', stepsStart)
  assert.ok(stepsStart >= 0 && stepsEnd > stepsStart)
  const steps = page.slice(stepsStart, stepsEnd)
  let previousTitle = -1
  for (const title of titles) {
    const currentTitle = steps.indexOf(`{ title: '${title}' }`)
    assert.ok(currentTitle > previousTitle, title)
    previousTitle = currentTitle
  }
  assert.equal(titles.length, 5)
})

test('PathMNIST sample fill only updates local form state', async () => {
  const page = await source(pagePath)
  const sampleBody = page.match(/const fillSample = \(\) => \{([\s\S]*?)\n  \}\n\n  const saveDraft/)
  assert.ok(sampleBody)
  assert.ok(sampleBody[1].includes('form.setFieldsValue(sampleDraft(options.data))'))
  assert.ok(sampleBody[1].includes('尚未保存或提交'))
  assert.ok(!sampleBody[1].includes('platformCommand'))
  assert.ok(!sampleBody[1].includes('navigate('))
})

test('BLOCKER and unacknowledged WARNING prevent submission', async () => {
  const page = await source(pagePath)
  assert.ok(page.includes("if (report.blockers.length)"))
  assert.ok(page.includes("当前存在 BLOCKER，不能提交。"))
  assert.ok(page.includes("report.warnings.length && !warningsAccepted"))
  assert.ok(page.includes("disabled={!report || Boolean(report.blockers.length) || Boolean(report.warnings.length && !warningsAccepted)}"))
})

test('request details precede compatibility and incomplete selection steps cannot save', async () => {
  const page = await source(pagePath)
  assert.match(page, /step === 2[\s\S]*计算需求与数据范围/)
  assert.match(page, /step === 3[\s\S]*数据与模型兼容性检查/)
  const fieldsStart = page.indexOf('const wizardFields')
  const fieldsEnd = page.indexOf('function SelectionGrid')
  assert.ok(fieldsStart >= 0 && fieldsEnd > fieldsStart)
  const fields = page.slice(fieldsStart, fieldsEnd)
  for (const requiredField of [
    "['profile', 'project_lead']",
    "['profile', 'contact']",
    "['profile', 'ethics_or_approval_statement']",
    "['review_requirements', 'output_recipient']",
  ]) {
    const requiredIndex = fields.indexOf(requiredField)
    assert.ok(requiredIndex >= 0, requiredField)
    assert.ok(requiredIndex < fields.indexOf("['profile', 'purpose_code']"), requiredField)
  }
  for (const validationMessage of [
    '项目负责人至少填写 2 个字符',
    '联系方式或联系部门至少填写 2 个字符',
    '伦理或审批状态说明至少填写 5 个字符',
    '输出接收负责人至少填写 2 个字符',
  ]) assert.ok(page.includes(validationMessage), validationMessage)
  assert.ok(page.includes("if (step === 3)"))
  assert.ok(page.includes("await saveAndCheckDraft()"))
  assert.ok(page.includes("{step >= 2 && <Button"))
  assert.ok(page.includes("onValuesChange={(changedValues) => {"))
  assert.ok(page.includes("setReport(null)"))
  assert.ok(page.includes("Form.useWatch('data_version_id', { form, preserve: true })"))
  assert.ok(page.includes("Form.useWatch('model_version_id', { form, preserve: true })"))
})

test('wizard only preselects an explicitly confirmed Agent pair and freezes its evidence', async () => {
  const page = await source(pagePath)
  assert.match(page, /demandAssistantHandoff\?\.selectedPairKey/)
  assert.match(page, /selectVisibleRecommendations\([\s\S]*?demandAssistantHandoff\.selectedPairKey/)
  assert.match(page, /result\.pair_candidates\.find\([\s\S]*?demandAssistantHandoff\.selectedPairKey/)
  assert.ok(page.includes('snapshotMatches'))
  assert.ok(page.includes("selected_by_user: true"))
  assert.ok(page.includes('setRecommendationContext(context)'))
  assert.ok(page.includes('已带入你确认的数据—模型组合和需求字段；尚未保存或提交'))
  assert.ok(page.includes('Agent 组合选择快照'))
  assert.ok(page.includes('客户端选择陈述，未作平台验证'))
  assert.ok(page.includes('client_selection_snapshot_receipt'))
  assert.ok(page.includes('申请资格依据'))
  assert.ok(page.includes('服务端兼容性报告'))
  assert.ok(page.includes("if ('data_version_id' in changedValues || 'model_version_id' in changedValues)"))
  assert.ok(page.includes('setRecommendationContext(null)'))
  assert.ok(page.includes('recommendation_context: recommendationContext || undefined'))
})

test('catalog product handoff preselects only a currently visible version', async () => {
  const page = await source(pagePath)
  assert.ok(page.includes('productSelection?:'))
  assert.match(page, /options\.data\.data_products\.some\([\s\S]*?productSelection\.dataVersionId/)
  assert.match(page, /options\.data\.model_products\.some\([\s\S]*?productSelection\.modelVersionId/)
  assert.ok(page.includes('已预选当前产品，请继续选择组合并完善需求'))
})

test('structured API validation details never coerce to object strings', async () => {
  const [api, legacyApi, errorDetail] = await Promise.all([
    source(apiPath),
    source(legacyApiPath),
    source(errorDetailPath),
  ])
  assert.ok(api.includes("from '../api/errorDetail'"))
  assert.ok(legacyApi.includes("from './errorDetail'"))
  assert.ok(errorDetail.includes('detail?: unknown'))
  assert.ok(errorDetail.includes('Array.isArray(detail)'))
  assert.ok(errorDetail.includes("record.msg"))
  assert.ok(errorDetail.includes("filter((part) => part !== 'body')"))
  assert.ok(!api.includes('payload.detail ||'))
  assert.ok(!legacyApi.includes('payload.detail ||'))
  assert.ok(!errorDetail.includes('JSON.stringify(detail)'))
})

test('structured API validation formatter returns readable fields without echoing input', async () => {
  const sourceText = await source(errorDetailPath)
  const { code: outputText } = await transformWithOxc(sourceText, 'errorDetail.ts', { lang: 'ts' })
  const module = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
  const message = module.formatApiDetail([
    { loc: ['body', 'profile', 'project_lead'], msg: 'String should have at least 2 characters', ctx: { min_length: 2 }, input: '不应显示的姓名' },
    { loc: ['body', 'profile', 'contact'], msg: 'Field required', input: '不应显示的联系方式' },
    { loc: ['body', 'profile', 'ethics_or_approval_statement'], msg: 'String should have at least 5 characters', ctx: { min_length: 5 }, input: '不应显示的审批内容' },
    { loc: ['body', 'review_requirements', 'output_recipient'], msg: 'String should have at least 2 characters', ctx: { min_length: 2 }, input: '不应显示的接收人' },
  ])
  assert.equal(message, '项目负责人：至少填写 2 个字符；联系方式或联系部门：未填写；伦理或审批状态说明：至少填写 5 个字符；输出接收负责人：至少填写 2 个字符')
  assert.ok(!message.includes('[object Object]'))
  assert.ok(!message.includes('不应显示'))
})

test('wizard and reviewer writes use the shared single-flight guard', async () => {
  const page = await source(pagePath)
  assert.ok(page.includes("import { createSingleFlight, startAbortableLoad } from './requestLifecycle'"))
  assert.ok((page.match(/useRef\(createSingleFlight\(\)\)\.current/g) || []).length >= 2)
  assert.ok((page.match(/await guard\.run/g) || []).length >= 4)
})

test('application routes and role-specific menus are present', async () => {
  const [router, shell] = await Promise.all([source(routerPath), source(shellPath)])
  for (const route of [
    '/applications',
    '/applications/new',
    '/applications/:applicationId',
    '/applications/:applicationId/edit',
  ]) assert.ok(router.includes(`path: '${route}'`))
  for (const label of ['服务申请审批', '数据授权与使用审批', '模型授权与使用审批', '我的申请']) {
    assert.ok(shell.includes(label))
  }
})

test('application UI projection does not expose sensitive infrastructure fields', async () => {
  const page = await source(pagePath)
  for (const forbidden of [
    'object_storage_ref',
    'storage_reference',
    'connector_credentials',
    'database_url',
    'access_token',
    'secret_key',
  ]) assert.ok(!page.includes(forbidden), forbidden)
  assert.ok(!page.includes('hard_isolation=false'))
  assert.ok(page.includes('compute_job_creation: boolean'))
})
