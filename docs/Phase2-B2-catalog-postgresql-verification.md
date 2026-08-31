# Phase 2-B.2.3 Catalog PostgreSQL 16 集成验证

## 1. 验证结论

当前状态：**PostgreSQL 16 实库验证通过。**

2026-07-22 在当前开发机完成以下真实环境验证：

- Docker Desktop 4.83.0 使用 WSL 2 后端，Docker Engine 29.6.2；
- Docker Desktop 程序位于 `D:\Apps\DockerDesktop`；
- WSL 镜像、容器和卷的数据根位于 `D:\DockerData`；
- PostgreSQL 服务端版本为 16.14，容器健康检查通过；
- MinIO live 健康检查返回 HTTP 200；
- Backend live/ready 健康检查均返回 HTTP 200；
- 专用验证库为 `medtrust_catalog_verify`；
- 开启并发与升降级开关后的完整结果为 **32 passed、0 failed、0 skipped**。

验证覆盖 PL/pgSQL、JSONB、复合外键、CHECK、部分唯一索引、并发发布和 Alembic 真实升降级。最终 Alembic head 已恢复为 `20260722_0004`。

## 2. 范围与边界

本阶段没有新增 Catalog ORM、领域服务、API 或下游业务模块。验证新增：

- `backend/tests/integration/test_catalog_postgresql.py`
- `backend/tests/integration/test_zz_catalog_migration_cycle_postgresql.py`
- `docs/Phase2-B2-catalog-postgresql-verification.md`

实库验证发现并修复了两个开发阶段问题：

1. Catalog migration 的 CHECK 名称提前带完整前缀，被 SQLAlchemy naming convention 再次加前缀并截断，造成 ORM metadata 与实库名称漂移；现已改为使用约束局部名，由 naming convention 生成最终名称。
2. Backend 容器的热重载扫描整个 `/app`，会触碰 Windows 挂载目录中的 `.pytest_cache` 并产生权限错误；Compose 已将监听范围收窄到 `/app/app`。
3. 开发凭据只适合本机，Compose 的 PostgreSQL、MinIO 和 Backend 端口已限制绑定 `127.0.0.1`，不对局域网开放。

测试必须使用**专用、可丢弃的 PostgreSQL 16 数据库**。并发发布测试会提交种子记录和一条获胜的 Publication；升降级循环会删除并重建 Catalog 五表，禁止对开发共享库、演示库或生产库运行。

## 3. 关键技术澄清

### 3.1 JSONB 类型不等于 JSON Schema 约束

当前 migration 将扩展元数据声明为 PostgreSQL `jsonb`，数据库能保证值是合法 JSONB，但没有用数据库 CHECK 验证 `schema_version`、属性集合或业务结构。

当前 `schema_version` 规则由 `submit_version_for_review()` 在应用层校验。因此验证分为两项：

1. PostgreSQL 实库确认列类型为 `jsonb`，并验证嵌套对象、数组和数字可往返读取；
2. Catalog 领域服务确认缺失或类型错误的 `schema_version` 无法提交审查。

不能把第 2 项表述为“PostgreSQL JSONB Schema 约束通过”。若未来要求数据库层 JSON Schema 保证，需要单独评审扩展、CHECK 函数或生成列方案，不能在本验证阶段暗中改变 migration。

### 3.2 发布并发由数据库唯一索引兜底

领域服务会先查询是否已有 active Publication，但该查询不能独立解决并发竞态。最终一致性由两个 PostgreSQL 部分唯一索引保证：

- `uq_publications_active_product`
- `uq_publications_active_version`

并发测试使用两个独立事务同时为同一产品的两个 approved 版本创建 active Publication，预期恰好一个提交成功，另一个收到唯一冲突。

## 4. 验证矩阵

| 验证项 | 测试位置 | 预期结果 | 当前结果 |
|---|---|---|---|
| `alembic upgrade head` | `test_zz_catalog_migration_cycle_postgresql.py` | head 为 `20260722_0004`，Catalog 五表存在 | 通过 |
| Catalog 五表 | `test_catalog_schema_objects_exist_on_migrated_postgresql` | 五表全部位于 `medtrust` schema | 通过 |
| 复合外键 | `test_catalog_foreign_keys_and_checks_reject_invalid_rows` | 跨 Space Version 被数据库拒绝 | 通过 |
| CHECK 约束 | 同上 | 非法状态、非正版本号被拒绝 | 通过 |
| PL/pgSQL 函数和触发器安装 | `test_catalog_schema_objects_exist_on_migrated_postgresql` | 4 函数、4 触发器存在 | 通过 |
| Version 不可变 | `test_catalog_plpgsql_guards_reject_direct_sql_tampering` | under_review/approved 直接 SQL 篡改被拒绝 | 通过 |
| Resource/Source 不可变 | 同上 | 非 draft 父版本下修改被拒绝 | 通过 |
| Publication 生命周期 | 同上 | 未批准版本发布、身份修改和物理删除被拒绝 | 通过 |
| JSONB 类型与往返 | `test_catalog_jsonb_round_trip_and_service_schema_validation` | 实库类型为 jsonb，嵌套数据无损 | 通过 |
| `schema_version` 业务校验 | 同上 | 应用服务拒绝错误类型 | 通过 |
| 单 active Publication | `test_catalog_partial_unique_indexes_allow_only_one_active_publication` | 第二条 active Publication 唯一冲突 | 通过 |
| 并发发布竞争 | `test_concurrent_publication_attempts_have_one_winner` | 两个事务恰好一个成功 | 通过 |
| `alembic downgrade 20260722_0003` | `test_zz_catalog_migration_cycle_postgresql.py` | Catalog 五表移除，随后恢复 head | 通过 |

## 5. 环境准备

以下示例使用项目 Compose 中的 PostgreSQL 16，并创建独立验证库。命令只应在确认该 Compose 项目不承载需保留数据后执行。

```powershell
cd '<repo-root>'
docker compose up -d postgres
docker compose exec postgres createdb -U medtrust medtrust_catalog_verify

$env:MEDTRUST_DATABASE_URL = 'postgresql+asyncpg://medtrust:medtrust_dev_only@127.0.0.1:5432/medtrust_catalog_verify'
$env:MEDTRUST_TEST_DATABASE_URL = $env:MEDTRUST_DATABASE_URL

cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

若使用外部 PostgreSQL 16，只需将两个环境变量指向同一个专用空数据库。不要使用 SQLite 代替本轮验证。

## 6. 执行顺序

### 6.1 非破坏性 PostgreSQL 行为测试

除并发用例外，这些测试全部使用外层事务并在结束时回滚。

```powershell
cd '<repo-root>\backend'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_catalog_postgresql.py -m integration -q
```

未开启并发开关时，预期 5 个测试执行、1 个并发测试跳过。

### 6.2 并发发布测试

该测试会提交数据，只能在可丢弃测试库执行。

```powershell
$env:MEDTRUST_RUN_CATALOG_CONCURRENCY_TEST = '1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_catalog_postgresql.py -m integration -q
```

### 6.3 升降级循环

该测试先确保升级到 head，再降到 Connector revision，最后在 `finally` 中恢复 head。它会删除 Catalog 五表及其中数据。

```powershell
$env:MEDTRUST_RUN_CATALOG_MIGRATION_CYCLE = '1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_zz_catalog_migration_cycle_postgresql.py -m integration -q
```

### 6.4 全量验证

```powershell
$env:MEDTRUST_RUN_CATALOG_CONCURRENCY_TEST = '1'
$env:MEDTRUST_RUN_CATALOG_MIGRATION_CYCLE = '1'
.\.venv\Scripts\python.exe -m pytest -m integration -q
```

本次执行已保存 PostgreSQL 版本、命令退出码和 pytest 汇总；全部用例通过后已将第 1 节更新为“实库验证通过”。

## 7. 实库验收记录

本次最终验收结果：

- 两个新增测试文件通过 Python 编译；
- pytest 成功收集 40 个测试，其中 PostgreSQL 集成测试 10 个；
- 使用 PostgreSQL 16.14 并开启两个显式开关后，完整测试为 32 passed、0 failed、0 skipped；
- 并发发布测试得到一个成功事务和一个唯一冲突事务；
- migration cycle 成功降至 `20260722_0003`，Catalog 五表消失，随后恢复 `20260722_0004`；
- PostgreSQL、MinIO 和 Backend 三个 Compose 服务均保持运行；
- 没有新增 Application、Contract、Compute、Audit、API 或通用 CRUD。

这些结果证明当前 Catalog migration 和领域约束已在本机 PostgreSQL 16 环境实际执行，而不再只是离线 SQL 检查。

## 8. 进入 Application 域的门槛

进入 Application 域前至少需要补录以下实库证据：

1. PostgreSQL 服务端版本为 16.x；
2. migration cycle 通过且最终 head 恢复为 `20260722_0004`；
3. Catalog 非破坏性 PostgreSQL 测试全部通过；
4. 并发测试结果恰好一个 `published`、一个 `conflict`；
5. 明确接受 V1 的 `schema_version` 为应用层规则，或另行评审数据库层 JSON Schema 方案。

以上五项证据均已形成。V1 继续采用“PostgreSQL 保证 JSONB 类型、领域服务保证 `schema_version` 业务结构”的边界。

当前结论：**Catalog 数据资产层通过 PostgreSQL 16 实库验证，可以进入 Application 数据流通层设计。**
