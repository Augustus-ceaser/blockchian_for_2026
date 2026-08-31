# Phase 2-B.8-B2 Consumer Inbox ORM 实施报告

日期：2026-07-22  
状态：已完成并通过 PostgreSQL 16 验证

## 1. 结论

本阶段落地了第 37 张业务表 `medtrust.consumer_inbox_entries`。它只负责 Dispatcher 到 Execution Coordinator 的可靠接收、消费者幂等、处理租约和终态处理结果，不替代 AuditEvent、OutboxMessage 或 ComputeRun 事实。

Dispatcher 的确认边界为：

```text
验证 Envelope 与来源证据
→ Inbox INSERT 或幂等读取
→ COMMIT
→ 返回 ACK
```

Inbox 提交失败或摘要冲突时不会返回成功 ACK。

## 2. 实现文件

```text
backend/app/modules/inbox/models.py
backend/app/modules/inbox/services.py
backend/app/modules/inbox/__init__.py
backend/alembic/versions/20260722_0016_consumer_inbox.py
backend/tests/test_inbox_models.py
backend/tests/integration/test_inbox_postgresql.py
```

`backend/alembic/env.py` 已导入 Inbox metadata。0016 同时以增量方式把 `compute.run.dispatched` 加入审计事件词表，没有修改 0010 至 0015。

## 3. 数据库不变量

- `UNIQUE(consumer_name, event_id)`：同一消费者对同一事件最多一个接收事实。
- `event_id + space_id`、`source_message_id + space_id` 分别引用 AuditEvent 与 OutboxMessage 候选键。
- PostgreSQL 触发器核对 Outbox 所指 AuditEvent、Space、payload digest、topic、destination 与事件类型。
- 首次插入只能为 `received`；`processing` 必须持有租约。
- `received → processing → completed/dead_letter`，或 `processing → received` 重试。
- 终态记录禁止 UPDATE 和 DELETE；来源身份字段创建后不可修改。
- `row_version` 每次状态变化只能递增一次，旧 Worker 无法覆盖租约接管者。
- 不使用 ORM 删除级联。

## 4. 领取、重试与租约

领取使用 PostgreSQL `FOR UPDATE SKIP LOCKED`。Executor 调用不在领取事务中发生。实现了：

- `claim_inbox_batch`
- `reclaim_expired_inbox`
- `release_inbox_for_retry`
- `complete_inbox`
- `dead_letter_inbox`

最大尝试次数为 10；重试采用有上限的指数退避和稳定抖动。错误文本会清理凭据、令牌、签名参数与 URL 查询信息。

## 5. 验证结果

- Inbox 快速测试：2 项通过。
- Inbox PostgreSQL 专项：2 项通过。
- 0016 → 0015 → 0016 真实循环：通过。
- 空数据库完整升级：通过。
- Stage 2 完成后的全后端回归：124 项收集，121 项通过，3 项按显式环境条件跳过。
- 当前 PostgreSQL：Alembic head `20260722_0018`，37 张实表，0 条无效审计链。

当前 head 高于 0016 是因为下一阶段已新增 0017/0018 的 Coordinator dispatch 守卫；Inbox 表数仍为 37，没有创建通用 `idempotency_keys` 表。

## 6. 安全边界

0016 只接受来自不可变 AuditEvent/Outbox 的 `compute.run.reserved` 投递。它不保存患者数据、真实 WSI 路径、Connector 凭据、对象存储密钥或访问令牌。真实运行与 Artifact 外部发布均未由本阶段解锁。
