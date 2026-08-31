# Phase 4 多主体路演业务模型冻结

日期：2026-07-23
状态：Stage 1 通过，可进入 Stage 2
基线：Alembic `20260722_0020`，38 张实表

## 1. 冻结结论

Phase 4 不是把现有 PathMNIST 固定演示任务包装成更多按钮，而是在不破坏既有证据链的前提下，补齐四类主体共同参与的业务闭环：

```text
空间运营方
  ├─ 审核数据产品/模型产品上架
  ├─ 完成申请预审与平台合规审查
  └─ 执行合同就绪检查

数据提供方
  ├─ 发布数据产品元数据
  ├─ 审核数据使用范围
  ├─ 确认数据侧就绪
  └─ 强制参与结果出域审查

模型提供方
  ├─ 发布固定白名单模型产品
  ├─ 审核模型使用范围
  ├─ 确认模型侧就绪
  └─ 按合同要求参与结果质量审查

数据需求方
  ├─ 选择一个或多个具体 DataProductVersion
  ├─ 选择一个具体 ModelVersion
  ├─ 提交计算需求、用途与期望输出
  └─ 仅下载审核通过的安全结果包
```

冻结的角色代码为：

- `space_operator`
- `data_provider`
- `model_provider`
- `data_requester`

历史代码 `operator/provider/consumer/service_provider` 保留用于旧证据和旧基线；新路演流程只使用上述四个上下文角色。角色必须由后端根据固定演示身份解析并校验其 SpaceParticipant 关系，前端切换菜单不构成授权。

## 2. 对既有对象的复用边界

| 既有对象 | Phase 4 继续承担的权威含义 | 不允许承担的新含义 |
| --- | --- | --- |
| DataProduct / Version / Resource / Source / Publication | 数据产品身份、不可变版本、来源 Connector 和目录可发现性 | 不存真实患者数据、真实路径或下载入口 |
| Application / Item / Snapshot | 一次计算需求及其数据版本、用途、输出和提交证据 | 不再以自由文本算法字段作为模型权威来源 |
| ReviewTask / ReviewDecision | ApplicationSnapshot 的准入审核事实 | 不用于 Artifact 出域审核 |
| Contract / Revision / Party / Object / Policy | 审核通过范围转换成机器可执行合同 | 不以名称或当前产品替代具体版本 |
| ComputeJob / Run | 固定合同下的计算意图和每次运行事实 | 不保存结果审核或下载状态 |
| Artifact | Run 产生且默认隔离的制品 | Run 成功不等于可发布 |
| ArtifactReview（旧表） | 保留 Phase 2/3 单审核历史证据 | Phase 4 不再向此表写入多方结果审核 |
| ModelRegistry（代码内） | 固定 entrypoint、摘要、运行时和输出白名单的执行许可 | 不作为业务目录、上架审批或所有权事实源 |

## 3. 最小新增领域对象

### 3.1 模型产品目录

新增三表：

1. `model_products`：稳定的模型产品身份，绑定 Space 和模型提供方。
2. `model_versions`：不可变模型版本，固定模型摘要、manifest 摘要、注册摘要、固定 entrypoint、输入输出 Schema、许可、适用模态和默认使用策略。
3. `model_publications`：目录发布事实，独立于版本生命周期。

ModelVersion 与代码内 ModelRegistry 的唯一合法连接为：

```text
model_versions.entrypoint_id
+ model_versions.model_digest
+ model_versions.registry_digest
```

三项必须与代码内白名单登记精确一致。数据库记录不能注册任意 Python 路径、Shell 命令、代码包或宿主机模型路径。

### 3.2 计算需求中的模型选择

新增 `application_model_selections`，每个 Application 恰好选择一个具体 ModelVersion，并冻结：

- model_product_id / model_version_id；
- model_provider_organization_id；
- model_snapshot_digest；
- requested_model_policy_digest；
- manifest/registry 证据摘要。

现有 ApplicationItem 继续承载一个或多个 DataProductVersion。V1 一次需求仍由现有 Application.provider_organization_id 约束为同一数据提供组织；跨多个数据提供组织的联合需求不在本轮伪装支持，未来需要独立的多提供方需求聚合设计。

### 3.3 合同中的模型对象

新增 `contract_model_objects`，绑定 ContractRevision 和唯一 ModelVersion。合同必须同时固定：

- 数据对象：ContractObject → DataProductVersion；
- 模型对象：ContractModelObject → ModelVersion；
- 主体：数据提供方、模型提供方、需求方、运营见证方；
- Action / Output / run_count / 有效期 / 执行环境；
- 结果审核计划和安全结果文件白名单。

合同只能收窄 ApplicationSnapshot 中的数据、模型、Action 和 Output，不得扩大。

### 3.4 执行前就绪确认

新增 `contract_readiness_confirmations`。每个 active Revision 的就绪事实按类型保存：

- `data_ready`：数据提供方确认具体 DataProductVersion 与 Connector 就绪；
- `model_ready`：模型提供方确认具体 ModelVersion 与固定 registry 登记就绪；
- `platform_ready`：运营方重新校验合同、Policy、Binding、Connector、Capability、数据/模型摘要及前两项确认。

确认是追加式证据，旧确认不覆盖。若合同、版本、Binding 或能力摘要变化，平台就绪检查必须重新生成新证据。没有三项当前有效确认，不允许创建新的正式 ComputeJob。

### 3.5 多方 Artifact 审核

新增两表，避免污染旧 `artifact_reviews`：

1. `artifact_review_tasks`：独立审核任务；
2. `artifact_review_decisions`：每任务最多一个、禁止 UPDATE/DELETE 的终态决定。

审核类型冻结为：

- `data_provider_egress_review`：必需；
- `platform_compliance_review`：必需；
- `model_provider_quality_review`：由合同审核计划决定是否必需。

审核对象始终是具体 Artifact 的 content digest。人工通过只能收窄 Policy，不能覆盖 deny。拒绝后不得修改原 Artifact 再审，必须产生新 Run 或新 Artifact。

### 3.6 安全结果包和受控下载

新增两表：

1. `approved_result_packages`：审核汇总与当前有效性校验通过后生成的独立安全结果包；
2. `result_download_grants`：绑定需求组织、用户、结果包、过期时间、最大次数和 token digest 的短期授权。

结果包只允许包含：

- `aggregate_metrics.json`
- `confusion_matrix.csv`
- `execution_summary.json`
- 经批准的演示报告（PDF）

结果包必须写入独立 MinIO release bucket；`Artifact.storage_reference` 仍是隔离区引用。下载接口只接受服务端生成的随机 token，数据库只保存 token digest，不返回对象存储密钥、预签名 URL、宿主机路径或原始 quarantine 引用。每次授权、下载和拒绝均写 AuditEvent/Outbox。

## 4. 审核与状态编排

### 4.1 产品上架

```text
provider draft
→ submit listing review
→ operator approves
→ approved immutable version
→ publication created
```

目录仅暴露元数据、版本摘要、用途策略、许可、质量和来源节点状态；不开放资源本体。

### 4.2 计算需求审核

```text
Application draft
→ freeze Snapshot（数据版本 + 模型版本 + Action + Output + 附件）
→ application_precheck
→ data_provider_review ─┐
→ model_provider_review ├─ parallel after precheck
→ compliance/ethics ────┘ optional by frozen routing evidence
→ all required decisions approved
→ eligible for Contract draft
```

现有 ReviewTask 类型增加 `data_provider_review` 和 `model_provider_review`；旧 `provider_review` 只为旧基线保留。任何主体不能审批自己的需求，也不能替代其他责任组织。

### 4.3 合同和就绪

```text
eligible snapshot
→ multiparty ContractRevision draft
→ propose / signatures
→ active
→ data_ready + model_ready
→ platform_ready
→ ComputeJob allowed
```

`active` 仍只表示合同生效，不表示资产已集中上传或计算已经执行。数据与模型继续由各自 Connector/固定 registry 准备，不要求重新上传到平台中央。

### 4.4 结果审核和下载

```text
Run succeeded
→ Artifact quarantined
→ required ArtifactReviewTasks
→ all required Decisions approved
→ current authority + Policy deny recheck
→ safe package assembled in release bucket
→ bounded download grant
→ audited download
```

Artifact 本身不因审核通过而改写为原始数据可下载。Phase 4 的 `released` 只表示独立安全结果包已经可由受约束的需求方下载，不表示 quarantine Artifact、原始图像、特征或模型权重对外开放。

## 5. 数据权利与合规表述

路演文案冻结为：

- 个人/患者依法享有其个人信息相关权益；
- 医疗机构仅在合法基础、授权范围和治理职责内作为数据持有、管理或处理主体；
- 数据需求方是合同与 Policy 限定范围内的授权使用方，不取得原始数据所有权；
- 平台是空间运营和可信流通治理方，不因目录登记或执行编排取得数据所有权；
- 去标识化不当然等于匿名化，不以“已脱敏”替代合法性、最小必要和用途限制判断。

本工程只使用公开 PathMNIST 演示资产，不作真实医院接入、患者授权、临床用途或生产合规结论。

## 6. 数据库增量计划

历史迁移 `0001`—`0020` 不修改。建议分三个可回滚增量：

| Migration | 新表/变更 | 预计表数 |
| --- | --- | ---: |
| `0021_phase4_catalog_demand` | 角色/审核词表增量；model_products、model_versions、model_publications、application_model_selections、contract_model_objects | 43 |
| `0022_phase4_readiness_reviews` | contract_readiness_confirmations、artifact_review_tasks、artifact_review_decisions | 46 |
| `0023_phase4_result_packages` | approved_result_packages、result_download_grants | 48 |

所有新事实表使用 UUID、UTC 时间、明确 CHECK/FK/唯一约束、RESTRICT 删除策略和必要的数据库不可变守卫。不为迁移编号或“看起来完整”增加平行状态表。

## 7. API 与前端冻结

后端只提供显式命令，不提供通用 PATCH：

- 发布/送审/批准数据产品和模型产品；
- 创建/提交计算需求；
- 领取并决定指定审核任务；
- 生成/提案/签署/激活合同；
- 数据、模型和平台就绪确认；
- 创建固定白名单 ComputeJob/Run；
- 多方 Artifact 审核；
- 生成安全结果包、创建短期下载授权和受审计下载。

API 模式的四角色工作台均从后端固定身份上下文读取组织、角色和权限；Mock 模式保留用于 Phase 1 回顾，但不能驱动真实状态。

## 8. 安全边界与停止条件

- `hard_isolation=false`；本地固定执行器不是生产级沙箱或隐私计算。
- 不允许任意模型、代码、Shell、Python 路径或数据路径输入。
- 不允许原图、患者级结果、原始特征、模型权重、凭据、访问令牌或本地路径进入 API、Audit 或结果包。
- 不允许前端直接写状态，不允许关闭触发器，不允许修改历史 migration。
- 若多方流程只能通过覆盖旧 Decision、放宽 Policy deny、绕过合同/就绪或把本地路径伪装成下载完成，则立即停止。

## 9. Stage 1 验收

Stage 1 通过。上述 10 张新增表是当前最小、无重复真相源的实现边界。特别保留了三条诚实限制：V1 单需求只覆盖一个数据提供组织、Phase 4 只登记固定 PathMNIST 模型、结果下载仅针对经过重打包的聚合安全制品。
