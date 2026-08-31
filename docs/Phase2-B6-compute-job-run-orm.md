# Phase 2-B.6-B1 ComputeJob / ComputeRun ORM 实现说明

> 状态：已实现并通过 PostgreSQL 16 验证。本文只描述 `compute_jobs`、`compute_runs` 和 `20260722_0011`；当前仓库已推进到`20260722_0013`并在后续批次实现隔离Artifact/ArtifactReview，但仍未实现Audit/outbox、执行器、API或真实计算。

## 1. 本批结论

Phase 2-B.6-B1 将 v7 冻结的“稳定计算意图”和“单次执行尝试”落为两张 SQLAlchemy 2.0 typed ORM 表。B1验收时为32张实表；当前Alembic head为`20260722_0013`、共34张实表，新增两表属于后续Artifact批次，不改变本页的Job/Run边界。

```text
ACTIVE ContractRevision
  -> ComputeJob（稳定意图与创建时授权证据）
  -> ComputeRun（一次尝试与启动时重评估）
  -> Audit/outbox gate（当前固定 fail-closed）
```

本批没有新增结果审核、发布或出域状态，也没有保存患者数据路径、Connector 凭据、访问令牌或可执行用户代码。

## 2. 实现文件

| 文件 | 作用 |
| --- | --- |
| `backend/app/modules/compute/models.py` | `ComputeJob`、`ComputeRun` typed ORM、状态词表、复合 FK、唯一约束与索引 |
| `backend/app/modules/compute/services.py` | 双重授权评估、Job 创建/验证、Run 准备/取消/预留、Session 不变量与 Audit fail-closed |
| `backend/app/modules/compute/__init__.py` | Compute 对象与受控命令导出 |
| `backend/alembic/env.py` | 将 Compute 模型纳入迁移元数据 |
| `backend/alembic/versions/20260722_0011_compute_jobs_runs.py` | 两表、PL/pgSQL 触发器、原子次数预留和临时 Audit 门 |
| `backend/tests/test_compute_models.py` | SQLite 快速领域测试 |
| `backend/tests/integration/test_compute_postgresql.py` | PostgreSQL 触发器、直接 SQL、并发和回滚专项测试 |
| `backend/tests/integration/test_zz_catalog_migration_cycle_postgresql.py` | 0011 升降级循环 |

## 3. ComputeJob

`ComputeJob` 固定以下权威内容：

- Space、Contract、ACTIVE ContractRevision 和 Revision 内容摘要；
- consumer ContractParty、请求组织、请求用户和同一 Revision 的 ContractObject；
- 规范化输出类型；
- `algorithm_spec_snapshot`、`compute_input_snapshot` 及各自 SHA-256；
- 创建时授权评估、摘要和创建请求摘要。

Job 创建前由领域服务读取当前 Application、Contract、Policy、Constraint、Binding、Connector 和 Capability；INSERT 时 PostgreSQL 触发器再次校验 Revision、Party、Object、成员、空间参与关系和产品版本。意图与证据列创建后不可修改，终态不可更新或删除。

## 4. ComputeRun

`ComputeRun` 表达同一 Job 的一次尝试：

- `attempt_no` 在 Job 内单调递增；
- 同一 Job 同时最多一个非终态 Run；
- `prepared` 不包含额度、Binding 或环境证据；
- `prepared -> reserved` 时固定 Policy、run-count Constraint、三类 Binding、环境快照及启动授权评估；
- `failed`、`interrupted`、`cancelled`、`timed_out` 等终态保留历史，不以修改旧 Run 方式重试。

当前允许创建 `prepared` Run；由于 Audit/outbox 尚未实现，生产路径无法进入 `reserved`、`dispatched` 或 `running`。

## 5. run_count 原子预留

数据库在 `prepared -> reserved` 的同一事务中：

1. 锁定 Job；
2. 重新校验 ACTIVE Revision、固定对象和产品版本；
3. 锁定唯一 governing permit Policy；
4. 验证 `run_count/lte/count` Constraint；
5. 验证 compute、egress、audit 三项 Binding、Connector 在线状态和精确 `1.0` Capability；
6. 在 `Revision + Policy + Party + Object` 作用域分配 `max(reservation_ordinal) + 1`；
7. 通过部分唯一索引防止序号复用，并由触发器拒绝超限。

额度语义保持 v7：预留事务整体回滚不留下序号；一旦预留事务成功，后续失败、取消或中断均不返还次数。

并发专项在限额 1 的情况下启动两个独立事务，结果为一个成功获得序号 1、一个被 PostgreSQL 以 `run_count quota is exhausted` 拒绝。专项测试为了单独触达额度算法，会临时替换 Audit 门函数，并在 `finally` 中恢复固定拒绝；应用服务和生产迁移没有 Audit 旁路。

## 6. Audit fail-closed

`medtrust.assert_compute_audit_ready_v7()` 当前始终抛出 `AuditEvidenceUnavailable`。因此：

- Job 可以创建和重新验证；
- Run 可以创建为 `prepared` 或在预留前取消；
- 真实预留、下发和运行均被数据库拒绝；
- 普通日志、控制台输出或模拟事件不能替代事务型 Audit/outbox。

后续只有在 Audit/outbox 与状态变化同事务提交后，才能用新的受控 migration 替换该临时门函数。

## 7. PostgreSQL 时间语义

0011 的当前有效性和心跳判断使用 `clock_timestamp()`，而不是事务开始时固定的 `CURRENT_TIMESTAMP`。这是为了避免长事务内刚创建的成员资格、合同有效期或心跳被误判为“尚未生效”；数据库分配的预留时间也使用真实墙钟时间。

## 8. 验证结果

- Compute 快速领域测试：7/7 通过；
- Compute PostgreSQL 专项：3/3 通过（包含真实并发开关）；
- PostgreSQL 全域集成回归：24/24 通过；
- `0011 -> 0010 -> 0011` 真实迁移循环：通过；
- B1验收实表：32；当前仓库实表：34；
- 当前 Alembic head：`20260722_0013`（ComputeJob/Run仍由0011创建）。

全量后端回归：80/80 通过（已开启 PostgreSQL 集成、Catalog/Compute 并发和迁移循环开关）。

## 9. 已解决的上游Contract问题

0010 的 `guard_contract_revision_signed_consistency_v6()` 曾同时挂在`contract_revisions`和`contract_signatures`，导致Revision触发上下文在COMMIT时解析不存在的`NEW.contract_revision_id`并报SQLSTATE 42703。

该问题已由独立`20260722_0012_contract_active_commit_fix`纠正：两个表使用各自v7触发入口并复用同一签名一致性helper。Compute并发测试已删除临时禁用触发器的绕行，合法active Contract与ComputeJob可以真实COMMIT；Audit门仍保持fail-closed。详见[Contract Active Commit Hotfix](Phase2-B5-contract-active-commit-hotfix.md)。

## 10. B1 当时未实现及当前边界

- `artifacts`、`artifact_reviews`已在后续0013实现，详见[Artifact / ArtifactReview ORM实现说明](Phase2-B6-artifact-review-orm.md)；
- AuditEvent、transactional outbox 或哈希链；
- Run 下发、Worker、执行器和任务队列；
- 用户算法上传或真实病理模型运行；
- Compute API 和前端接入；
- 原始数据下载、结果自动出域或访问令牌签发。
