# Phase 2-B.7-D 关键命令 Audit/Outbox 同事务接入

> 状态：已完成并通过 PostgreSQL 16 验证  
> 日期：2026-07-22  
> Alembic head：`20260722_0015`  
> 实表：36 张（本阶段未新增表）

## 1. 阶段结论

本阶段已将以下五个 PostgreSQL 权威命令接入不可变 `AuditEvent` 与事务型 `OutboxMessage`：

1. ContractRevision 激活；
2. ComputeJob 创建；
3. ComputeRun 原子预留；
4. Artifact 创建；
5. ArtifactReview 终态决定。

每条命令均在调用方同一个 `AsyncSession` 事务内完成：

```text
业务事实变化
+ AuditEvent
+ OutboxMessage
```

业务服务和审计服务均不自行 `commit`。领域服务只执行校验、`flush` 和事件追加；事务成功后由调用方统一提交。若业务对象已 `flush` 后事件或 Outbox 构造失败，命令服务会回滚当前 Session，避免调用方误捕获异常后提交部分业务事实。

## 2. 已接入命令与事件

| 命令 | Event type | Subject | Outbox 目标 | 当前结果 |
|---|---|---|---|---|
| ContractRevision 激活 | `contract.revision.activated` | `contract_revision` | `audit.timeline` | 已接入 |
| ComputeJob 创建 | `compute.job.created` | `compute_job` | `audit.timeline` | 已接入 |
| ComputeRun 预留 | `compute.run.reserved` | `compute_run` | `audit.timeline`、`compute.dispatch` | 已接入 |
| Artifact 创建 | `artifact.created` | `artifact` | `audit.timeline`、`artifact.review-routing` | 已接入 |
| ArtifactReview 决定 | `artifact.review.decided` | `artifact_review` | `audit.timeline`、`artifact.release-evaluation` | 已接入 |

事件证据只保存最小业务摘要，例如合同版本摘要、授权评估摘要、Policy/Binding 标识、内容摘要和决定摘要。不保存患者数据、真实病理路径、Connector 凭据、对象存储密钥、临时访问令牌或下载链接。

## 3. 事务边界

### 3.1 调用层职责

- 调用方提供一个稳定的 `AuditCommandContext`；
- `AuditCommandContext` 固定 `command_id`、幂等键摘要、`correlation_id`、`causation_id` 和 Actor；
- 同一重试必须复用同一个命令上下文；
- 业务命令、AuditEvent 和全部 OutboxMessage 复用同一个 Session；
- 审计服务不创建第二个 Session，不开启独立事务，不提交事务。

### 3.2 失败语义

已验证：

- 业务对象已 flush、AuditEvent 追加失败：业务对象回滚；
- AuditEvent 已构造、Outbox payload digest 失败：业务对象和 AuditEvent 一并回滚；
- ComputeRun 事件已 flush、run_count 或数据库守卫拒绝：事件、Outbox 和 Run 变化一并回滚；
- 回滚后调用方再次 `commit`，也不会留下已 flush 的部分业务事实。

## 4. 幂等与并发

### 4.1 命令预检

新增 `begin_audited_command`，在创建业务对象之前完成：

1. 锁定对应 Space 行；
2. 校验 `command_id`、幂等键和 `correlation_id` 的既有映射；
3. 计算 canonical request digest；
4. 查找相同 event type 的已提交命令事实；
5. 精确重试时返回既有 Subject；
6. 相同幂等键但请求摘要不同则抛出 `IdempotencyConflict`。

Space 行同时是审计链序号锁。创建型命令在业务对象插入前先获得该锁，因此两个并发重试不会各自创建一个业务对象。PostgreSQL 双会话测试已验证：相同 ComputeJob 命令并发提交只产生一个 Job、一个 AuditEvent 和一组 OutboxMessage。

### 4.2 run_count

ComputeRun 仍使用 0011 冻结的数据库原子序号与 Policy 行锁方案。专项测试验证：

- `run_limit=1` 时两个独立事务并发预留，只有一个成功；
- 事务回滚不会留下幽灵序号或审计事件；
- 成功预留后的失败语义仍遵循 v7，不回收已消耗序号；
- 未写入对应 AuditEvent/Outbox 的直接 SQL 预留被数据库拒绝。

## 5. 为什么需要 0015

最初目标是保持 Alembic head 0014，但真实检查发现 0011 中的：

```text
assert_compute_audit_ready_v7()
```

会无条件拒绝 `prepared -> reserved`。仅修改应用服务无法解除该数据库硬门禁，因此新增纠正迁移：

```text
20260722_0015_critical_command_audit_gate
```

0015：

- 不新增或删除表；
- 不修改 0010—0014 历史迁移；
- 新增 `assert_compute_run_reservation_audit_v8(run_id)`；
- 只把 `prepared -> reserved` 的第一次硬门禁替换为事务证据检查；
- 要求同一事务已存在一个 `compute.run.reserved` 事件，以及 `audit.timeline` 和 `compute.dispatch` 两个 Outbox 目标；
- 保留后续 `dispatched/running/succeeded` 对原硬门禁的调用。

数据库检查结果：

```text
reservation v8 gate calls = 1
legacy real-execution hard-gate calls = 1
tables = 36
```

## 6. 仍然 fail-closed 的能力

### 6.1 ComputeRun 真实执行

本阶段只允许 Run 进入 `reserved`。Outbox 中的 `compute.dispatch` 只是可靠待投递请求，不代表执行器已经启动。

以下事件仍未接入命令：

- `compute.run.started`
- `compute.run.completed`
- `compute.run.failed`
- `compute.run.interrupted`

`dispatched/running/succeeded` 仍被数据库硬门禁阻断，必须等待 Dispatcher 和执行器确认协议。

### 6.2 Artifact 发布

当前 `released` 表示制品已经完成对外发布，而不是内部审核通过。因此：

- `artifact.review.decided` 已可靠落库；
- 审核通过仍不等于 released；
- `release_artifact` 继续抛出 `AuditEvidenceUnavailable`；
- 0013 的 Artifact release 数据库门禁保持不变；
- 本阶段没有生成下载链接，也没有开放对象存储访问。

## 7. 主要代码位置

| 路径 | 作用 |
|---|---|
| `backend/app/modules/audit/services.py` | `AuditCommandContext`、命令预检、并发幂等和现有事件追加服务 |
| `backend/app/modules/contracts/services.py` | ContractRevision 激活及原子审计接入 |
| `backend/app/modules/compute/services.py` | Job、Run、Artifact、ArtifactReview 四类命令接入及回滚守卫 |
| `backend/alembic/versions/20260722_0015_critical_command_audit_gate.py` | 预留事务证据数据库门禁 |
| `backend/tests/integration/test_critical_command_audit_postgresql.py` | 五命令证据、幂等、并发和失败注入专项测试 |
| `backend/tests/integration/test_compute_postgresql.py` | run_count 真实并发与直接 SQL 回归 |
| `backend/tests/integration/test_zz_catalog_migration_cycle_postgresql.py` | 0015 → 0014 → 0015 及完整迁移循环 |

## 8. 验证结果

### PostgreSQL 16

- 空临时数据库从零升级到 0015：通过；
- 0015 → 0014 → 0015：通过；
- 全迁移降级/升级循环：通过；
- Alembic head：`20260722_0015`；
- 实表：36；
- 当前测试库 124 个已产生审计事件的 Space 执行链验证：0 个无效链；
- run_count 双事务并发：一个成功、一个拒绝；
- 同一 ComputeJob 命令双事务并发：返回同一个 Job；
- 五条命令事件及 Outbox 原子创建：通过；
- 逐命令 AuditEvent 失败回滚：通过；
- Outbox payload digest 失败回滚：通过。

### 回归

- 全后端测试收集：109 项；
- 正常全量回归：106 通过，3 项按环境开关跳过；
- 额外显式执行 run_count 并发专项：通过；
- 额外显式执行破坏性迁移循环（专用临时数据库）：通过；
- Python `compileall`：通过。

临时数据库 `medtrust_phase49_test` 仅用于迁移验证，测试后已删除；其中不含业务或患者数据。

## 9. 本阶段明确未实现

- Outbox Dispatcher；
- Kafka、RabbitMQ 或其他外部消息系统；
- Compute Worker 或执行器；
- Connector 外部调用；
- Run started/completed/failed/interrupted 回调；
- Artifact 真实发布或下载；
- Compute API；
- 前端修改；
- 病理数据或病理模型运行。

## 10. 下一阶段前置事项

下一阶段应实现 Outbox Dispatcher 的领取、租约、重试和幂等投递；随后冻结执行器确认协议，再接入内置模拟执行器。只有执行器明确确认实际启动后，才能在新事务中产生 `compute.run.started`。只有发布执行器确认对象已安全开放后，才能产生 `artifact.released`。
