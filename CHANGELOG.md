# Changelog

## Phase 5.13E-Final

- Closed the retained hospital-side reference execution with deterministic
  scanning, independent hospital review, complete causal validation, a signed
  aggregate EvidenceBundle, and verified central summary registration.
- Added central migration `20260730_0058` and local migration
  `phase5.13E_0011`.
- Kept the original Artifact quarantined and byte-identical; central received
  no Artifact bytes, local path, raw data, patient-level data, or model weight.
- Preserved existing Job, Run, Artifact, result package, download grant, and
  one-use authorization counts. `hard_isolation=false` remains explicit.

## Phase 5.13E-2C-R1

- Added fresh fixed-execution authorization TTL and minimum-validity gates,
  signed central consumption receipts, and one-use Order accounting.
- Added parallel immutable Task, Input, Runtime, ReferenceExecution, and local
  Artifact bindings plus the restricted local execution operator workflow.
- Completed one pre-authorized fixed 20-sample PathMNIST execution at
  19/20 and 0.95; the new Artifact stops at `quarantined`.
- Added replay/restart protection, concurrent audit serialization, four-width
  browser evidence, migration `20260729_0057`, and local migration
  `phase5.13E_0010`.
- Created no central Job/Run/Artifact, EvidenceBundle, release package,
  download grant, data/model transfer, tag, or remote; `hard_isolation=false`.

## Phase 5.13E-0

- Froze the hospital-side controlled execution architecture, Executor security
  boundary, image supply chain, network/filesystem isolation, Local Artifact
  lifecycle, Hospital Output Review, and EvidenceBundle generation flow.
- Extended the threat model to 34 execution and egress risks and froze the
  L0-L4 maturity gates.
- Limited the first eventual execution to fixed PathMNIST plus fixed ResNet-18
  on the public/non-clinical demonstration subset.
- Documentation only: no code, API, migration, database, Executor, container,
  execution, data/model transfer, Artifact, MinIO object, tag, or capability
  change; `hard_isolation=false`.

## Phase 5.13D

- Added migration `20260729_0052`, dedicated Ed25519 policy keys, deterministic
  signed PolicyBundle versions, revocation, and control-only ExecutionOrders.
- Added mTLS pull, independent Connector validation, local policy reviewer
  accept/reject, signed receipts/decisions, replay protection, and revocation.
- Added central/local policy pages with no execution action.
- Accepted one final control-only order, manually rejected one, automatically
  rejected one, and verified revocation after acceptance.
- Job/Run/Artifact and isolated MinIO deltas remained zero;
  `hard_isolation=false`.

## Phase 5.13C

- Added local metadata registry, immutable versions, local-only location
  references, minimum quality profiles, reviews, and bundles.
- Added mTLS metadata sync with identity, capability, sequence, digest,
  idempotency, paused/revoked, and prohibited-field guards.
- Added central mirrors at migration `20260729_0051`; prior business and MinIO
  counts stayed unchanged.

## Phase 5.13B

- Added an independent Hospital Connector control process and separate
  loopback Compose project with D-drive SQLite, identity, certificates and
  local audit.
- Added one-time enrollment, Operator registration review, Local Test CA mTLS,
  heartbeat, disabled Capability Manifest, pause/resume, certificate rotation
  and revocation.
- Added central operator/hospital pages and local Connector pages.
- Migrated to `20260729_0050`; all historical business and MinIO counts stayed
  unchanged and `hard_isolation=false`.
- Created no data/model transfer, LocalAsset, ExecutionOrder, Job, Run,
  Artifact or EvidenceBundle.

## Phase 5.13A

- 将产品重定位为医院控制的医疗 AI 可信验证与证据生产平台。
- 冻结中央、医院、需求方/模型方三个控制域及控制、数据、证据、撤销和拒绝流。
- 冻结 33 类 Hospital Connector 威胁、L0-L4 成熟度和公开宣传护栏。
- 设计可签名 PolicyBundle、ExecutionOrder、EvidenceBundle 及治理、质量和 RWD 领域模型。
- 冻结 Phase 5.13-5.16 路线图、90 天计划和 Phase 5.13B 严格范围。
- 仅修改文档；没有代码、API、数据库、迁移、应用、业务状态或 tag 变化。

## Phase 5.12.6B-R

- Backfilled one historical PathMNIST and fixed ResNet-18 real CPU run into
  append-only runtime and platform-verification evidence.
- Locked the exact versions, Artifact, three approvals, safe package,
  one-time grant, aggregate result, and audit-chain references.
- Added bidirectional product summaries and a verified evidence-matrix cell.
- Created no Job, Run, Artifact, package, download, external materialization,
  release tag, or clinical claim.

## Phase 5.12.5

- Added version-locked DatasetModelRelation summaries and append-only,
  database-protected DatasetModelEvidence records.
- Added operator static review APIs, idempotent canonical application,
  audit events, a 3 by 2 matrix and bidirectional product detail summaries.
- Recorded four pathology pairs as requiring unverified transformations and
  two endoscopy/pathology pairs as statically incompatible.
- Kept external declaration, executed, execution-failed and verified evidence
  at zero; downloaded no data or model weights and unlocked no compute path.

## Phase 5.12.4

- Added an independent curator-submit/operator-review publication workflow for
  governed external model drafts, including immutable review tasks and audit
  events.
- Published CONCH and UNI for metadata discovery; kept Prov-GigaPath as a
  metadata-only draft.
- Added public catalog provenance and non-executable labels, curator portal
  access, responsive browser coverage and explicit Application/service gates.
- Migrated the canonical database to `20260727_0046`; existing Applications,
  Contracts, ComputeJobs, ComputeRuns and MinIO objects remained unchanged.
- Downloaded no weights, registered no Executor, claimed no compatibility,
  opened no network boundary, and created no release tag.

## Phase 5.12.3B2

- Created immutable metadata-only ModelProduct drafts for CONCH, UNI and
  Prov-GigaPath, each bound to the exact external source and governance
  snapshot.
- Added materialization gates across Application, readiness and compute paths.

## Phase 5.12.3B1

- Reviewed eight selected external model candidates using 43 bounded official
  requests and 96 append-only operator Reviews.
- Derived three restricted metadata-only draft candidates while preserving all
  raw records, versions, historical ModelProducts and compute history.
- Downloaded no weights or datasets, cloned no repository, and invoked no
  inference service.

## Phase 5.12.3A

- Added external model governance profiles, append-only reviews, family
  resolution, deterministic technical completeness and draft eligibility.
- Added operator write APIs, four-role read APIs, governance workbench, detail
  drawer, review timeline, and responsive containment.
- Migrated the canonical database to `20260727_0043` and initialized 16
  profiles while preserving all raw records, versions and historical business
  objects.
- No external URLs, weights, repository clones, inference APIs, ModelProducts,
  LAN exposure, release tag, or `v0.13` were added.

## Phase 5.12.2

- Added versioned external model catalog synchronization, immutable model
  history, D-drive snapshots and four-role read-only browsing.
- Preserved `not_materialized`; no model weights or executable products added.

## 2026-07-27 - Phase 5.11.4 first public metadata products

- Added an independent catalog curator account and reused the native
  DataProduct submit, operator review and publication lifecycle.
- Published CPTAC-COAD, CAMELYON17 and HyperKvasir as discoverable metadata-only
  products; two drafts and the archived CPTAC-BRCA test object remain intact.
- Added backend Application, readiness and ComputeJob denial for unmaterialized
  external products plus explicit public-catalog boundary labels.
- Added migration `20260727_0041`, API-only idempotent publication tooling and
  PostgreSQL/browser regression coverage.
- No dataset/model download, materialization, new Application/Contract/Job,
  LAN exposure, release tag or `v0.13` was created.

## 2026-07-27 - Phase 5.11.3A 外部公共数据目录治理

- 为982条不可变外部目录记录新增独立治理画像、只追加人工评审和非破坏性重复项决议。
- 新增固定状态优先级、元数据完整度、阻塞/警告和`eligible_for_draft`计算规则。
- 新增四角色只读治理API、运营方幂等写接口、审计事件和治理工作台。
- 正式目录保持Review=0、DuplicateResolution=0、DataProduct新增=0、下载=0。
- Alembic单一head更新到`20260727_0038`；审计链、测试、构建与四账号响应式浏览器验收通过。

## 2026-07-25 - Phase 5.9 生命周期治理与四账号独立门户

- 新增数据/模型产品通用生命周期请求，支持平台审核的下架、重新上架和逻辑归档。
- 新增服务端影响分析、并发/幂等保护、历史发布时间保留和主演示产品归档保护。
- 新增四个本地用户名/密码账号、scrypt 哈希、digest-only HttpOnly 会话和登录/退出接口。
- 正常 API 权限不再依赖客户端 `X-Demo-Identity`；调试角色切换默认隐藏。
- 新增四角色独立菜单、当前账号/组织显示、直接路由 403 和生命周期审核中心。
- 新增 migration `20260725_0032`，业务表增至 54。
- 后端 156 passed / 4 skipped，前端 43 tests、typecheck、build 通过；OpenAPI 109 paths / 112 operations。
- 四账号 Cookie 隔离、真实浏览器登录、三视口布局和数据/模型完整治理生命周期通过。
- 局域网、公网、云部署和 Phase 5.10 未开始。

## 2026-07-25 - Phase 5.8.1 路演预检与启动流程热修复

- 修复 Compose 服务停止时空输出调用 `.Trim()` 导致的 PowerShell 5.1 空值异常。
- 统一真实 Compose 服务发现、容器状态检查和 PostgreSQL/MinIO 有界就绪等待。
- preflight 保持只读，并对停止、缺失、不健康服务输出完整可执行失败摘要。
- stop/start 增加 PID 命令归属、PID 重用、stale PID 和受管子进程树保护。
- prepare 可从应用停止状态自动启动基础设施、预检、启动应用并验证 HTTP/status；reset 保持显式可选。
- 默认 prepare 不 reset，避免固定 Phase 4 reset 删除 Phase 5 路演链。
- 未修改业务状态机、migration、API 业务语义、推理、Artifact、结果包或下载授权。

## 2026-07-25 - Phase 5.8 全链路路演编排与稳定性封板

- 新增组织权限约束的只读 `roadshow-experience` 聚合 API，不新增业务表或 migration。
- 新增 `/roadshow` 入口、8/15 分钟模式、12 节点真实业务链、下一责任方、实时/备用链和讲解面板。
- 路演身份、模式和对象上下文只保存在 `sessionStorage`，详情页和角色切换保持会话。
- 新增关键/全部事件、组件链、Artifact/Package/Grant 安全反差和系统健康视图。
- 新增准备、状态和 Phase 5.7 正式测试基线脚本；修复 PathMNIST 预检仍使用旧四文件清单的稳定性缺陷。
- 严格后端回归为 155 passed / 2 skipped；前端 39 tests、typecheck、build 通过；OpenAPI 为 99 paths / 102 operations。
- 真实演示库保持 51 张表、81 条审计事件和有效审计链；两条完成链为 12/12，一条实时链为 3/12。
- `Executor=unknown` 保持可见，因为当前没有独立持久心跳；`hard_isolation=false` 保持可见。

## 2026-07-25 - Phase 5.7 Artifact 多方审核、安全结果包与一次性下载

- 新增 Artifact 通用结果中心 API 和四角色结果审核/下载页面。
- 复用既有审核、package 和 grant 表，新增 migration `20260725_0031` 仅扩展审核计划和下载拒绝审计事件。
- 两个 Artifact 均完成医院、模型方和平台三方 required review，平台必须最后审核。
- 从 MinIO quarantine 校验不可变 Callback manifest，生成两个独立 ZIP；每个 ZIP 精确包含三个白名单文件。
- 两个一次性 grant 均成功下载 1 次，二次使用被拒绝并写入审计；token 只持久化 digest。
- 最终 ComputeJob=2、ComputeRun=2、Artifact=2 quarantined、Package=2 available、Grant=2 exhausted、无效审计链=0。
- 后端 151 passed / 2 skipped，前端 32 tests、typecheck、build 通过；OpenAPI 为 95 paths / 98 operations。

## 2026-07-25 - Phase 5.6 受控派发、固定执行与 Artifact 隔离

- 新增运营方显式派发 API，复用 `compute.run.reserved`、Outbox、Consumer Inbox、Coordinator 和 Callback Inbox。
- 修复 Phase 5.5 预占槽位与来源 Run 被重复计算的问题，Job 和 Run 共同继承一次 `run_count`。
- 固定 PathMNIST/ResNet-18 完成两次 20 图 CPU 推理，均为 19/20、Accuracy 0.95、Mean confidence 0.960102856159。
- 三文件输出经白名单扫描后写入专用 MinIO quarantine bucket。
- 最终 ComputeJob=2 succeeded、ComputeRun=2 succeeded、Artifact=2 quarantined、ReleasePackage=0、DownloadGrant=0、无效审计链=0。
- 重复派发复用同一 Run，重复 Callback 不新增 Run 或 Artifact。
- 后端 147 passed / 5 skipped，前端 27 tests、typecheck、build 通过；OpenAPI 为 87 paths / 90 operations。

## 2026-07-25 - Phase 5.5 执行就绪、资格校验与 ComputeJob 创建

- 新增医院数据就绪、模型固定资产就绪、撤销证据、平台资格矩阵和不可变 Execution Eligibility Snapshot。
- 新增真实 ComputeJob 创建与原子派发前槽位预留；`run_count=1` 并发时只允许一个有效任务。
- 新增迁移 `20260724_0030` 和 3 张业务表，并为两份历史 active 合同回填 6 条固定能力绑定。
- 新增四角色 `/execution` 列表、详情、确认表单、资格矩阵、任务详情、证据时间线和技术抽屉。
- 两份 active 合同均生成资格快照和待派发任务；最终 ComputeJob=2、ComputeRun=0、Artifact=0、无效审计链=0。
- 修复 390x844 下长 UUID/SHA 和描述表固有宽度造成的页面级横向溢出。
- 后端 148 passed / 2 skipped，前端 27 tests、typecheck、build 通过；OpenAPI 为 86 paths / 89 operations。

## 2026-07-24 - Phase 5.4 数字合约编排、四方确认与生效

- 新增 approved Application 到唯一数字合约草稿的通用编排，冻结申请快照、审核事实、数据/模型固定版本和四方组织。
- 新增确定性的最严格策略收敛、BLOCKER、来源矩阵、结构化合同内容和稳定 canonical digest。
- 复用既有 `draft -> proposed -> signed -> active` 状态机，实现需求方、医院、模型方和平台依次确认同一版本与摘要，平台必须最后确认。
- 新增通用数字合约列表、详情、确认、激活和对象级审计 API，以及四角色合同页面。
- 新增迁移 `20260724_0029`，补充合同草稿和策略收敛 AuditEvent 词汇，并仅为 Phase 5.4 结构化合同推迟执行绑定；表数量仍为 48。
- 两条真实浏览器合同均达到 active，8 条确认记录使用各自唯一 digest；平台提前确认被后端拒绝。
- 合同激活后停在等待数据与模型就绪，演示数据库 ComputeJob 仍为 0；平台内确认不宣称为 CA 或可靠电子签名。
- 后端 147 passed / 2 skipped，前端 22 tests、typecheck、build 通过；OpenAPI 为 78 paths。

## 2026-07-24 - Phase 5.3 计算需求与数据-模型组合申请

- 新增需求企业五步申请向导、published-only 数据/模型固定版本选择、草稿保存编辑和提交确认。
- 新增服务端兼容性规则与持久化快照，支持 PASS、WARNING、BLOCKER，并拒绝过期检查和未确认 WARNING。
- 新增平台预审、医院数据使用审核和模型使用审核真实 ReviewTask 队列、意见、条件与范围证据。
- 复用 Application、ApplicationSnapshot、ReviewTask、AuditEvent 和 Outbox，不新增业务表或影子申请模型。
- 新增迁移 `20260724_0028`，补充申请生命周期事件、draft 模型选择守卫和事务内延迟组合外键；表数量仍为 48。
- 两条不同名称的真实浏览器流程均到达 approved，下一步显示数字合约，没有创建 ComputeJob。
- 修复预览丢失未挂载表单值、提交未保存最新需求字段和 390x844 窄屏横向溢出。
- 后端 142 passed / 5 skipped，前端 18 tests、typecheck、build 通过；OpenAPI 为 72 paths。

## 2026-07-24 - Phase 5.2 模型产品全生命周期

- 新增模型提供方四步新建/编辑表单、运营审核队列、模型详情证据和已发布模型目录。
- 新增通用模型产品创建、更新、提交、退回、批准发布、目录和审计 API。
- 复用 Marketplace 状态机和固定 ModelRegistry；浏览器不能上传权重、镜像、脚本或任意 entrypoint。
- 新增迁移 `20260724_0027`，补充 created、updated、returned 三个 AuditEvent 词汇，并移除阻止复用同一白名单资产的历史全局唯一约束；表数量仍为 48。
- 后端精确校验 registry、runtime、Schema、资源限制和安全策略，并拒绝模型下载、动态脚本、二次分发和未授权网络。
- 两条真实浏览器流程通过，其中一条覆盖退回、修订、刷新持久化和重提；需求企业目录即时看到两个发布模型。
- Phase 5.1 数据产品详情、审计证据和 published-only 目录回归通过。
- 后端 139 passed / 5 skipped，前端 11 tests、typecheck、build 通过；OpenAPI 为 62 paths。

## 2026-07-24 - Phase 5.1 医院数据产品全生命周期

- 新增医院数据产品管理、四步新建/编辑表单、运营审核队列、产品详情证据和已发布目录。
- 新增通用数据产品创建、更新、提交、退回、批准发布、目录和审计 API；不创建 Roadshow 影子状态。
- 复用 Catalog 状态机、DataResource、ProductSource 和 Publication；运营待办由 `under_review` 版本派生。
- 新增迁移 `20260724_0026`，仅补充 created、updated、returned 三个 AuditEvent 词汇，表数量仍为 48。
- 创建、提交和批准重放保持幂等；后端拒绝跨组织编辑、非医院创建和伪造敏感输出。
- 审计中心支持按数据产品版本过滤，技术证据抽屉只展示真实持久化或可验证字段。
- 两条真实浏览器流程通过，其中一条覆盖退回、编辑和重提；需求企业目录即时看到两个发布产品。
- 后端 138 passed / 5 skipped，前端 8 tests、typecheck、build 通过；OpenAPI 为 52 paths。

## 2026-07-24 - Phase 5.0 可复现路演基线

- 修复 React Strict Mode 主动取消请求被错误显示的问题，保留真实网络和 HTTP 错误。
- 增加卸载安全、普通错误可见和写请求 single-flight 测试，共 4 项通过。
- 移除 Phase 4 脚本中的账号专属 Node 路径和提交脚本资产绝对路径。
- 增加被忽略的本地配置、无秘密示例配置和只读 preflight。
- 修复 Windows 中文和空格工作区下 Vite 启动参数拆分，并要求前后端同时健康才报告启动成功。
- 空库迁移到 `20260723_0025` 后确认 48 张表；后端完整回归 137 passed / 5 skipped。
- 连续完成 3 次 stop/reset/start/health，浏览器验证四角色、目录、真实错误显示和恢复。
- 冻结完整 Phase 4 + Phase 5.0 基线；Phase 5.1 未开始。

## 2026-07-23 - Phase 4 多主体可信协作路演

- 新增可发布、可审核的固定模型产品目录，并把计算需求同时固定到数据版本和模型版本。
- 新增数据方、模型方、需求方和空间运营方四类后端授权身份及独立待办。
- 将平台预审、数据使用审核、模型使用审核、四方签署和三方执行就绪接入权威业务命令。
- 新增多方 Artifact 结果审核、白名单结果包、短期一次性下载授权和完整 Audit/Outbox 证据。
- 结果包只包含 `aggregate_metrics.json`、`confusion_matrix.csv`、`execution_summary.json`；原始图像、患者级结果、特征和模型权重继续禁止出域。
- 新增 Phase 4 专用启动、停止、重置脚本及四角色 API 前端；保留既有 Mock/API 模式。
- 真实浏览器完成 29 步链路，固定模型完成 20 图 CPU 推理，审计链有效，下载授权二次使用被拒绝。
- Alembic head 为 `20260723_0025`，48 张业务表；历史迁移未被改写。

## 2026-07-23 - Phase 4 Stage 1

- 冻结四类后端身份上下文：空间运营方、数据提供方、模型提供方、数据需求方。
- 完成模型产品目录、计算需求模型选择、合同模型对象、三方就绪确认、多方 Artifact 审核和安全结果包的最小领域设计。
- 明确旧 `ArtifactReview` 与代码内 `ModelRegistry` 的保留边界，避免把历史单审核或执行白名单冒充新的业务目录与多方事实源。
- 冻结 0021—0023 的 10 张新增表计划；历史 0001—0020 不修改。
- 继续保持 `hard_isolation=false`、非临床、非生产、非国家测评和无原始数据/权重出域边界。

## Unreleased — Phase 3 real data demo

- 保留 Phase 1 Mock 模式，新增真实 FastAPI 数据模式和七个核心页面。
- 新增固定 PathMNIST 20 张 CPU 受控推理命令；禁止任意模型、路径、Shell 或用户代码输入。
- 新增可重复的演示基线、冻结备份恢复和一键启动/停止/复位脚本。
- 接通 Outbox Dispatcher、Consumer Inbox、Coordinator、本地白名单执行器和 Callback Inbox。
- 真实 Run 可推进到 `succeeded`，Artifact 始终默认 `quarantined`，不提供下载或自动发布。
- 明确 `hard_isolation=false`，不宣称临床、生产级隐私计算、医院接入或国家测评能力。

## v0.2-controlled-smoke — 2026-07-23

- 冻结首个 PathMNIST + ResNet-18、20 张测试图、CPU-only 的端到端受控推理证据。
- Alembic head `20260722_0020`，38 张实表。
- 建立可追溯发布说明、权威结果 JSON、数据库 schema 导出和本地恢复备份。
# Unreleased

- Added a metadata-only external public dataset catalog connector, version
  history, D-drive snapshots, controlled synchronization API, operator UI, and
  four-role read-only catalog UI. Runtime acceptance remains blocked pending a
  working Docker/PostgreSQL environment.
# 2026-07-28

- Added immutable asset materialization plans, independent curator/operator
  approval boundaries and a read-only operator portal.
- Completed Phase 5.12.6A with zero selected candidates because all current
  model weights require gated private-token access.
- Preserved zero external downloads, zero new compute activity and 30 MinIO
  objects.
# Phase 5.13C-A1

- 增加 Connector 本地 PBKDF2 密码、摘要 Session、双角色授权和自审阻断。
- 增加完整 metadata-only 本地工作台、同步历史和中央两版镜像详情。
- 四档浏览器验收通过；未启动 Phase 5.13D，未改变 `hard_isolation=false`。
