# Phase 5 路演产品化与可解释执行计划

## 1. 文档目的

本文最初是对 Phase 4 路演前端、Roadshow API、领域模型、审计模型和实际页面的只读审查结果。

实施状态：Phase 5.1 至 Phase 5.8 已完成验收。Phase 5.8 没有增加新的业务生命周期，而是以只读聚合和前端会话编排统一展示 Phase 5.1 至 5.7 的真实事实。

Phase 5 的目标不是重构 Phase 4 核心架构，而是把已经存在的真实业务链路表达为可填写、可确认、可追踪、可解释的产品流程：

```text
用户输入
→ 后端校验
→ 业务对象写入
→ 合法状态迁移
→ AuditEvent 与 Outbox
→ 对象详情、操作证据和技术证据反馈
```

## 2. 审查基线与边界

当前基线：

- Alembic：`20260725_0031`
- 业务表：51 张
- 后端回归：155 passed，2 个环境门控用例 skipped
- 前端 39 项测试、TypeScript 检查和生产构建已通过
- 四个演示身份、29 步主流程、受控执行、Artifact 隔离、多方结果审核、安全结果包和一次性下载已经存在
- 当前执行器仍为 `hard_isolation=false`

本计划不得：

- 修改历史 migration
- 绕过现有领域服务直接改状态
- 合并“审核通过”和“结果发布”两个事实
- 弱化 run count、固定版本、Artifact 隔离或下载授权
- 把前端角色切换当作授权
- 把数据库内哈希链描述为第三方可信存证
- 宣称已支持跨医院联合申请、生产级隔离或临床验证

## 3. 当前缺陷清单

### 3.1 P0：进入 Phase 5 前必须修复

1. 数据目录和模型目录在 React 开发模式下稳定显示：

   ```text
   signal is aborted without reason
   ```

   根因是 `CatalogPage` 的 effect 首次被 Strict Mode 清理时中止请求，错误状态被写入；第二次请求即使成功也没有清空错误。该问题是前端请求生命周期缺陷，不是后端目录 API 缺陷。

2. 当前 Roadshow 命令 API 不接收结构化业务请求体，只调用固定 Phase 4 演示命令创建或推进预置对象。

3. `roadshowCommand` 只发送空 POST，请求体、字段级校验错误和返回对象导航信息均不存在。

### 3.2 产品创建与编辑缺陷

- 没有真正的数据产品新建、编辑、版本新增或资源登记表单。
- 没有真正的模型产品新建、编辑、版本新增或 registry 绑定表单。
- 没有真正的计算需求草稿表单。
- “保存草稿”和“提交审核”没有分成两个独立命令。
- 没有产品、版本、申请、审核、合同、任务和结果包的稳定详情路由。
- 创建成功后只显示 toast 并刷新，不展示业务编号、对象 ID、审计事件或跳转入口。
- 页面没有并发编辑版本提示，现有 `row_version` 未进入 API 契约。

### 3.3 审批缺陷

- 审批动作只有“批准”按钮。
- 不能录入审批结论、意见、拒绝原因、整改方式和附加条件。
- 不能确认批准的数据范围、用途、运行次数和输出类别。
- 不能区分“批准原申请”与“收窄后批准”。
- Artifact 审核不能填写数据出域意见、技术质量意见或平台合规依据。
- 前端没有展示已有 `ReviewDecision`、`ArtifactReviewDecision` 的 comment、reason code、evidence 和 digest。

### 3.4 可解释性与审计缺陷

- `/workflow` 只返回最近 25 条审计事件的 sequence、type、result、occurred_at。
- 页面不展示 actor、organization、subject、command ID、correlation ID、evidence digest 或哈希链。
- 不能按当前对象过滤审计事件。
- 不能从业务详情跳转到已过滤的审计中心。
- 看不到 AuditEvent 对应 Outbox 的状态、尝试次数、发布时间和 payload digest。
- 没有“本次操作证据”面板。
- 没有“技术证据”抽屉。
- 审计中心混合了业务事实和基础设施概念，但没有建立事件到业务对象的可读映射。

### 3.5 执行与流程表达缺陷

- 执行页只展示 4 个粗粒度步骤以及 Run ID、状态和序号。
- 已存在的授权评估、执行环境、绑定 ID、dispatch/start/completion/audit receipt digest、失败码和时间戳没有展示。
- 没有把“合同生效、版本锁定、网络限制、只读挂载、运行次数、允许输出”集中展示。
- 没有明确显示“计算完成但 Artifact 仍处于 quarantined，禁止下载”。
- 29 步路线图的多项动作共用同一个完成键，不能逐动作反映真实完成情况。
- `phaseDone('download')` 固定返回 `false`，完成下载后路线图仍显示未完成。

### 3.6 页面与视觉缺陷

- 登录页的角色解释和安全边界较完整，应保留其克制风格。
- 业务页依赖统一的大型 Hero 和 Card，信息密度偏低。
- 当前目录只有一条固定演示记录且不可操作。
- 详情信息、审批进度、证据和主操作没有形成稳定布局。
- 移动端已有基础断点，但 Phase 5 新表单、证据面板和技术抽屉必须重新做响应式验收。
- 成功、加载、空状态和错误反馈缺乏统一的对象级语义。

## 4. Phase 5 信息架构

建议新增或明确以下路由：

| 对象 | 列表 | 新建 | 详情/编辑 |
|---|---|---|---|
| 数据产品 | `/data-products` | `/data-products/new` | `/data-products/:productId/versions/:versionId` |
| 模型产品 | `/model-products` | `/model-products/new` | `/model-products/:productId/versions/:versionId` |
| 计算需求 | `/applications` | `/applications/new` | `/applications/:applicationId` |
| 申请审核 | `/reviews` | 不单独新建 | `/reviews/:reviewTaskId` |
| 数字合约 | `/contracts` | 由申请生成 | `/contracts/:revisionId` |
| 执行任务 | `/execution` | 由生效合同创建 | `/execution/:runId` |
| 结果审核 | `/results` | 由 Artifact 创建计划 | `/artifacts/:artifactId/reviews` |
| 结果包 | `/result-packages` | 由审核结果生成 | `/result-packages/:packageId` |
| 审计中心 | `/audit` | 不适用 | `/audit?subjectType=...&subjectId=...` |

Phase 4 路演入口可继续存在，但应改为这些真实对象页面的导航和演示编排层，而不是独立的固定业务实现。

## 5. 页面级改造清单

### 5.1 角色工作台

- 显示当前身份、机构、真实待办和最近操作。
- 待办项直接跳转到目标对象或审核任务。
- 主链显示对象级状态，不再用一个共享完成键代表多个动作。
- “下一步”必须来自后端返回的 allowed actions，而不是前端自行推断授权。
- 保留 `hard_isolation=false` 和非临床声明。

### 5.2 数据产品

- 列表支持状态、机构、领域、版本和更新时间过滤。
- 新建使用四步表单：基本信息、数据构成与质量、使用与输出策略、节点绑定与确认。
- 详情页展示产品、当前版本、资源、来源绑定、策略摘要、发布状态和操作证据。
- 草稿状态允许编辑；提交后展示只读快照。
- 主操作按状态只保留“保存草稿”或“提交上架审核”。

### 5.3 模型产品

- 列表展示固定版本、运行时、入口、摘要、兼容性和发布状态。
- 新建使用四步表单：基本信息、版本与摘要、输入输出与兼容性、运行环境与策略。
- 不提供任意代码上传或任意入口填写。
- registry entrypoint 和 digest 必须由后端白名单校验。
- 详情页明确区分模型产品元数据、固定执行版本和不可导出的模型权重。

### 5.4 计算需求

- 新建表单允许选择已发布的数据版本和模型版本。
- 填写目的、法律或伦理依据、算法信息、使用时长、运行次数、请求动作和输出类别。
- 兼容性检查结果在提交前展示，但最终判断由后端完成。
- 保存草稿后进入申请详情；提交后进入审核进度页。
- 详情页展示申请快照 digest，防止用户误以为后续编辑会改变审核对象。

### 5.5 审批任务

- 审批表单展示被审核快照、申请范围和上一环节意见。
- 支持批准、拒绝、意见、reason code、整改方式和结构化 evidence。
- “批准范围”只能等于或小于申请范围。
- 运行次数和输出类型的收窄必须进入 decision evidence，并由合同生成服务消费。
- 审批成功后显示 decision ID、AuditEvent 和下一审核环节。

### 5.6 数字合约

- 详情页展示四方主体、固定数据版本、固定模型版本、动作、时长、run count、输出、执行绑定和审核计划。
- 四方签署独立显示 actor、时间和摘要。
- 合同激活前展示守卫检查结果，不允许前端直接修改 revision 状态。
- 合同只从已批准申请和审核事实生成。

### 5.7 受控执行

- 顶部展示执行约束：合同、固定版本、运行次数、网络、挂载、允许输出和禁止输出。
- 时间线按真实事件展示 job 创建、run 预留、派发、启动、完成和 Artifact 创建。
- 执行摘要展示 executor、模型摘要、数据摘要、处理数量、退出状态和异常码。
- Artifact 区域始终明确 release status 和下载权限。
- 页面不得展示内部存储路径、Connector 凭据或完整执行 payload。

### 5.8 结果审核与结果包

- 分开展示 Artifact、三方审核任务、审核决定和安全结果包。
- 每个审核任务使用独立表单和 evidence。
- “审核均完成”不能直接把源 Artifact 改为 released；仍由独立结果包命令生成。
- 结果包展示白名单文件、package digest、大小、状态和下载次数。
- 下载成功后刷新对象和审计状态，路线图应标记完成。

### 5.9 审计中心

- 支持 subject type、subject ID、event type、actor、result、时间范围和 command ID 过滤。
- 默认显示可读的业务事件时间线。
- 每条事件提供“查看技术证据”入口。
- 空间运营方可查看 Outbox 投递状态；其他角色只看其有权访问的业务证据。
- 基础设施计数与具体业务事件分区展示。

## 6. 表单字段与数据设计

### 6.1 数据产品表单

| 分组 | 字段 | 建议落点 | 迁移 |
|---|---|---|---|
| 基本信息 | 产品名称 | `DataProduct.name` | 否 |
| 基本信息 | 产品编号 | `DataProduct.product_code`，后端生成 | 否 |
| 基本信息 | 描述 | `DataProduct.description` | 否 |
| 基本信息 | 产品类型 | `DataProduct.product_type` | 否 |
| 基本信息 | 疾病/业务领域 | `DataProduct.domain` | 否 |
| 基本信息 | 负责人 | 默认 `created_by`；展示用户信息 | 否 |
| 基本信息 | 联系部门 | `provenance_summary.owner_department` | 否，除非需要组织级检索或权限 |
| 构成 | 病例、切片、图像数量 | `scope_metadata.counts` | 否 |
| 构成 | 图像尺寸、格式、标注 | `DataResource.schema_metadata` | 否 |
| 质量 | 缺失率、质检结论 | version/resource `quality_report` | 否 |
| 版本 | 版本号和标签 | `version_no`、`version_label` | 否 |
| 来源 | 来源类型和说明 | `provenance_summary` | 否 |
| 策略 | 允许/禁止用途 | `default_policy_template` | 否 |
| 策略 | 最大运行次数 | `default_policy_template.constraints` | 否 |
| 策略 | 允许/禁止输出 | `default_policy_template.constraints` | 否 |
| 节点 | Connector、资源别名、摘要 | `DataProductSource` | 否 |

不要为了表单展示把所有 JSON 字段立即拆成列。只有需要数据库级唯一性、外键、范围校验、权限或高频查询时才增加结构化列。

### 6.2 模型产品表单

| 分组 | 字段 | 建议落点 | 迁移 |
|---|---|---|---|
| 基本信息 | 名称、编号、描述、领域 | `ModelProduct` | 否 |
| 版本 | 版本号、标签、状态 | `ModelVersion` | 否 |
| 执行 | 固定 entrypoint | `entrypoint_id` | 否 |
| 执行 | 模型、manifest、registry digest | 现有 digest 字段 | 否 |
| 执行 | runtime | `runtime` | 否 |
| Schema | 输入/输出 schema 版本 | 现有 version 字段 | 否 |
| Schema | 完整结构 | `compatibility_metadata.schemas` | 否 |
| 兼容性 | 数据模态、尺寸、标签空间 | `compatibility_metadata` | 否 |
| 许可 | 来源、权利、限制 | `license_metadata` | 否 |
| 策略 | 允许用途与输出 | `default_policy_template` | 否 |

完整 schema 如需独立版本、数据库级引用或跨模型检索，再评估专用 schema 表；Phase 5 路演不应先做该迁移。

### 6.3 计算需求表单

| 字段 | 建议落点 | 迁移 |
|---|---|---|
| 申请编号 | `application_number`，后端生成 | 否 |
| 目的 | `purpose` | 否 |
| 法律/伦理依据 | `legal_or_ethics_basis` | 否 |
| 数据产品和版本 | `ApplicationItem` | 否 |
| 请求数据范围 | `ApplicationItem.requested_scope` | 否 |
| 模型产品和版本 | `ApplicationModelSelection` | 否 |
| 算法名称、版本、摘要 | `Application.algorithm_*` | 否 |
| 使用时长 | `requested_duration_seconds` | 否 |
| 运行次数 | `requested_run_limit` | 否 |
| 请求动作 | `ApplicationRequestedAction` | 否 |
| 输出类别 | `ApplicationRequestedOutputType` | 否 |
| 材料附件 | `ApplicationAttachment` | 否 |

注意：`aggregate_statistics` 等是业务输出类别；`confusion_matrix.csv`、`execution_summary.json` 是安全结果包文件。界面必须区分二者，不能把文件名直接写入申请输出枚举。

### 6.4 审批表单

| 字段 | 建议落点 | 迁移 |
|---|---|---|
| 结论 | `decision` | 否 |
| 拒绝原因 | `reason_code` | 否 |
| 审批意见 | `comment` | 否 |
| 整改方式 | `remediation` | 否 |
| 批准数据范围 | `evidence.approved_scope` | 否 |
| 批准用途 | `evidence.approved_actions` | 否 |
| 批准运行次数 | `evidence.approved_run_limit` | 否 |
| 批准输出 | `evidence.approved_outputs` | 否 |
| 附加条件 | `evidence.conditions` | 否 |
| 依据摘要 | `evidence.basis` | 否 |

领域服务必须验证批准范围不扩大原申请，并保证合同生成只读取审核后的交集。

## 7. 前端动作、API、状态和 AuditEvent 映射

以下业务 API 已由 Phase 5.1 至 5.7 实现；Phase 5.8 仅新增只读路演聚合，不替代这些权威写入接口。

| 前端动作 | 建议 API | 状态变化 | AuditEvent |
|---|---|---|---|
| 保存数据产品草稿 | `POST /data-products` | 无 → product/version `draft` | 新增 `data_product.version.created` |
| 更新数据产品草稿 | `PATCH /data-product-versions/{id}` | `draft` → `draft` | 可选 `data_product.version.updated`，见迁移策略 |
| 提交数据产品审核 | `POST /data-product-versions/{id}/submit` | `draft` → `under_review` | 已有 `data_product.version.submitted` |
| 批准并发布数据产品 | `POST /data-product-versions/{id}/approve` | `under_review` → `approved` → publication active | 已有 approved、published 两个事件 |
| 保存模型产品草稿 | `POST /model-products` | 无 → product/version `draft` | 新增 `model_product.version.created` |
| 更新模型产品草稿 | `PATCH /model-versions/{id}` | `draft` → `draft` | 可选 `model_product.version.updated` |
| 提交模型审核 | `POST /model-versions/{id}/submit` | `draft` → `under_review` | 已有 `model_product.version.submitted` |
| 批准并发布模型 | `POST /model-versions/{id}/approve` | `under_review` → `approved` → publication active | 已有 approved、published 两个事件 |
| 保存申请草稿 | `POST /application-drafts` | 无 → `draft` | `application.created` |
| 更新申请草稿 | `PATCH /application-drafts/{id}` | `draft` → `draft` | `application.updated` |
| 执行兼容性检查 | `POST /application-drafts/{id}/compatibility` | `draft` → `draft` | `application.compatibility.checked` |
| 提交申请 | `POST /application-drafts/{id}/submit` | `draft` → `prechecking` | `application.submitted` |
| 提交申请审核决定 | `POST /application-review-tasks/{id}/decide` | task pending/claimed → decided；application 推进 | `application.review.decided` 及终态事件 |
| 生成合同草案 | `POST /applications/{id}/contract` | 无 → revision `proposed` | `contract.draft.generated`、`contract.policy.converged`、`contract.revision.proposed` |
| 确认合同 | `POST /digital-contracts/{id}/confirm` | proposed → signed（全部确认后） | `contract.revision.signed` |
| 激活合同 | `POST /digital-contracts/{id}/activate` | signed → active | `contract.revision.activated` |
| 确认就绪 | `POST /contract-revisions/{id}/readiness` | 新增独立 readiness fact | 已有 `contract.readiness.confirmed` |
| 创建任务 | `POST /compute-jobs` | 无 → job created；run reserved | 已有 `compute.job.created`、`compute.run.reserved` |
| 派发/开始/完成 | Coordinator/Executor 回执 | reserved → dispatched → running → succeeded | 已有 dispatched、started、completed |
| 创建隔离制品 | Callback | 无 → Artifact quarantined | 已有 `artifact.created` |
| 提交结果审核 | `POST /artifact-review-tasks/{id}/decisions` | pending/claimed → decided | 已有 `artifact.multiparty_review.decided` |
| 生成结果包 | `POST /artifacts/{id}/result-packages` | 无 → package available | 已有 `result.package.created` |
| 创建下载授权 | `POST /result-packages/{id}/download-grants` | 无 → grant active | 已有 `result.download.grant.created` |
| 下载结果 | `POST /result-downloads` | grant active → exhausted | 已有 `result.download.completed` |

要求：

- API 返回 `object_id`、业务编号、状态、`event_id`、`command_id` 和 `correlation_id`。
- 状态变化必须由现有领域服务执行。
- 同一 command 重放必须返回原结果，不重复产生业务事实。
- 前端不得根据按钮点击自行假设状态已经成功。

## 8. 操作证据面板设计

### 8.1 位置

- 所有业务详情页右侧固定区域。
- 宽屏采用约 70/30 主内容布局。
- 窄屏降级为主内容后的可折叠区域。

### 8.2 顶部状态

展示三个独立状态，不合并为一个“成功”：

- 业务写入：成功/失败
- 审计事实：已写入/未写入
- 消息投递：pending/processing/published/dead letter

### 8.3 最近事件

默认显示与当前 subject 关联的最近 5 条事件：

- 可读动作名称
- actor 机构和用户
- 发生时间
- 对象编号
- 结果
- 状态变化摘要
- evidence digest 验证结果
- Outbox 状态

提供：

- “查看完整审计链”
- “复制业务编号”
- “查看技术证据”

### 8.4 数据来源

新增对象级只读查询：

```text
GET /audit-events?subject_type=...&subject_id=...&limit=5
```

API 通过 AuditEvent 与 OutboxMessage 关联查询，不复制审计数据到新业务表。

## 9. 技术证据抽屉设计

### 9.1 可持久展示的现有事实

- Event ID
- event type 和 schema version
- command ID
- correlation ID
- causation ID
- actor type、organization ID、user ID、connector ID 或 service code
- subject type 和 subject ID
- evidence digest
- previous event digest
- current event digest
- 哈希链验证结果
- Outbox message ID、topic、destination、status、attempt count
- payload digest、published time、last error

### 9.2 禁止展示

- 完整敏感 payload
- 下载 token
- Connector 凭据
- 对象存储内部路径
- 原始数据路径
- 模型权重路径
- 请求头中的认证信息

### 9.3 HTTP 与链路遥测的诚实边界

当前 AuditEvent 没有持久化以下字段：

- API endpoint
- HTTP status
- request duration
- 独立 trace ID

Phase 5 基础版本不得把这些字段伪装成已存在的审计事实。

建议：

- 将 `correlation_id` 作为业务链路关联 ID 展示，不改名为“已接入分布式追踪”。
- endpoint、HTTP status 和 duration 只在当前命令响应中作为临时操作回执展示。
- 如果要求刷新后仍能查询这些 HTTP 事实，应另行设计 `command_receipts` 或遥测存储，并执行新 migration、脱敏和保留期评审。

## 10. 统一 UI 规范

### 10.1 页面模板

```text
顶部：对象名称 + 业务编号 + 状态 + 一个主操作
次级信息：参与机构 + 创建时间 + 当前负责人
主体左侧：业务字段、版本、流程和审核
主体右侧：审核进度 + 操作证据
底部：对象级审计时间线
```

### 10.2 交互规范

- 每页一个主操作，其余使用次级按钮或更多菜单。
- 保存草稿和提交审核必须是两个明确动作。
- 破坏性、拒绝和撤回动作必须二次确认。
- 表单错误定位到字段，不只显示顶部 toast。
- 后端 409 应展示业务冲突原因和当前权威状态。
- 成功反馈必须包含业务编号和“查看详情”。
- 长命令显示进行中状态，但不得乐观修改业务状态。

### 10.3 视觉规范

- 减少大面积 Hero 和空白卡片，提升结构化字段密度。
- Card 圆角不超过 8px；页面区域不做嵌套卡片。
- 蓝色表示常规信息，绿色表示已验证，黄色表示待办，红色表示拒绝或阻止，灰色表示不可用。
- 合同和策略不依赖单一紫色语义，使用图标、标题和字段共同表达。
- 状态颜色必须同时配文字，不能只依赖颜色。
- digest、ID 和代码使用等宽字体，并支持复制。
- 桌面、平板和 390px 移动宽度均不得出现按钮、表格或长摘要溢出。

### 10.4 路演模式

Roadshow Mode 只提供：

- 表单示例值预填
- 当前演示步骤提示
- 快速切换到下一责任角色
- 一键打开当前对象审计
- 一键复制业务编号

它不能：

- 跳过后端校验
- 直接写状态
- 伪造 AuditEvent
- 绕过审核、签署、run count 或结果隔离

## 11. 分阶段实施顺序

### Stage 0：稳定当前基线

范围：

- 修复目录页 AbortError 残留。
- 为所有 GET 页面统一 loading、abort 和 stale response 处理。
- 冻结 Phase 5 API/状态/AuditEvent 映射。

测试与验收：

- React Strict Mode 下目录页无错误边界。
- 快速切换路由和身份不出现旧请求覆盖新状态。
- 当前 Phase 4 137 个后端测试和前端构建继续通过。

回滚：

- 仅回滚前端请求 hook，不涉及业务数据。

### Stage 1：只读列表、详情和证据查询

范围：

- 增加对象列表与详情 API。
- 增加 subject 过滤的审计查询和 Outbox 关联查询。
- 建立稳定详情路由。
- 不开放写入表单。

测试与验收：

- 四角色只能读取其权限内对象。
- 详情字段与数据库事实一致。
- subject 过滤不串对象、不串空间。
- 技术抽屉不返回敏感 payload 和内部路径。

回滚：

- 路由 feature flag 关闭；无数据迁移。

### Stage 2：数据产品和模型产品表单化

范围：

- 新建、编辑草稿、提交审核。
- 后端生成编号和 digest。
- 创建后跳转详情并显示操作证据。
- 增加草稿创建 AuditEvent 词汇。

测试与验收：

- 必填、枚举、digest、Connector 和 registry 校验覆盖。
- 保存草稿不触发提交状态。
- 提交后草稿字段不可静默修改。
- 幂等重放不重复创建产品、版本、事件或 Outbox。
- 创建成功返回业务编号、对象 ID 和 event ID。

回滚：

- 先关闭写入口。
- 新建草稿保留，不删除历史事实。
- migration 只允许向前修复，不回写历史 migration。

### Stage 3：计算需求和审批表单化

范围：

- 申请草稿、数据/模型选择、范围、次数和输出。
- 三方申请审核表单。
- 审核意见和批准范围进入现有 decision evidence。

测试与验收：

- 只能选择已发布且兼容的固定版本。
- 审批范围不能扩大申请。
- 任一强制审核拒绝后，下游合同创建被阻止。
- 刷新后申请、快照和审核意见保持一致。
- 合同生成读取批准交集而不是原申请原值。

回滚：

- 关闭新表单入口；保留已提交快照和审核事实。

### Stage 4：合同、就绪和执行可解释化

范围：

- 合同详情、签署证据、激活守卫。
- 就绪证据详情。
- 执行约束、事件时间线、receipt digest 和 Artifact 隔离说明。

测试与验收：

- 未全部签署不得激活。
- 未完成三类 readiness 不得创建任务。
- run count 并发预留仍保持原子性。
- 执行页状态来自 Job、Run、Artifact 和 AuditEvent，不来自前端计时器。
- 失败、中断和超时有明确终态及错误码。

回滚：

- 只读展示可单独关闭；不回滚执行状态。

### Stage 5：结果审核、结果包和下载闭环

范围：

- 三类 Artifact 审核表单。
- 结果包详情和白名单文件。
- 下载完成状态和路线图闭环。

测试与验收：

- 强制审核未全部批准时不能生成结果包。
- 模型技术确认不能替代医院出域审核或平台合规审核。
- 源 Artifact 保持 quarantined。
- ZIP 只包含白名单文件。
- token 一次性、短时、绑定请求方。
- 下载成功产生 `result.download.completed`，刷新后路线图完成。

回滚：

- 禁用新授权创建；已消耗 token 不恢复。
- 结果包对象和审计事实不删除。

### Stage 6：统一 UI 与 Roadshow Mode

范围：

- 统一详情模板、表单组件、状态标签、空状态和成功回执。
- 增加非绕过式 Roadshow Mode。
- 完成桌面和移动端视觉验收。

测试与验收：

- 390px、768px、1440px 无重叠和横向溢出。
- 键盘操作、焦点、标签和错误提示可用。
- 四角色完成完整流程时无需手工拼 URL。
- Roadshow Mode 关闭后业务功能仍完整可用。

回滚：

- 通过 feature flag 退回标准导航，不影响后端事实。

## 12. 不需要数据库变化的项目

- 修复目录页 AbortError。
- 新增列表、详情和 allowed actions API。
- 表单 DTO、字段校验和错误响应。
- 数据产品大部分业务字段写入现有列和 JSON。
- 模型产品大部分业务字段写入现有列和 JSON。
- Application、ApplicationItem、ApplicationModelSelection、动作、输出和附件写入。
- ReviewDecision 和 ArtifactReviewDecision 表单化。
- 合同、就绪、执行和结果详情展示。
- subject 过滤的 AuditEvent 查询。
- AuditEvent 与 OutboxMessage 关联展示。
- 操作证据面板。
- 只展示现有持久字段的技术证据抽屉。
- 统一 UI、详情路由、响应式布局和 Roadshow Mode。
- 下载完成后的前端状态刷新和路线图修正。

## 13. 确实需要新 migration 的项目

### 13.1 Phase 5 基础范围必需

草稿创建如果要成为第一类不可变业务事实，必须扩展受数据库 CHECK 约束的 AuditEvent 词汇。

最小建议新增：

```text
data_product.version.created
model_product.version.created
application.created
```

可继续使用现有 subject types：

```text
data_product_version
model_version
application
```

创建产品和首个版本可作为一个聚合命令，由 version-created 事件 evidence 记录 product ID。这样不必立即新增 `data_product` 和 `model_product` subject type。

如果产品壳和版本被拆成两个独立可审计命令，则还需要新增：

```text
event types:
data_product.created
model_product.created

subject types:
data_product
model_product
```

### 13.2 仅在需求确认后迁移

以下项目不是 Phase 5 路演基础范围的必需迁移：

- 草稿每次编辑都产生不可变 `*.updated` AuditEvent。
- 联系部门、业务负责人等需要独立索引、权限或报表的结构化列。
- 输入/输出 schema 需要独立版本和数据库级引用。
- HTTP endpoint、status、duration 等命令回执需要刷新后持久查询。
- 审计事件需要保存专用前后状态列，而不是从 evidence 中读取。

这些需求应分别设计 migration，不能为了“以后可能使用”一次性加入。

## 14. 风险与回滚方案

### 14.1 状态机回归

风险：CRUD API 绕过领域服务，造成非法状态。

控制：

- API 只调用领域服务。
- 写接口按状态做后端守卫。
- 增加非法迁移、并发和幂等测试。

回滚：

- 关闭写 feature flag；保留已产生的合法事实。

### 14.2 JSON 字段失控

风险：为了避免 migration，把无版本结构随意写入 JSON。

控制：

- 每个 JSON 文档有 `schema_version`。
- 使用 Pydantic DTO 和 canonical digest。
- 稳定查询字段才升级为结构化列。

### 14.3 审计词汇漂移

风险：UI 使用自创事件名，与数据库词汇不一致。

控制：

- 单一事件词汇注册表。
- API、前端标签和测试从同一映射生成或校验。
- migration、ORM 常量和审计服务同批提交。

### 14.4 敏感信息泄露

风险：技术抽屉暴露 payload、路径、token 或凭据。

控制：

- 服务端白名单响应 DTO。
- 禁止前端接收后再隐藏。
- 增加敏感字段回归扫描。

### 14.5 前端乐观状态误导

风险：按钮点击后 UI 显示成功，但事务或 Outbox 未完成。

控制：

- 命令响应返回 authoritative state 和 event ID。
- 面板分别显示业务写入、审计写入和投递状态。
- pending Outbox 不显示为“全部完成”。

### 14.6 现有演示状态污染

风险：表单化创建大量记录，使固定路演脚本无法复现。

控制：

- Roadshow Mode 使用独立演示空间或明确 `is_demo` 数据。
- 重置脚本只清理专用演示数据库和精确结果 bucket。
- 普通 CRUD 与固定演示 seed 分离。

### 14.7 数据库与对象存储非原子

风险：结果包对象已写入但数据库事务回滚，产生孤立对象。

控制：

- 保留现有诚实边界。
- 使用精确前缀的垃圾回收和对账任务。
- 不在回滚中删除未知对象。

## 15. 完成定义

Phase 5 完成后应满足：

- 用户能真实填写并保存数据产品、模型产品和计算需求草稿。
- 保存草稿与提交审核是独立命令。
- 每个关键动作都有明确 API、合法状态变化和 AuditEvent。
- 创建成功能看到业务编号、详情页和本次操作证据。
- 审批人能填写意见、原因、批准范围和附加条件。
- 合同只包含申请与审核允许范围的交集。
- 执行页能解释约束、派发、运行、回执、Artifact 隔离和结果审核。
- 审计中心能按对象过滤，并提供脱敏技术证据。
- 结果包和下载仍保持白名单、短期、一次性和可审计。
- 所有现有安全边界、状态机和 Phase 4 回归测试保持成立。

Phase 5.1 至 Phase 5.7 已按独立授权完成，包括 Artifact 多方审核、独立三文件 Release Package 和一次性下载拒绝审计。后续阶段仍需新的明确授权，不自动进入 Phase 5.8、计费、任意执行、生产隔离或真实医院接入。
