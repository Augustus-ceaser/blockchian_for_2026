# Phase 5.12.7-Z2E 前端从零完整业务链验收

## 1. 状态

- `frontend_zero_to_end_complete=true`
- tested commit：`097ac29393323b17c69cf3cd894814a70de0c5f6`
- implementation commit：无；本轮没有修改产品代码
- documentation commit：本报告所在提交，提交信息为 `docs: accept front-end zero-to-end business flow`
- environment：独立 Compose project `medtrust-z2e`，Gateway `127.0.0.1:18080`
- canonical protected：`true`
- direct API writes：`0`
- manual SQL：`0`
- Fake Executor：`false`
- simulation：`false`
- `hard_isolation=false`：保持工程原型边界，不代表生产隔离或临床能力
- tag：未创建；`v0.13`和`v0.13-roadshow-evidence-rc`均等待用户批准

## 2. Frontend Capability Map

完整入口、按钮、角色和自动动作分类见
`docs/roadshow/z2e/FRONTEND-WORKFLOW-CAPABILITY-MAP.md`。

| # | 步骤 | 前端页面/入口 | 角色 | 结果 | API 旁路 |
|---:|---|---|---|---|---|
| 1 | 新建 Application | `/applications` → `/applications/new` | requester | 通过 | 否 |
| 2 | 选择 DataProduct | `/applications/new` 第 1 步 | requester | 通过 | 否 |
| 3 | 选择 ModelProduct | `/applications/new` 第 2 步 | requester | 通过 | 否 |
| 4 | 填写用途与范围 | `/applications/new` 第 3、4 步 | requester | 通过 | 否 |
| 5 | 提交 Application | `/applications/new` 第 5 步 | requester | 通过 | 否 |
| 6 | Hospital 审核 | `/applications/:applicationId` | hospital | 通过 | 否 |
| 7 | Model Provider 审核 | `/applications/:applicationId` | model provider | 通过 | 否 |
| 8 | Operator 预审 | `/applications/:applicationId` | operator | 通过 | 否 |
| 9 | 生成 Contract | `/applications/:applicationId` | operator | 通过 | 否 |
| 10 | 四方确认并激活 | `/contracts/:contractId` | 四个业务角色 | 通过 | 否 |
| 11 | 查看 Readiness | `/execution/:contractId` | 四个业务角色 | 通过 | 否 |
| 12 | 数据 Ready | `/execution/:contractId` | hospital | 通过 | 否 |
| 13 | 模型 Ready | `/execution/:contractId` | model provider | 通过 | 否 |
| 14 | Executor/策略检查 | `/execution/:contractId` | operator | 通过 | 否 |
| 15 | 创建 ComputeJob | `/execution/:contractId` | requester | 通过 | 否 |
| 16 | 派发 Job | `/execution/:contractId` | operator | 通过 | 否 |
| 17 | 查看 ComputeRun | `/execution/:contractId` | 四个业务角色 | 通过，系统自动执行 | 否 |
| 18 | 查看 Artifact | `/results/:artifactId` | 四个业务角色 | 通过 | 否 |
| 19 | 三方结果 Review | `/results/:artifactId` | hospital/model/operator | 通过 | 否 |
| 20 | 生成 ReleasePackage | `/results/:artifactId` | operator | 通过 | 否 |
| 21 | 创建 DownloadGrant | `/results/:artifactId` | requester | 通过 | 否 |
| 22 | 首次下载 | `/results/:artifactId` | requester | HTTP 200 | 否 |
| 23 | 重复下载拒绝 | `/results/:artifactId` | requester | HTTP 409 | 否 |
| 24 | 查看 Audit chain | `/results/:artifactId`、`/audit` | 四个业务角色 | 通过 | 否 |

所有必要人工步骤都有前端入口；系统自动步骤均由前序前端命令合法触发。

## 3. New Chain

- Z2E 标识：`Z2E-20260728-C8B97EDA`
- Application：`5a131140-7619-5e52-962e-c874e0906944`，编号 `APP-5A131140`
- Contract：`7635e4f1-04a8-5510-a510-af5982c6b125`，编号 `CON-5A131140`
- ComputeJob：`f8939eed-61c0-419e-b996-bcca19e82f48`
- ComputeRun：`13c22433-3f1c-446a-a4f5-3a314c020839`
- Artifact：`cb6f10f0-bb27-462c-89e7-99afb443a0cf`
- ReleasePackage：`71e8553d-88d6-4f75-9149-df8c5fadd4d8`
- DownloadGrant：`c8eead47-ae72-477e-8a12-ac10eb83fc3f`

Application 于 `2026-07-28T02:44:17.627757+00:00`由前端创建，于
`2026-07-28T02:46:06.889773+00:00`提交，最终状态为 `approved`。
它选择：

- 数据：`结直肠组织病理分类数据产品（公开验证） v1.0`
- DataProductVersion：`c1aba304-9572-5af5-bd37-c8e737857746`
- 模型：`PathMNIST ResNet-18病理分类模型 v1.0`
- ModelProductVersion：`14942c18-c38a-5e35-b3cf-ced2e14b5062`
- 固定样本数：20
- 输出：聚合指标、混淆矩阵、执行摘要

没有选择或下载 CPTAC-COAD、CAMELYON17、HyperKvasir、CONCH、UNI 或
Prov-GigaPath。没有新增数据产品、模型产品、关系、证据、治理审核或物化计划。

## 4. Roles

- requester：创建、保存并提交申请；确认合约；创建 Job；获取一次性结果
- hospital：独立完成数据使用审核、合约确认、数据 Ready 和结果出域审核
- model provider：独立完成模型使用审核、合约确认、模型 Ready 和结果质量审核
- operator：独立完成平台预审、合约生成/确认/激活、资格检查、派发、结果合规审核和安全包生成
- curator：作为第五个独立已认证 Context 存在，不执行越权业务写入
- session isolation：5 个独立浏览器 Context，Cookie 不共享，角色串号为 0
- self-approval blocked：requester 详情页不存在自审批准入口

Application 三条正式审核分别为：

| 顺序 | 角色 | 类型 | 决定时间 | 结果 |
|---:|---|---|---|---|
| 10 | operator | `application_precheck` | `2026-07-28T02:48:18.519300+00:00` | approved |
| 20 | hospital | `data_provider_review` | `2026-07-28T02:48:21.116921+00:00` | approved |
| 30 | model provider | `model_provider_review` | `2026-07-28T02:48:23.766748+00:00` | approved |

Contract revision 1 的四方均确认同一 digest：
`sha256:57f78c17fe5709a36c94661d18cace7052cd9fe55c5b1b045b83bf08a0a0d2d3`。
合约于 `2026-07-28T02:48:34.280410+00:00`激活。

## 5. Execution

- data ready：通过前端确认
- model ready：通过前端确认
- platform/executor/policy/contract readiness：通过
- eligibility snapshot：`10e9f5a1-b6e2-4626-a026-3da527920813`
- blocker count：`0`
- real execution：`true`
- adapter：`LocalBuiltInExecutorAdapter`
- device：CPU
- Fake Executor / simulation：`false / false`
- Run status：`succeeded`
- started：`2026-07-28T02:53:02.878009+00:00`
- finished：`2026-07-28T02:53:12.413841+00:00`
- duration：`9.535832`秒
- sample / processed / failed：`20 / 20 / 0`
- correct / accuracy：`19 / 0.95`
- mean confidence：`0.960102856159`
- inference seconds：`0.261066902007`
- execution environment digest：`sha256:c98a761066e4e3ffa06751a53338633c3813aa9ee7f3ba58c286bd572c43c41f`
- output digest：`sha256:2d8082cb91f4464a0d77b11641180c6d5fa3bfb8f8abff409349d4f1130439f8`
- network access：`false`
- dataset digest unchanged：`true`
- model digest verified：`true`
- callbacks：`execution.started`与`execution.completed`均为 `completed`

该结果来自新 Run 的只读证据，不是历史 `0.95`结果的硬编码复用。

## 6. Release

- Artifact status：`quarantined`
- raw Artifact download：不允许
- result reviews：`3/3 approved`
- ReleasePackage status：`available`
- Package digest：`sha256:2db3c6416882f418bfd48ed5a954ae9b42a55f8995651e6a4ccc69ee8594c54a`
- DownloadGrant：`exhausted`，`1/1`
- first download：HTTP `200`
- repeat download：HTTP `409`，原因 `duplicate_or_exhausted`

安全结果包仅包含：

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `aggregate_metrics.json` | 417 B | `34a5b5acc62889e8f492ecb886e4a75245a6e3f00353a9bf5079ddedc54f2605` |
| `confusion_matrix.csv` | 461 B | `4f4b57502a4bc4fceba24b160cbb3652fa869ac44ac7a0dec900adfc360ddf3e` |
| `execution_summary.json` | 967 B | `d3fc7dcd717df07aada8afe483fa3f69502959c79f03ee3f3512be0bea999d62` |

ZIP 为 1276 B，文件摘要与 Package digest 一致。包内没有原始图像、模型权重、
中间特征、单样本敏感结果或额外文件。

Z2E MinIO 从 30 增至 34：3 个隔离输出文件和 1 个批准结果包；既有 30 个对象
无缺失、无摘要变化。该变化仅存在于 Z2E 独立存储。

审计链最终：

- head sequence：`390`
- head event：`result.download.rejected`
- head digest：`sha256:f0e6c0d6fc05747e16a7176c01a9f674b7e17c67f04abbdd4090c52793f14912`
- chain valid：`true`
- result 事件 sequence：`382-390`

## 7. Browser

- 五个账号均在独立 Context 登录并保持独立会话
- capability audit：24 个页面/入口，无业务写入
- 完整链业务写请求：29 个，全部来自浏览器、通过 `/api/v1` Gateway
- 主链截图：49 张；能力审计截图：24 张
- 关键响应式检查：9 个视图 × 4 个 viewport = 36 次
- viewport：`390x844`、`768x1024`、`1366x768`、`1920x1080`
- page-level horizontal overflow：`0`
- page errors：`0`
- unexpected Console errors：`0`
- request failures：`0`
- unexpected HTTP errors：`0`
- external requests：`0`
- 外部权重、公共数据下载或推理服务请求：`0`
- sensitive exposure：`0`
- clinical overclaim：`0`

第二次下载的 HTTP 409 以及浏览器对应的单条 Console 资源错误是刻意验证的一次性
授权拒绝，不计为非预期错误。

回归结果：

- frontend tests：`71 passed`
- `pnpm typecheck`：通过
- `pnpm build`：通过；仅有既有 chunk-size warning
- backend tests：`163 passed, 66 skipped`
- Python compileall：通过
- OpenAPI：164 paths、170 operations、170 个唯一 operation IDs
- Alembic：canonical 与 Z2E 均为 current=head=`20260728_0049`
- Compose：Z2E 组合与基础 `compose.yaml`配置检查通过

66 个后端 skip 是需要专用测试数据库、破坏性并发开关或受控 PathMNIST smoke 环境的
既有门槛，不是失败。没有声称本轮验证了需要公网来源变量的所有部署模板。

## 8. UI Gaps

- missing pages：0
- missing business buttons：0
- API-only required steps：0
- dead ends：0
- manual URL changes：0
- unclear blocking statuses：0
- blocking frontend gap：0

非阻断问题：

1. Readiness 页“确认意见”可见标签与文本域没有完整程序化关联。人工点击、输入和提交均正常，但辅助技术与自动化定位不够稳健；建议后续补充控件 `id`与标签关联。
2. 自动化最初对申请预览文案的断言过严，恢复同一草稿后继续提交，产生了两组合法的 `application.updated`与`application.compatibility.checked`事件。没有产生第二个 Application，也没有使用 API 旁路；这属于验收脚本定位修正，不是产品流程缺口。

本轮没有因上述非阻断问题修改产品代码。

## 9. Canonical Protection

- before 原始业务状态 SHA-256：`d5d0d086a6185effcceacecbbd4da99de8cc73fc4eb0a5ede36a5b597356289a`
- after 原始业务状态 SHA-256：`d5d0d086a6185effcceacecbbd4da99de8cc73fc4eb0a5ede36a5b597356289a`
- before/after 文档：逐字节一致
- business state changed：`false`
- canonical PostgreSQL volume：`medtrust-space_postgres_data`
- Z2E PostgreSQL volume：`medtrust-z2e-postgres-data`
- canonical MinIO volume：`medtrust-space_minio_data`
- Z2E MinIO volume：`medtrust-z2e-minio-data`
- shared PGDATA / shared object storage：`false / false`
- single writer：`true`
- canonical counts：Application 3、Contract 3、Job 3、Run 2、Artifact 2、Package 2、Grant 2
- canonical relation/evidence：`7 / 8`
- canonical MinIO：`30`
- canonical audit：sequence `353`，chain valid，invalid sequence `null`

详细对比见 `docs/roadshow/z2e/CANONICAL-STATE-DIFF.md`。

Z2E 独立环境的预期增量与实际一致：

| 对象 | Before | After | 增量 |
|---|---:|---:|---:|
| Application | 3 | 4 | +1 |
| Contract | 3 | 4 | +1 |
| ComputeJob | 3 | 4 | +1 |
| ComputeRun | 2 | 3 | +1 |
| Artifact | 2 | 3 | +1 |
| ReleasePackage | 2 | 3 | +1 |
| DownloadGrant | 2 | 3 | +1 |
| AuditEvent | 353 | 390 | +37 |
| MinIO objects | 30 | 34 | +4 |

DataProduct、ModelProduct、治理审核、Relation、Evidence 和 MaterializationPlan 均无变化。

## 10. Final Decision

- full front-end chain passed：`true`
- core business process complete：`true`
- from-zero new chain proven：`true`
- canonical Engineering Roadshow RC maintained：`true`
- tag ready：`true`
- `v0.13` created：`false`
- remaining blocker：仅等待用户阅读本报告并批准创建标签

Phase 5.12.7-Z2E 证明了用户可以从一个新 Application 开始，仅通过前端完成
Application → Contract → Readiness → Job → real Run → quarantined Artifact →
三方结果审核 → ReleasePackage → 一次性下载 → 重复下载拒绝 → Audit 全链。

该结论只适用于当前非临床工程路演环境。`hard_isolation=false`保持不变，不能据此声称
医院生产部署、临床安全、任意代码隔离或监管认证已经完成。
