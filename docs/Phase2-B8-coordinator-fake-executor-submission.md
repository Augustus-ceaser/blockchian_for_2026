# Phase 2-B.8-C Coordinator 与 FakeExecutor 提交闭环实施报告

日期：2026-07-22  
状态：已完成并通过 PostgreSQL 16 验证

## 1. 结论

本阶段实现了以下边界：

```text
compute.run.reserved
→ Consumer Inbox
→ Coordinator重新授权
→ FakeExecutor Accepted
→ ComputeRun dispatched
→ AuditEvent + OutboxMessage + Inbox completed 同事务提交
```

`accepted` 只代表执行器已接受任务，不代表真实运行已经开始。本阶段没有进入 `running`，没有运行模型，也没有创建 Artifact。

## 2. 实现文件

```text
backend/app/execution/request.py
backend/app/execution/receipt.py
backend/app/execution/errors.py
backend/app/execution/adapter.py
backend/app/execution/coordinator.py
backend/app/execution/__init__.py
backend/app/workers/execution_coordinator.py
backend/alembic/versions/20260722_0017_compute_dispatch_gate.py
backend/alembic/versions/20260722_0018_compute_dispatch_gate_followup.py
backend/tests/test_execution_coordinator.py
backend/tests/integration/test_execution_coordinator_postgresql.py
```

## 3. 执行请求与 FakeExecutor

`ExecutionRequest` 是不可变、可确定性摘要的值对象，只包含 Run、Job、合同对象、Policy/Constraint/Binding、算法与输入快照摘要、执行环境和资源限制。它明确禁止路径、数据库连接串、患者数据、凭据、密钥和访问令牌。

`FakeExecutorAdapter` 提供确定性测试实现：

- 相同 submission key 与 request digest 返回相同 external execution ID；
- 相同 key、不同 digest 抛出冲突；
- 支持 accepted、明确拒绝、超时/结果未知以及按幂等键恢复查询；
- 不执行任何代码、模型或数据处理。

## 4. Coordinator 事务边界

1. 短事务领取 Inbox 并提交租约。
2. 事务外重新读取权威状态、完整授权重验、构建请求并调用 Executor。
3. Executor Accepted 后，在一个数据库事务中完成：
   - `reserved → dispatched`；
   - 写入 external execution reference 与回执摘要；
   - 追加 `compute.run.dispatched` AuditEvent；
   - 创建所需 OutboxMessage；
   - Inbox 进入 `completed`。

写回失败时 Inbox 不会完成。重试优先按 submission key 查询既有执行器回执，避免重复提交。

## 5. 数据库守卫

0017 只开放具有完整证据的 `reserved → dispatched`，并在 COMMIT 时验证：

- Run 调度回执字段完整；
- 存在匹配的 `compute.run.dispatched` AuditEvent；
- 存在一条 `audit.timeline` Outbox；
- 对应 Coordinator Inbox 已完成且指向该 Run。

0018 修正延迟触发器在同一事务内继续推进到后续状态时读取当前行状态的语义；它仍要求完整 dispatch 证据，没有放宽 `running` 的 Audit fail-closed 门禁。0010 至 0016 均未被修改。

## 6. 验证结果

- Coordinator/FakeExecutor 快速测试：2 项通过。
- PostgreSQL 提交闭环专项：1 项通过。
- 合法 dispatch、重复投递恢复、摘要冲突和直接 SQL 守卫均通过。
- 空数据库完整升级至 0018：通过。
- 独立无数据测试库完整破坏性迁移循环：1 项通过。
- 全后端回归：124 项收集，121 项通过，3 项按显式环境条件跳过。
- 当前 PostgreSQL：Alembic head `20260722_0018`，37 张实表，0 条无效审计链。

## 7. 仍保持关闭的能力

- `dispatched → running` 仍被可靠 Audit/回调证据门禁阻断。
- started/completed/failed/interrupted 回调尚未接入。
- Artifact 不会由 FakeExecutor 自动生成或发布。
- 未读取或运行用户数据集、用户模型或真实病理数据。
