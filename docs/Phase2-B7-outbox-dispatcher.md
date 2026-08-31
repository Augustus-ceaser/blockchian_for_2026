# Phase 2-B.7-E Outbox Dispatcher 实现说明

日期：2026-07-22  
状态：已完成并通过真实 PostgreSQL 16 验证

## 1. 结论

本阶段实现了独立运行的 Transactional Outbox Dispatcher。它只负责可靠领取与投递消息，不执行 Compute、不发布 Artifact，也不判断任何业务状态。

投递边界严格拆成三段：

```text
事务 A：claim + processing lease + COMMIT
事务外：Publisher.publish + 明确 ACK/NACK
事务 B：mark published 或 mark failed + COMMIT
```

Publisher 外部调用不占用领取事务的行锁。只有明确 ACK 且携带投递时间时，消息才会进入 `published`。Worker 在 ACK 后、写回前崩溃时，消息会在租约过期后再次投递；这是预期的“至少一次投递”，不是 exactly-once。

## 2. 实现文件

```text
backend/app/messaging/
├── __init__.py
├── envelope.py
├── errors.py
└── publisher.py

backend/app/workers/
├── __init__.py
└── outbox_dispatcher.py

backend/tests/
├── test_outbox_dispatcher.py
└── integration/test_audit_outbox_postgresql.py
```

配置同步到：

```text
backend/app/core/config.py
backend/.env.example
```

本阶段没有新增表、字段、触发器或 migration；历史 `0010` 至 `0015` 均未修改。

## 3. Publisher 边界

稳定接口：

```python
class EventPublisher(Protocol):
    async def publish(self, message: OutboxEnvelope) -> PublishResult: ...
```

`PublishResult`明确携带：

- `acknowledged`；
- `external_message_id`（可选）；
- `delivered_at`；
- `retryable`；
- `error_code`。

实现包括：

- `FakePublisher`：可脚本化 ACK/NACK，只用于测试；
- `InMemoryPublisher`：仅用于本机开发，按 `(destination, event_id)` 演示消费幂等，不承诺进程外可靠性；
- `UnavailablePublisher`：明确 NACK，不会把消息伪装为 published。

CLI 默认配置为 `unavailable`。未显式配置 Publisher 时，Worker 会在领取任何消息前拒绝启动，避免因为配置缺失反复消耗重试次数或把消息推进 dead-letter。

## 4. Envelope

`OutboxEnvelope`由已提交的 `OutboxMessage + AuditEvent`构建，包含：

- message/event ID；
- event type 与 schema version；
- Space、topic、destination；
- subject、result；
- correlation/causation；
- occurred time；
- evidence；
- event/payload digest；
- idempotency key。

构建时会重新计算 `payload_digest`，并核对消息中的事件、Space、Subject、Correlation、Evidence 与权威 `AuditEvent`一致。Envelope不包含患者级数据、真实WSI路径、对象存储密钥、Connector凭据、访问令牌或数据库连接信息。

## 5. 租约、重试与并发

Dispatcher复用既有冻结服务：

- `reclaim_expired_outbox`；
- `claim_outbox_batch`；
- `mark_outbox_published`；
- `mark_outbox_failed`。

PostgreSQL领取继续使用 `SELECT ... FOR UPDATE SKIP LOCKED`。同一消息同一时刻只能被一个Worker持有；未过期租约不会被其他Worker领取；过期租约可被接管并增加 `attempt_count`。

写回结果时重新锁定消息并验证：

- 状态仍为 `processing`；
- `lock_owner`仍为当前Worker；
- 租约尚未过期。

旧Worker或超时Worker不能覆盖新Owner的处理结果。失败按既有指数退避语义重新进入 `pending`；第10次失败或不可重试错误进入 `dead_letter`。

## 6. 至少一次投递与消费者幂等

已验证以下崩溃窗口：

```text
Publisher已ACK
→ Dispatcher尚未mark published
→ Worker崩溃
→ lease过期
→ 新Worker再次投递
```

因此消费者必须以 `event_id`（在共享路由中结合 destination）实施幂等。测试中的 `InMemoryPublisher`收到两次投递，但同一destination下的业务处理只执行一次。

这不等于端到端 exactly-once，也不依赖普通日志推断投递成功。

## 7. 日志与统计

安全日志只包含：

- message_id；
- event_id；
- event_type；
- attempt_count；
- worker_id；
- result；
- error_code。

不会打印Envelope evidence或payload。错误文本继续通过既有Outbox清洗与长度限制。

`DispatcherStats`提供：

- `claimed_count`；
- `published_count`；
- `retry_count`；
- `dead_letter_count`；
- `lease_reclaimed_count`；
- `ownership_lost_count`。

本阶段没有引入Prometheus。

## 8. 优雅停止

Worker收到SIGINT/SIGTERM后：

1. 设置停止标志，不再领取新消息；
2. 等待当前批次在 `shutdown_timeout` 内完成；
3. 超时则取消本地投递任务；
4. 未确认结果保持 `processing`，由租约过期后的其他Worker接管；
5. 不把未知结果标记为 `published`。

## 9. 配置与启动

关键环境变量：

```text
MEDTRUST_OUTBOX_PUBLISHER=unavailable|in_memory
MEDTRUST_OUTBOX_BATCH_SIZE=50
MEDTRUST_OUTBOX_POLL_INTERVAL=1.0
MEDTRUST_OUTBOX_LEASE_SECONDS=60
MEDTRUST_OUTBOX_MAX_ATTEMPTS=10
MEDTRUST_OUTBOX_SHUTDOWN_TIMEOUT=30.0
```

本机开发演示：

```powershell
cd backend
$env:MEDTRUST_OUTBOX_PUBLISHER="in_memory"
.\.venv\Scripts\python.exe -m app.workers.outbox_dispatcher
```

`in_memory`只证明Dispatcher协议和状态流转，不代表消息已送达外部消费者。未配置时Worker拒绝启动且不领取消息。

## 10. 验证范围

快速测试覆盖：

- ACK后才published；
- NACK进入重试而非published；
- 第10次失败dead-letter；
- 过期租约接管；
- 旧Owner不能覆盖；
- ACK后崩溃导致重复投递；
- 幂等消费者只处理一次；
- stop后不再领取；
- 日志不泄露evidence。

PostgreSQL专项覆盖：

- 两个Dispatcher并发领取不同消息；
- processing未过期不可抢占；
- 过期租约由新Worker接管；
- 旧Worker写回被拒绝；
- published/dead-letter不再领取；
- Audit链仍有效。

最终验证结果：

```text
Dispatcher快速测试：7 passed
Audit/Outbox PostgreSQL专项：5 passed
全后端回归：114 passed, 3 skipped
Audit无效链：0
```

3项skip是项目原有的显式破坏性迁移/专项并发门控，不是本轮失败；Dispatcher多Worker竞争、租约接管与重复投递专项已真实执行。

数据库基线保持：

```text
Alembic head: 20260722_0015
实表: 36
```

## 11. 仍未解锁的能力

Dispatcher只投递消息，没有业务消费者。因此以下能力继续阻断：

- ComputeRun进入真实 `running`；
- `compute.run.started/completed/failed/interrupted`执行回执；
- Artifact真实 `released`；
- 对象存储外部可访问；
- Compute/Artifact API；
- 病理数据或模型运行。

下一阶段应先设计执行协调器消费者协议，再实现只运行平台预登记算法的内置受控执行器。
