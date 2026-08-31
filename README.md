# MedTrust Space

> 公开源码快照：2026-08-31。该仓库展示 MedTrust Space 的工程原型与路演实现；历史阶段记录保留在本文后半部分，不应被当作当前生产能力声明。

MedTrust Space 是一个医疗可信数据空间工程原型，采用 React、FastAPI、PostgreSQL、MinIO 与受控 PathMNIST 参考流程，演示数据/模型产品登记、需求申请、多方审批、数字合约状态、模拟支付、受控执行和安全聚合结果交付。

## 当前快照

- 中央 Alembic 源码 head：`20260829_0061`。
- 后端验证：`450 passed / 68 skipped / 0 failed`；68 项均为未配置 PostgreSQL 测试库时显式门控的 integration tests。
- 前端验证：`169 passed / 0 failed`；TypeScript 检查与 Vite production build 通过。
- 本快照不包含本地数据库、MinIO 数据、原始数据集、模型权重、运行结果、环境变量、API Key、录屏、视频母版或构建缓存。

## 必须保留的工程边界

- `hard_isolation=false`：当前不是生产级硬隔离或临床系统。
- 仅演示预设能力与固定参考流程，不支持任意代码执行。
- 数字签署、`ACTIVE`、支付和下载授权均为工程演示状态；不代表法律电子签名、真实资金结算或认证结果。
- 结果包仅允许安全聚合结果，不交付原始图像、患者级结果、特征、模型权重、内部路径或凭据。
- 外部数据/模型目录为 metadata-only 治理视图，不代表平台拥有、下载或获得再分发权。
- 远程角色助手默认关闭；启用第三方模型前，应单独评估并配置将要发送给该服务的数据范围。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `backend/` | FastAPI、领域模块、Alembic migrations 与测试 |
| `frontend/` | React/Vite 路演界面与前端测试 |
| `hospital-connector/`、`hospital-executor/` | 医院侧 Alpha 连接器与固定执行参考实现 |
| `registered_assets/` | 可公开的固定资产 manifest、来源与校验信息；不含数据或权重 |
| `config/` | 仅提交示例配置；真实配置保留在本地 |
| `scripts/` | 本地启动、预检、停止和固定演示工具 |
| `docs/` | 架构、阶段交付、限制和部署资料 |

## 验证源码

环境要求：Python 3.12、Node.js、pnpm、PowerShell 7；完整本地路演还需要 Docker Desktop。

```powershell
# 后端
python -m venv .\backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
.\backend\.venv\Scripts\python.exe -m pytest .\backend\tests

# 前端
Push-Location .\frontend
pnpm install --frozen-lockfile
pnpm test
pnpm build
Pop-Location
```

未配置 `MEDTRUST_TEST_DATABASE_URL` 时，PostgreSQL integration tests 会按设计跳过；这不等于数据库集成验证已经执行。

## Windows 本地路演

完整受控执行需要用户自行取得并保存在本地的 PathMNIST NPZ 与固定模型权重。仓库只提交来源、版本和校验 manifest；模型 manifest 明确不主张权重再分发权。

```powershell
# 1. 安装依赖后创建本地配置，填写资产路径；真实密码与 API Key 不得提交
Copy-Item .\config\phase4-demo.example.env .\config\phase4-demo.env
.\scripts\set_local_demo_password.ps1

# 2. compose.yaml 使用外部卷；首次运行先创建
docker volume create medtrust-space_postgres_data
docker volume create medtrust-space_minio_data

# 3. 默认绑定 D 盘；没有 D 盘时先改成本机可用目录
$env:MEDTRUST_STORAGE_ROOT = "C:/MedTrustData"
$env:MEDTRUST_CACHE_ROOT = "C:/MedTrustCache"
New-Item -ItemType Directory -Force C:\MedTrustData, C:\MedTrustCache | Out-Null

# 4. 无重置准备并启动路演
.\scripts\prepare_roadshow.ps1 -Open
```

- 登录入口：`http://127.0.0.1:5173/demo-login`
- 路演入口：`http://127.0.0.1:5173/roadshow`
- OpenAPI：`http://127.0.0.1:8000/docs`
- 停止：`.\scripts\stop_phase4_demo.ps1`

首次公开快照刻意排除了内部 Agent/规划记录、交接材料、本地评审文件、云端本地配置、运行数据库、下载资产与整个视频制作目录。需要演示视频时，应使用 Release 或独立制品存储，而不是把大型二进制写入源码历史。

## 许可与第三方资产

本快照尚未选择根开源许可证。PathMNIST 数据来源与 `CC BY 4.0` 信息记录在 `registered_assets/pathmnist_v1/dataset_manifest.json`；固定模型 manifest 记录了来源，同时明确权重权利未由来源单独说明，因此仓库不分发模型权重。

## 历史阶段记录

以下内容按当时阶段保留，版本号、表数量和测试数字属于历史快照；当前状态以上方“当前快照”为准。

## Phase 5.13E-Final Hospital Evidence Closure

The engineering Alpha now closes one retained, pre-authorized hospital-side
reference execution through local scanning, independent hospital review,
causal validation, signed aggregate evidence, and verified central summary
registration. The original Artifact remains quarantined and byte-identical;
the central platform receives no Artifact bytes or hospital-local path.

This is a non-clinical engineering result with `hard_isolation=false`, not a
production, compliance, clinical, or certification claim. See
[`docs/Phase5.13E-Final-evidence-closure.md`](docs/Phase5.13E-Final-evidence-closure.md).

## Phase 5.13A 医院控制型证据平台设计冻结

Phase 5.12 已由 annotated tag `v0.13-roadshow-evidence-rc` 冻结。下一阶段产品定位为“医疗 AI 可信验证与证据生产平台”：中央负责协作、治理和证据登记，独立 Hospital Connector 负责医院本地政策核验、任务拒绝、本地执行和出域审核。

Phase 5.13A 仅完成战略、架构、威胁模型和协议设计，没有修改代码、数据库或迁移，也没有部署真实医院 Connector。当前仍为非临床工程原型，`hard_isolation=false`。详见 `docs/strategy/PHASE5.13A-ACCEPTANCE.md`。

Phase 5.12.5 adds a version-locked, append-only dataset-model evidence graph.
The canonical public matrix contains six metadata-only static reviews: four
pathology pairs require transformation and two HyperKvasir/pathology-model
pairs are structurally incompatible. Executed and verified evidence remain
zero. See
`docs/Phase5.12.5-dataset-model-evidence-acceptance.md`.

Phase 5.12.4 publishes CONCH and UNI as governed metadata-only external model
catalog products while keeping Prov-GigaPath as a draft. Publication enables
discovery only: no weights, Executor, compatibility claim, Application or
execution was added. See
`docs/Phase5.12.4-metadata-model-publication-acceptance.md`.

## Phase 5.9 生命周期治理与四账号独立门户

Phase 5.9 已完成数据/模型产品的下架、重新上架和逻辑归档治理，并将前端自报演示身份替换为四个本地用户名/密码账号、服务端 HttpOnly Cookie 会话和角色独立门户。普通路演不再依赖顶部角色切换。

首次使用先设置本地密码：

```powershell
.\scripts\set_local_demo_password.ps1
.\scripts\prepare_roadshow.ps1
```

登录入口：`http://127.0.0.1:5173/demo-login`。四个用户名为 `hospital.demo`、`model.demo`、`requester.demo`、`operator.demo`；密码不进入 Git。

当前验证基线：Alembic `20260725_0032`、54 张业务表、OpenAPI 109 paths / 112 operations、后端 156 passed / 4 skipped、前端 43 tests，typecheck/build 通过。详见 [Phase 5.9 交付报告](docs/Phase5.9-lifecycle-governance-and-four-portals-delivery.md)。

## Phase 5.8 全链路路演编排与稳定性封板

Phase 5.1 至 5.7 仍是唯一权威业务系统。Phase 5.8 新增只读路演聚合和 `/roadshow` 会话编排，把真实产品、申请、数字合约、执行资格、固定推理、quarantined Artifact、多方审核、安全结果包和一次性下载证据统一成 12 节点演示链；没有新增业务表或 migration。

首次运行先创建被 Git 忽略的本地配置，然后使用统一准备脚本：

```powershell
Copy-Item .\config\phase4-demo.example.env .\config\phase4-demo.env
.\scripts\prepare_roadshow.ps1
```

路演入口：`http://127.0.0.1:5173/roadshow`

`prepare_roadshow.ps1` 会停止旧应用进程、启动 PostgreSQL/MinIO、执行只读预检、启动应用与 Worker，并完成 HTTP/status 检查；默认不重置数据库。只有明确需要固定 Phase 4 基线时才使用 `-Reset`，该选项会移除 Phase 5 路演业务链。

Phase 5.8 的历史冻结基线保持为 Alembic `20260725_0031`、51 张业务表、OpenAPI 99 paths / 102 operations、后端 155 passed / 2 skipped、前端 39 tests。该历史事实未被改写。

## Start Here

公开仓库从本 README 与上方验证/启动说明开始。内部 Codex 交接、下一步任务和工作记录未纳入公开快照。

## Phase 4 多主体路演 MVP

MedTrust Space 已完成四方多主体可信协作工程闭环：医院数据方、AI 模型方、需求企业和空间运营方可以完成数据/模型产品上架、计算需求、多方审批、数字合约、三方就绪、固定 PathMNIST 受控执行、隔离 Artifact、强制结果审核和三文件安全结果包下载。

当前 Alembic head 为 `20260725_0032`，`medtrust` schema 共有 54 张业务表。最近一次后端严格回归为 156 passed、4 个环境门控 skipped；2026-07-25 前端 43 项测试、TypeScript 检查和生产构建通过。

结果包只允许包含 `aggregate_metrics.json`、`confusion_matrix.csv` 和 `execution_summary.json`。原始图像、患者级结果、特征、模型权重、内部路径和凭据禁止交付。`hard_isolation=false`；本项目不是临床验证、生产级隐私计算、医疗器械性能验证、真实医院接入或国家可信数据空间测评。

## Phase 3 历史演示启动

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1 -Reset
```

保留当前演示数据再次启动时去掉 `-Reset`。停止应用进程：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_demo.ps1
```

前端：`http://127.0.0.1:5173`；OpenAPI：`http://127.0.0.1:8000/docs`。详细操作见 `docs/Phase3-real-demo-delivery.md`。

## 验证

```powershell
cd frontend
pnpm typecheck
pnpm build

cd ..\backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider
```

## 文档

- 项目公开入口与当前状态：本 README
- [产品规划](docs/产品规划.md)
- [架构说明](docs/架构说明.md)
- [API 设计](docs/API设计.md)
- [演示流程](docs/演示流程.md)
- [更新日志](docs/更新日志.md)
- [后端骨架说明](backend/README_backend.md)
- [Identity 数据库模型](docs/Phase2-B2-identity-model.md)
- [Spaces 数据库模型](docs/Phase2-B2-space-model.md)
- [Connectors 数据库模型](docs/Phase2-B2-connector-model.md)
- [Catalog / DataProduct 领域模型](docs/Phase2-B2-catalog-model.md)
- [PostgreSQL 数据库冻结设计 v2（34表）](docs/Phase2-database-design-v2.md)
- [Catalog ORM 前冻结检查](docs/Phase2-B2-catalog-freeze-check.md)
- [Catalog ORM + Migration 实现说明](docs/Phase2-B2-catalog-orm.md)
- [Application 领域模型](docs/Phase2-B3-application-model.md)
- [PostgreSQL 数据库冻结设计 v3（37表目标）](docs/Phase2-database-design-v3.md)
- [Application Core ORM + Migration 实现说明](docs/Phase2-B3-application-orm.md)
- [Application 扩展领域模型](docs/Phase2-B3-application-extension-model.md)
- [Application 扩展 ORM 前冻结检查](docs/Phase2-B3-application-extension-freeze-check.md)
- [Application 扩展 ORM + Migration 实现说明](docs/Phase2-B3-application-extension-orm.md)
- [Review 领域模型](docs/Phase2-B4-review-model.md)
- [PostgreSQL 数据库冻结设计 v4（Review）](docs/Phase2-database-design-v4.md)
- [Review ORM + Migration 实现说明](docs/Phase2-B4-review-orm.md)
- [Review 编排与 Application 汇总模型](docs/Phase2-B4-review-orchestration-model.md)
- [Review 编排实现冻结](docs/Phase2-B4-review-orchestration-freeze.md)
- [Contract 领域模型](docs/Phase2-B5-contract-model.md)
- [PostgreSQL 数据库冻结设计 v5（Contract）](docs/Phase2-database-design-v5.md)
- [Contract Core ORM + Migration 实现说明](docs/Phase2-B5-contract-core-orm.md)
- [Contract Policy ORM + Migration 实现说明](docs/Phase2-B5-policy-orm.md)
- [Contract Signature + Active Guard 实现说明](docs/Phase2-B5-signature-orm.md)
- [Contract Active Commit Hotfix](docs/Phase2-B5-contract-active-commit-hotfix.md)
- [Contract Policy / Constraint / Binding / Signature 领域模型](docs/Phase2-B5-contract-policy-model.md)
- [PostgreSQL 数据库冻结设计 v6（Contract Policy）](docs/Phase2-database-design-v6.md)
- [Compute / Artifact 领域模型](docs/Phase2-B6-compute-model.md)
- [PostgreSQL 数据库冻结设计 v7（Compute / Artifact）](docs/Phase2-database-design-v7.md)
- [ComputeJob / ComputeRun ORM + Migration 实现说明](docs/Phase2-B6-compute-job-run-orm.md)
- [Artifact / ArtifactReview ORM + Migration 实现说明](docs/Phase2-B6-artifact-review-orm.md)
- [AuditEvent + Transactional Outbox 领域模型](docs/Phase2-B7-audit-outbox-model.md)
- [PostgreSQL 数据库冻结设计 v8（AuditEvent / Transactional Outbox）](docs/Phase2-database-design-v8.md)
- [关键命令 Audit/Outbox 同事务接入](docs/Phase2-B7-critical-command-audit-integration.md)
- [Outbox Dispatcher 实现说明](docs/Phase2-B7-outbox-dispatcher.md)
- [Execution Coordinator 协议与消费者设计](docs/Phase2-B8-execution-coordinator-model.md)
- [Execution Callback Inbox 领域模型](docs/Phase2-B8-execution-callback-inbox-model.md)
- [Execution Callback Inbox ORM](docs/Phase2-B8-execution-callback-inbox-orm.md)
- [FakeExecutor Callback 闭环](docs/Phase2-B8-fake-executor-callback-loop.md)
- [Local Built-in Executor 接入前就绪](docs/Phase2-B8-local-executor-readiness.md)
- [模型接入前就绪检查清单](docs/Phase2-B8-model-onboarding-readiness-checklist.md)
- [Phase 3 演示基线](docs/Phase3-demo-baseline.md)
- [Phase 3 后端 API](docs/Phase3-backend-api.md)
- [Phase 3 前端 API 接入](docs/Phase3-frontend-api-integration.md)
- [Phase 3 真实演示交付](docs/Phase3-real-demo-delivery.md)
- [v0.2 controlled smoke 发布说明](docs/releases/v0.2-controlled-smoke.md)
- [Phase 5.1 数据产品生命周期交付](docs/Phase5.1-data-product-lifecycle-delivery.md)
- [Phase 5.2 模型产品生命周期交付](docs/Phase5.2-model-product-lifecycle-delivery.md)
- [Phase 5.3 计算需求与组合申请生命周期交付](docs/Phase5.3-application-lifecycle-delivery.md)
- [Phase 5.4 数字合约编排、四方确认与生效交付](docs/Phase5.4-digital-contract-lifecycle-delivery.md)
- [Phase 5.5 执行就绪、资格校验与 ComputeJob 创建交付](docs/Phase5.5-execution-readiness-and-job-creation-delivery.md)
- [Phase 5.6 受控派发、固定执行与 Artifact 隔离交付](docs/Phase5.6-controlled-execution-and-quarantine-delivery.md)
- [Phase 5.7 Artifact 多方审核、安全结果包与一次性下载交付](docs/Phase5.7-result-review-release-and-download-delivery.md)
- [Phase 5.8 全链路路演编排与稳定性封板](docs/Phase5.8-roadshow-seal-delivery.md)
- [Phase 5.8.1 路演预检与启动热修复](docs/Phase5.8.1-roadshow-preflight-hotfix-delivery.md)
- [Phase 5.9 生命周期治理与四账号独立门户](docs/Phase5.9-lifecycle-governance-and-four-portals-delivery.md)
- [产品生命周期治理规则](docs/PRODUCT-LIFECYCLE-GOVERNANCE.md)
- [四账号路演指南](docs/ROADSHOW-FOUR-ACCOUNT-GUIDE.md)
- [四浏览器 Profile 设置](docs/ROADSHOW-FOUR-BROWSER-PROFILE-SETUP.md)
- [本地演示账号设置](docs/LOCAL-DEMO-ACCOUNT-SETUP.md)
- [8 分钟路演脚本](docs/ROADSHOW-8MIN-SCRIPT.md)
- [15 分钟路演脚本](docs/ROADSHOW-15MIN-SCRIPT.md)
- [路演操作清单](docs/ROADSHOW-OPERATOR-CHECKLIST.md)
- [路演故障切换指南](docs/ROADSHOW-FAILOVER-GUIDE.md)

## Phase 4 多主体路演环境

Phase 4 提供四个由后端授权的演示身份，贯通数据产品、固定模型产品、计算需求、多方审核、数字合约、受控执行、隔离制品、结果审核和一次性安全下载。

```powershell
.\scripts\start_phase4_demo.ps1
```

打开 `http://127.0.0.1:5173/demo-login`。停止和重置分别使用：

```powershell
.\scripts\stop_phase4_demo.ps1
.\scripts\reset_phase4_demo.ps1
```

交付说明：

- [多主体业务模型](docs/Phase4-multiparty-workflow-model.md)
- [后端实施报告](docs/Phase4-backend-multiparty-workflow.md)
- [四角色前端实施报告](docs/Phase4-frontend-role-workflow.md)
- [路演启动与29步脚本](docs/Phase4-roadshow-demo-delivery.md)

当前仍为 `hard_isolation=false` 的公开数据工程原型，不是临床验证、生产级隐私计算、真实医院接入或国家可信数据空间测评结果。
# External public catalog (Phase 5.11.2 WIP)

The candidate public dataset catalog is metadata-only. Configure it locally
with:

```env
MEDTRUST_STORAGE_ROOT=D:\MedTrustData
MEDTRUST_CACHE_ROOT=D:\MedTrustCache
MEDTRUST_EXTERNAL_CATALOG_BASE_URL=http://127.0.0.1:3000/api/v1
MEDTRUST_ALLOW_INSECURE_LOCAL_CATALOG=true
```

For a Docker backend, use
`http://host.docker.internal:3000/api/v1`; Compose maps the D-drive roots into
the container. Remote preview and production configurations require HTTPS.
Catalog inclusion does not mean MedTrust downloaded, owns, can redistribute, or
can execute a dataset.

## Phase 5.11.4 public metadata products

Three governed records (CPTAC-COAD, CAMELYON17 and HyperKvasir) are now
discoverable in the DataProduct catalog. They remain `metadata_only`,
`external_upstream` and `not_ready`; MedTrust has not downloaded or hosted the
raw datasets and the products cannot create Applications or ComputeJobs. See
`docs/Phase5.11.4-metadata-product-publication-acceptance.md`.
# Phase 5.11.3A Public Catalog Governance

The external 982-record public catalog now has a separate governance overlay:
computed profiles, append-only operator reviews, non-destructive duplicate
resolutions, eligibility rules, and four-role read views. Catalog inclusion
does not imply that a source, license, or access method has been verified.
`eligible_for_draft` does not mean downloaded, published, redistributable, or
executable. See `docs/Phase5.11.3A-external-catalog-governance.md`.

## Phase 5.12.3A external model governance

The 16 metadata-only external model candidates now have separate computed
governance profiles, append-only operator review history, explicit family
resolution, strict draft-eligibility rules, and four-role read views. No
external source was revisited, no weight was downloaded, and no ModelProduct
was added. See `docs/Phase5.12.3A-external-model-governance.md`.

## Phase 5.12.3B1 first evidence-led model batch

Eight selected public model candidates now have 96 official-evidence,
append-only governance Reviews. CONCH, UNI and Prov-GigaPath are eligible only
for restricted metadata-only drafts. No weight, repository, inference runtime
or ModelProduct was materialized. See
`docs/Phase5.12.3B1-first-model-governance-result.md`.

## Phase 5.12.4 first published metadata-only models

CONCH and UNI are discoverable in the public model product catalog after
independent curator submission and operator approval. Prov-GigaPath remains a
draft. All three retain `metadata_only`, `not_ready` and `not_validated`;
external products are excluded from Application selection and ComputeJob
authorization. No weights or object-store files were added.
# Phase 5.12.6A status

The platform now contains a controlled, immutable external-asset
materialization planning domain. It does not contain a general external asset
downloader or external model Executor. Current CONCH and UNI candidates are
blocked by gated private-token access, so the canonical approved-plan count is
zero.

## Phase 5.12.6B-R status

The published PathMNIST and fixed ResNet-18 versions now expose a verified
historical reference relation. Its 0.95 aggregate accuracy applies only to the
fixed 20-image non-clinical demonstration subset. No model was rerun, no asset
was downloaded, and CONCH/UNI remain metadata-only and non-executable.

## Phase 5.13B Hospital Connector Control Alpha

An independent loopback-only Hospital Connector now supports one-time
enrollment, Operator-reviewed registration, Local Test CA client certificates,
mTLS heartbeat, versioned disabled-capability manifests, pause/resume,
new-key certificate rotation and revocation. Connector state and keys remain
under D-drive local storage.

This is not hospital production PKI or a data node. It transfers no data or
model, executes no task, creates no Artifact, and remains
`hard_isolation=false`. See
`docs/Phase5.13B-hospital-connector-control-acceptance.md`.

## Phase 5.13C Metadata-only Local Asset Registry

The Connector now registers public/synthetic metadata, immutable versions,
minimum quality summaries and separate local reviews, then synchronizes only an
approved metadata bundle through mTLS. Central stores a non-requestable,
non-executable mirror. No raw data, path, patient identifier, model, Job, Run,
Artifact, product, materialization plan, or MinIO object is created.
`hard_isolation=false`.
# Phase 5.13C-A1

Hospital Connector 已具备 loopback-only 的本地 curator/reviewer 双角色工作台，
可通过浏览器完成 metadata-only Asset、Version、Quality、Review、Bundle 和
mTLS 中央镜像。该能力不等于医院生产 IAM 或硬隔离，`hard_isolation=false`。
正式证据见 `docs/Phase5.13C-A1-formal-browser-acceptance.md`。

## Phase 5.13D Signed Policy Control

The central platform can compile and sign a control-only PolicyBundle and issue
a signed `CONTROL_VALIDATION_ONLY` order. The Hospital Connector pulls it over
mTLS, independently verifies it, and records a local accept or reject with a
signed receipt and decision.

Accepted means control validation only, not execution. This phase transfers no
raw data or model weights, creates no Job/Run/Artifact, and keeps
`execution_authorized=false` and `hard_isolation=false`. See
`docs/Phase5.13D-policy-control-acceptance.md`.

## Phase 5.13E-0 Controlled Execution Architecture Freeze

The hospital-side execution architecture and security boundary are now frozen
as documentation only. The target separates Connector policy mediation,
independent local execution approval, Executor Manager, no-network sandbox,
local Artifact quarantine, hospital output review, and signed EvidenceBundle
egress.

No Executor, container, execution object, migration, API, model run, data read,
or Artifact was created. The first eventual scope is fixed PathMNIST plus fixed
ResNet-18 on the existing public/non-clinical demonstration subset.
`execution_enabled=false` and `hard_isolation=false`. See
`docs/Phase5.13E-0-acceptance.md`.

## Phase 5.13E-2C-R1 Pre-authorized Reference Execution

One new fixed `PATHMNIST_REFERENCE_V1` execution was authorized before Task
creation through fresh Status v2, Readiness, Policy, Order, independent local
review, and an immutable one-use Snapshot. The 20-sample non-clinical run
completed with 19 correct predictions and aggregate accuracy 0.95.

The new local Artifact remains `quarantined` with exactly three aggregate
files. It has not been scanned, reviewed, bundled, registered centrally,
released, or downloaded. No raw data, local path, patient identifier, or model
weight was transferred. `hard_isolation=false`; R2 and R3 have not started.
See `docs/Phase5.13E-2C-R1-acceptance.md`.
