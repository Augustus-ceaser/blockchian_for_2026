import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) => readFile(new URL(path, import.meta.url), 'utf8')

function roleItems(shell, role) {
  const match = shell.match(new RegExp(`  ${role}: \\[([\\s\\S]*?)\\n  \\],`))
  assert.ok(match, `missing ${role} menu`)
  return match[1]
}

test('primary role menus are short and engineering controls stay routed but hidden', async () => {
  const shell = await source('../src/roadshow/RoadshowShell.tsx')
  const limits = {
    space_operator: 8,
    data_provider: 7,
    model_provider: 7,
    data_requester: 8,
  }
  for (const [role, limit] of Object.entries(limits)) {
    const items = roleItems(shell, role)
    assert.ok((items.match(/common\./g) || []).length <= limit, `${role} exceeds ${limit}`)
    assert.doesNotMatch(items, /connectorControl|policyControl|hospitalConnectors|hospitalPolicyControl|externalModelGovernance|datasetModelEvidence|materializationPlans|lifecycle|workflow|contracts|audit/)
  }
  for (const route of [
    '/portal/operator/connectors',
    '/portal/hospital/connectors',
    '/portal/operator/policy-control',
    '/portal/hospital/policy-control',
  ]) assert.ok(shell.includes(route), route)
})

test('normal and compatibility chrome remove demo disclaimers while preserving status', async () => {
  const [shell, legacyShell, legacyLogin, apiPages, styles] = await Promise.all([
    source('../src/roadshow/RoadshowShell.tsx'),
    source('../src/components/AppShell.tsx'),
    source('../src/pages/DemoLoginPage.tsx'),
    source('../src/api/ApiPages.tsx'),
    source('../src/styles.css'),
  ])
  assert.doesNotMatch(shell, /hard_isolation=false|\u672c机演示模式|space-badge/)
  assert.match(shell, /contextError && <div className="phase4-context-error"/)
  assert.doesNotMatch(shell, /phase4-context-bar|phase4-disclaimer|公开数据工程演示 ·/)
  assert.doesNotMatch(legacyShell, /DemoNotice|演示环境状态正常|服务平台原型运行中|Mock 演示模式|重置演示流程|返回演示登录/)
  assert.match(legacyShell, /系统状态正常/)
  assert.doesNotMatch(legacyLogin, /login-disclaimer|hard_isolation=false|不用于临床|可点击产品原型|真实后端演示模式|选择演示身份/)
  assert.match(legacyLogin, />选择身份</)
  assert.doesNotMatch(apiPages, /工程原型|hard_isolation=false|不用于临床|历史架构演示|兼容既有演示流程/)
  assert.doesNotMatch(styles, /\.phase4-context-bar|\.phase4-disclaimer|\.demo-notice/)
})

test('overview and catalogs remove repeated large disclaimers but retain compact state', async () => {
  const [overview, dataProducts, datasets, modelProducts, externalModels, dataGovernance, modelGovernance, results, styles] = await Promise.all([
    source('../src/roadshow/RoadshowSealPage.tsx'),
    source('../src/roadshow/DataProductLifecyclePages.tsx'),
    source('../src/roadshow/ExternalCatalogPages.tsx'),
    source('../src/roadshow/ModelProductLifecyclePages.tsx'),
    source('../src/roadshow/ExternalModelCatalogPages.tsx'),
    source('../src/roadshow/ExternalGovernancePages.tsx'),
    source('../src/roadshow/ExternalModelGovernancePages.tsx'),
    source('../src/roadshow/ResultReleasePages.tsx'),
    source('../src/styles.css'),
  ])
  assert.doesNotMatch(overview, /ENGINEERING ROADSHOW RELEASE CANDIDATE|本环境为医疗AI可信协作工程演示系统/)
  assert.doesNotMatch(dataProducts, /title="未物化，不可执行"|title="外部公共目录产品：仅提供元数据/)
  assert.match(dataProducts, /仅元数据/)
  assert.doesNotMatch(modelProducts, /title="外部公共模型，仅元数据"/)
  assert.doesNotMatch(modelProducts, /本产品当前只提供模型目录、来源和治理信息/)
  assert.match(modelProducts, /Executor 未注册/)
  assert.doesNotMatch(externalModels, /模型目录收录不代表权重已经下载/)
  assert.match(externalModels, /未物化/)
  assert.doesNotMatch(dataGovernance, /目录收录不代表来源、许可或访问条件已经核验/)
  assert.match(dataGovernance, /来源、许可与访问条件按状态分别展示/)
  assert.doesNotMatch(modelGovernance, /公开权重不等于本地已下载|元数据草稿不包含权重或执行镜像/)
  assert.doesNotMatch(modelGovernance, /<Alert/)
  assert.match(modelGovernance, /detail\.boundaries\.local_weights/)
  assert.match(modelGovernance, /detail\.boundaries\.executable \? '可执行' : '不可执行'/)
  assert.doesNotMatch(results, /title="非临床声明"/)
  assert.match(datasets, /external-catalog-filters--datasets/)
  assert.match(externalModels, /external-catalog-filters--models/)
  assert.match(styles, /\.external-catalog-filters--datasets/)
  assert.match(styles, /\.external-catalog-filters--models/)
})
