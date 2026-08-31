# Phase 2-B.8 Stage 3C：FakeExecutor Callback 闭环

## 结论

FakeExecutor 已在不运行模型、不读取数据文件的条件下完成：

```text
reserved → dispatched → running → succeeded
                                  └→ quarantined Artifact
```

同时验证了 `failed` 和 `interrupted` 分支不会生成成功 Artifact。数据库沿用既有 `succeeded` 状态；文档中的“completed”是外部回调事实，不新增 Run 状态。

## 实现位置

- 回调处理：`backend/app/execution/callback_processor.py`
- Worker：`backend/app/workers/execution_callback_worker.py`
- FakeExecutor 回调构造：`backend/app/execution/adapter.py`
- PostgreSQL 守卫：`backend/alembic/versions/20260722_0020_compute_callback_evidence.py`
- 端到端测试：`backend/tests/integration/test_execution_callback_processor_postgresql.py`

## 事务边界

领取事务只把 Inbox 从 `received` 推进为 `processing` 并提交。处理事务原子完成：

```text
ComputeRun / ComputeJob 状态
+ AuditEvent
+ OutboxMessage
+ Callback Inbox completed
+ 必要时创建 quarantined Artifact 和 artifact.created 证据
```

任一步失败，处理事务整体回滚。乱序回调回到 `received` 等待重试；外部执行 ID 冲突或终态冲突进入 `dead_letter`，不修改 Run。

## 数据库守卫

0020 未修改 0010—0019。它移除旧的 `AuditEvidenceUnavailable` 占位门禁，并增加延迟约束触发器，提交时验证：

- 回调 Inbox 已完成且 outcome 与状态转换一致；
- external execution ID 与 Run 回执一致；
- 对应 Run AuditEvent 存在；
- AuditEvent 有唯一 `audit.timeline` Outbox；
- completed 回调产生至少一个隔离 Artifact；
- 直接 SQL 无法绕过回调证据。

## 验证结果

- Fake 成功闭环：通过。
- started、completed 幂等：通过。
- failed、interrupted 分支：通过。
- 成功 Artifact 数量与隔离状态：通过。
- 直接 SQL 终态篡改：被拒绝。
- 全后端回归：133 passed，3 个显式环境门禁测试跳过。
- `run_count` 独立并发测试：通过。

