# Phase 5.0 可复现路演基线与前端请求稳定性交付

更新日期：2026-07-24

## 1. 交付结论

Phase 5.0 已完成。当前交付冻结的是完整 Phase 4 多主体路演实现，加上请求生命周期稳定、脚本可移植、只读预检和可重复启动能力。

本阶段没有实现 Phase 5.1 表单，没有新增 API、AuditEvent 词汇、migration、状态机或证据面板，也没有修改权限、`run_count`、Artifact 隔离、数据库触发器或约束。

Phase 66 的准确状态仍是 `planning_audit_passed`，不能称为“Phase 5 已完成”。

## 2. Git 与数据库基线

- 开始本阶段前的已提交基线：`dd87757 feat: deliver Phase 3 real backend demo`
- 已有历史标签：`v0.2-controlled-smoke`
- Phase 5.0 冻结标签：`v0.3-phase5.0-baseline`
- Alembic head：`20260723_0025`
- `medtrust` schema 业务表：48
- Phase 4 专用数据库：`medtrust_phase4_demo`
- 后端完整回归：137 passed，5 skipped
- OpenAPI：43 paths

`v0.3-phase5.0-baseline` 指向包含完整 Phase 4 和 Phase 5.0 的交付提交；本文件不硬编码提交 SHA，避免文档内容与其自身提交哈希形成循环依赖。

## 3. 修改范围

### 前端请求稳定

- 新增 `frontend/src/roadshow/requestLifecycle.ts`
- 新增 `frontend/tests/requestLifecycle.test.mjs`
- 更新 `RoadshowContext.tsx`
- 更新 `RoadshowPages.tsx`
- 更新 `frontend/package.json`

### 可移植脚本

- 新增 `scripts/phase4_demo_common.ps1`
- 新增 `scripts/preflight_phase4_demo.ps1`
- 新增 `config/phase4-demo.example.env`
- 更新 Phase 3/Phase 4 start、stop、reset 脚本
- 更新 `.gitignore`，排除 `config/phase4-demo.env`

### 状态与交接

- 更新 README、CHANGELOG、计划记录和 `docs/project_handoff/`
- 新增本交付报告

## 4. Strict Mode 根因与修复

React Strict Mode 在开发环境会执行挂载、清理和重新挂载。原目录页面在第一次 effect cleanup 中主动中止请求，但把该取消当作普通错误写入状态；第二次请求即使成功，也可能保留旧错误。

现在统一通过 `startAbortableLoad` 管理 GET 生命周期：

- 只忽略 `AbortError`、`CanceledError` 和 `ERR_CANCELED`
- 普通网络错误、401、403、500 继续进入错误状态
- cleanup 后不再更新已卸载组件
- 新请求开始时清理旧错误
- Strict Mode 保持启用

写命令和结果下载使用 `createSingleFlight`，同一个 pending 操作不会重复提交。

## 5. 启动路径解析

脚本通过 `$PSScriptRoot` 推导仓库根目录，不硬编码工作区绝对路径。

运行时解析顺序：

1. 进程环境变量覆盖
2. `PATH`
3. 仓库内可用 fallback

PathMNIST 和固定模型资产只通过显式环境变量、忽略提交的本地配置或仓库相对候选解析。提交脚本不包含当前 Codex 账号目录或本机资产绝对路径。

Windows 下 Vite 使用 frontend 工作目录中的相对入口启动，避免包含空格和中文的绝对脚本参数被 `Start-Process` 拆分。启动成功同时要求后端 ready API 和前端 `/demo-login` 返回成功。

## 6. 本地配置

从示例创建本地配置：

```powershell
Copy-Item .\config\phase4-demo.example.env .\config\phase4-demo.env
```

必需变量：

```text
MEDTRUST_PATHMNIST_DATASET_PATH
MEDTRUST_PATHMNIST_MODEL_PATH
```

可选变量：

```text
MEDTRUST_NODE
MEDTRUST_BACKEND_PYTHON
MEDTRUST_EXECUTOR_PYTHON
MEDTRUST_PHASE4_DATABASE_NAME
MEDTRUST_PHASE4_DATABASE_URL
MEDTRUST_PHASE4_CONFIG
```

`config/phase4-demo.env` 被 Git 忽略，不得写入密码、Token 或其他需要提交的秘密。

## 7. 预检与运行

只读预检：

```powershell
.\scripts\preflight_phase4_demo.ps1
```

预检覆盖 Docker、Compose、Node、pnpm、两套 Python、前端依赖、资产存在性、端口、PostgreSQL、MinIO 和 Phase 4 Alembic current。预检不迁移数据库、不重置数据、不删除文件。

启动：

```powershell
.\scripts\start_phase4_demo.ps1
```

停止、重置、重新启动：

```powershell
.\scripts\stop_phase4_demo.ps1
.\scripts\reset_phase4_demo.ps1
.\scripts\start_phase4_demo.ps1
```

入口：

- `http://127.0.0.1:5173/demo-login`
- `http://127.0.0.1:8000/docs`

## 8. 验证结果

### 前端

```text
request lifecycle tests: 4 passed
pnpm typecheck: passed
pnpm build: passed
Vite modules transformed: 3694
```

覆盖：

- 主动取消不显示错误
- 普通网络错误正常显示
- 卸载后晚到响应不更新状态
- pending 写请求只提交一次

### 后端与数据库

```text
Python compileall: passed
OpenAPI generation: passed, 43 paths
Empty database migration: base -> 20260723_0025
Business tables after migration: 48
Backend regression: 137 passed, 5 skipped
```

测试使用独立临时数据库 `medtrust_phase50_test`，没有使用 Phase 4 演示数据库承载回归。

### 三次运行循环

连续三次完成：

```text
stop -> reset -> start -> health check
```

每次均确认：

- `/demo-login` 返回 HTTP 200
- OpenAPI 包含 43 个路径
- 四个角色 overview 可读取
- 数据和模型目录均包含种子记录
- 重置后目录状态恢复为 `draft/draft`

### 浏览器与错误语义

- 四个角色工作台均可进入
- `/data-catalog` 和 `/model-catalog` 不再显示主动 Abort 错误
- 后端临时停止时，页面显示“无法读取路演状态 / Failed to fetch”
- 后端恢复后真实错误消失

这证明修复没有通过吞掉所有异常来隐藏问题。

## 9. 已知限制

- `hard_isolation=false`
- Executor、Coordinator 和数据库 Inbox publisher 仍是单机工程演示
- 本地资产必须由接手者自行提供并配置
- 前端生产构建仍有现有的大 chunk 提示
- 本机 pnpm 全局 store 与当前 `node_modules` 所用 store 可能不同；本次验证使用现有 D 盘 store
- 后端 pytest 可能因本机 `.pytest_cache` 权限产生缓存警告，不影响测试结果
- 当前不是临床验证、生产级隐私计算、真实医院接入或国家测评结果

## 10. 回滚

代码回滚应以 Git 提交或 `v0.3-phase5.0-baseline` 标签为边界，不修改历史 migration。

运行环境回滚：

```powershell
.\scripts\stop_phase4_demo.ps1
```

演示数据恢复：

```powershell
.\scripts\reset_phase4_demo.ps1
```

本地配置或资产路径变更只修改被忽略的 `config/phase4-demo.env`，不要把本机路径写回提交脚本。

## 11. 下一阶段

下一阶段候选为 Phase 5.1：医院数据方新建数据产品的完整垂直切片。

该阶段尚未开始，必须单独授权，并继续遵守：

- 不绕过领域服务和状态机
- 不修改历史 migration
- 不把前端角色切换当作授权
- 不削弱 Artifact 隔离和结果出域审核
- 不把技术日志冒充可持久审计证据
