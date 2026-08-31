# MedTrust Space Backend

MedTrust Space 的模块化单体后端。当前完成工程骨架，以及 Identity、Spaces、Connectors、Catalog、Application、Review、Contract、演示签署/生效守卫和 Compute/Artifact 的分批数据库实现。

当前已经包含 FastAPI 应用入口、异步 SQLAlchemy 会话、Alembic 环境、PostgreSQL 16/MinIO 开发编排、九个业务模块包，以及累计36张 ORM 表和十五批 migration。Alembic head为`20260722_0015`。Contract 已具备 Core、Policy/Constraint/Binding、演示 Signature及合法active图真实COMMIT守卫；Compute 已具备稳定 Job、单次 Run、双重授权评估、PostgreSQL 原子 `run_count` 预留、默认隔离 Artifact 与单终态 ArtifactReview；Audit已具备按Space不可变哈希链、事务型Outbox、五类关键命令同事务证据，以及独立Outbox Dispatcher。Dispatcher当前只有测试/本机Publisher，没有Compute或Artifact业务消费者，因此真实 Run 运行和 Artifact 发布仍被固定 fail-closed。当前没有 JWT 登录、业务 CRUD/API、真实电子签名、真实连接器通信、真实对象上传、第三方可信存证、隐私计算或用户代码执行。

## 目录

```text
backend/
├── app/
│   ├── api/                 # 系统级 HTTP 路由
│   ├── core/                # 环境配置
│   ├── db/                  # SQLAlchemy Base、Engine、Session
│   ├── messaging/           # Envelope 与 Publisher 抽象
│   ├── modules/
│   │   ├── identity/
│   │   ├── spaces/
│   │   ├── connectors/
│   │   ├── catalog/
│   │   ├── applications/
│   │   ├── reviews/
│   │   ├── contracts/
│   │   ├── compute/
│   │   └── audit/
│   ├── workers/             # 独立 Outbox Dispatcher
│   └── main.py
├── alembic/                 # Identity 到 ArtifactReview 的分批 revisions
├── tests/
├── .env.example
├── alembic.ini
├── Dockerfile
└── pyproject.toml
```

## 本地启动

要求 Python 3.12 和可访问的 PostgreSQL 16。

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

本机Dispatcher协议演示（只使用进程内Publisher，不代表外部可靠投递）：

```powershell
$env:MEDTRUST_OUTBOX_PUBLISHER="in_memory"
python -m app.workers.outbox_dispatcher
```

默认`unavailable`配置会在领取消息前拒绝启动，避免误标已投递或消耗重试次数。

访问：

- API 文档：`http://127.0.0.1:8000/docs`
- 存活检查：`http://127.0.0.1:8000/api/v1/health/live`
- 就绪检查：`http://127.0.0.1:8000/api/v1/health/ready`

`live` 仅说明 API 进程可响应；`ready` 会执行 `SELECT 1`，数据库不可用时返回 503。

## Compose 开发环境

在项目根目录执行：

```powershell
Copy-Item backend\.env.example backend\.env
docker compose up --build
```

默认端口：

| 服务 | 地址 |
|---|---|
| Backend | `http://127.0.0.1:8000` |
| PostgreSQL | `127.0.0.1:5432` |
| MinIO API | `http://127.0.0.1:9000` |
| MinIO Console | `http://127.0.0.1:9001` |

`.env.example` 中的凭据仅供本机演示，不得用于共享或生产环境。

## Alembic

查看当前数据库 migration 状态：

```powershell
cd backend
alembic current
```

后续域应继续基于已审查的冻结文档分批建立模型和 revision，并在每批迁移后验证升级、降级、外键、索引和非法状态拒绝。

Identity、Spaces、Connectors、Catalog、Application、Review、Contract、ComputeJob/ComputeRun 和 Artifact/ArtifactReview migration 已建立。使用专用测试数据库验证时：

```powershell
cd backend
$env:MEDTRUST_DATABASE_URL="postgresql+asyncpg://.../medtrust_test"
$env:MEDTRUST_TEST_DATABASE_URL=$env:MEDTRUST_DATABASE_URL
alembic upgrade head
pytest -m integration
alembic downgrade base
alembic upgrade head
```

这些命令只能指向可清理的专用测试库，不能对共享或生产数据库执行 downgrade。

## 验证

```powershell
cd backend
pytest
```

## 明确边界

- MinIO 服务只为后续演示对象准备；当前后端未读写 MinIO。
- Compute 当前实现 Job/Run 元数据、授权重评估、额度约束、隔离 Artifact 与 ArtifactReview；关键数据库命令已接入Audit/Outbox，Dispatcher只负责通用投递。因为执行协调器和发布消费者尚未实现，真实运行和Artifact发布仍保持fail-closed，也不会执行任何算法或用户代码。
- 当前 Signature 只是演示签署证据，不包含 CA、私钥或法律效力验证；身份认证和审计存证也尚未实现，不构成生产级可信或合规证明。
- 参与方角色属于 SpaceParticipant 上下文，不是用户全局 RBAC；授权服务尚未实现。
- Connector 当前仅表示空间内技术节点注册及能力声明，不代表已完成真实节点接入或可信能力核验。
- Catalog 当前保存数据产品元数据、摘要和 Connector 本地别名，不保存真实医疗数据、路径或访问凭据。
- Application 当前实现申请、版本项、请求动作、请求输出类型、附件元数据、提交快照和数据库不变量，不授予数据访问。
- Review 当前实现 ApplicationSnapshot 审核任务、领取/取消/决定状态、追加式 Decision，以及 Contract 准入证据重算；尚未实现通用规则引擎、审核 API 或持久化 Application 汇总表。
- Contract 当前已实现稳定系列、Revision、Party、固定版本 Object、Policy、类型化 Constraint、ConnectorCapability Binding、演示 Signature 和 signed/active 门禁。`active` 只表示当前治理与执行前提通过校验，不产生原始数据下载权，也不表示已经执行 Compute。
- Artifact 只能来自 succeeded Run，创建后固定为 quarantined；ArtifactReview approved 只形成审核证据，不等于 released，也不提供下载地址或对象存储访问权。

`reviews` 有意保持只面向 ApplicationSnapshot。Artifact 出域审核使用独立、带真实 Artifact 复合外键的 `artifact_reviews`，没有把既有审核表改造成无约束的多态目标。
