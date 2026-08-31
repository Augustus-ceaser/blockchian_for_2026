import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) => readFile(new URL(path, import.meta.url), 'utf8')

test('application workflow removes generic boundary chrome while preserving blockers and privacy guidance', async () => {
  const [page, assistant] = await Promise.all([
    source('../src/roadshow/ApplicationLifecyclePages.tsx'),
    source('../src/roadshow/RoleAssistant.tsx'),
  ])

  assert.doesNotMatch(`${page}\n${assistant}`, /工程演示边界|推荐不等于授权|不会自动训练模型|不用于个人诊疗或临床决策|hard_isolation=false/)
  for (const required of [
    '总体结果：${report.overall}',
    '当前存在 BLOCKER，不能提交。',
    '等待前序审核',
    '无权创建计算需求',
  ]) assert.ok(page.includes(required), required)
  assert.match(assistant, /请勿输入姓名、病历号、联系方式或其他患者身份信息/)
})

test('roadshow health and LAN join omit demo boundary presentation chrome', async () => {
  const [roadshow, join] = await Promise.all([
    source('../src/roadshow/RoadshowExperiencePage.tsx'),
    source('../src/roadshow/JoinPage.tsx'),
  ])

  assert.doesNotMatch(roadshow, /边界声明|Phase 5\.8 路演模式|hard_isolation=false|非临床验证|非生产级硬隔离/)
  assert.doesNotMatch(join, /工程演示边界|仅演示数据|非临床|hard_isolation=false|LAN Roadshow/)
  assert.doesNotMatch(join, /<Alert/)
  assert.match(join, /局域网访问入口/)
})

test('product and execution pages remove repeated static boundary prose', async () => {
  const [dataProducts, modelProducts, execution] = await Promise.all([
    source('../src/roadshow/DataProductLifecyclePages.tsx'),
    source('../src/roadshow/ModelProductLifecyclePages.tsx'),
    source('../src/roadshow/ExecutionReadinessPages.tsx'),
  ])

  assert.doesNotMatch(dataProducts, /四步向导只登记产品元数据|不允许前端伪造|演示数据边界|title="当前工程边界"|title="公开目录可见"|title="能力边界"|目录整理方/)
  assert.doesNotMatch(modelProducts, /只登记固定白名单模型|label="工程演示"|title="固定资产绑定"|title="固定许可边界"|title="公开目录可见"|工程演示，不用于诊断|title="固定安全限制"|上架不代表需求企业/)
  assert.doesNotMatch(execution, /工程演示边界：hard_isolation=false|hard_isolation=false|非生产级硬隔离/)
  for (const required of ['操作未完成', '运营方已退回补充', '提供方就绪尚未完成', 'Artifact 状态：']) {
    assert.ok(dataProducts.includes(required) || modelProducts.includes(required) || execution.includes(required), required)
  }
})

test('evidence and technical pages keep real state without generic disclaimer alerts', async () => {
  const [evidence, plans, connectors, policy, results] = await Promise.all([
    source('../src/roadshow/DatasetModelEvidencePages.tsx'),
    source('../src/roadshow/MaterializationPlanPages.tsx'),
    source('../src/roadshow/ConnectorControlPages.tsx'),
    source('../src/roadshow/PolicyControlPages.tsx'),
    source('../src/roadshow/ResultReleasePages.tsx'),
  ])

  assert.doesNotMatch(evidence, /证据级别必须按关系分别解读|静态证据不代表已运行|仅用于工程流程验证/)
  assert.doesNotMatch(plans, /当前没有可批准候选|本计划尚未下载任何数据或模型/)
  assert.doesNotMatch(connectors, /第一层 · Connector|第二层 · 独立 Executor|中央仅登记医院签名的验证摘要|Central metadata mirror only|Central approved metadata mirror/)
  assert.match(connectors, /仅显示一次/)
  assert.doesNotMatch(policy, /title="Authorization control only"/)
  assert.doesNotMatch(results, /非临床声明|label="hard_isolation"/)
  for (const required of ['无法读取结果中心', 'Artifact 状态：', '结果包可用']) {
    assert.ok(results.includes(required), required)
  }
})
