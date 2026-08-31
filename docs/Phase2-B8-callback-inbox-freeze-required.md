# Phase 2-B.8-D1 Callback Inbox 数据库变更冻结建议

日期：2026-07-22  
状态：阻塞；未进入 ORM 或 migration 实现

## 1. 阻塞结论

现有 `consumer_inbox_entries` 不能安全持久化 Executor Callback，因此 Phase 2-B.8-D1 必须按总任务的停止条件暂停。继续实现回调会要求绕过 0016 触发器、伪造 Audit/Outbox 来源，或使用进程内幂等；三种做法都不满足可信执行链要求。

## 2. 最小结构复现

一个合法外部回调具有：

```text
callback_id
run_id
external_execution_id
callback_type
occurred_at
payload_snapshot / payload_digest
execution_evidence_digest
executor identity / authentication evidence
```

但当前 Inbox 强制要求：

```text
event_id NOT NULL → audit_events
source_message_id NOT NULL → outbox_messages
payload_digest = 来源Outbox摘要
来源事件必须是 compute.run.reserved
topic/destination必须是 medtrust.compute.dispatch.v1 / compute.dispatch
UNIQUE(consumer_name, event_id)
```

因此它只能表达“平台内部 reservation 事件被 Coordinator 接收”，不能表达“外部 Executor 发来的、尚未形成平台业务 AuditEvent 的 callback”。

## 3. 因果冲突

回调的正确事务顺序应为：

```text
持久化并幂等领取Callback
→ 验证外部执行身份和摘要
→ 推进Run/创建Artifact
→ 追加AuditEvent与Outbox
→ Callback Inbox completed
→ COMMIT
```

现表却要求 Inbox 在插入时已经引用一个既存的 AuditEvent/Outbox。若用回调处理结果事件作为来源，会形成“处理结果先于输入接收”的因果循环；若复用 reservation 事件，则无法证明 callback_id、callback digest、类型和执行器身份，也无法检测“同 callback_id、不同 digest”的冲突。

## 4. 必须冻结的数据库变更

建议在新阶段先完成数据库设计，不直接写 migration。候选方案应二选一：

1. **演进现有 Inbox 为明确的多来源模型**：增加 `source_kind`，对 Audit/Outbox 来源与 Executor Callback 来源使用互斥 CHECK；Callback 来源保存 allowlist 快照、callback ID、digest、类型、Run/external execution identity 与认证证据摘要。
2. **新增专用 execution_callback_inbox**：仅当复用现表会导致大量 nullable 字段、复杂互斥 FK 或破坏现有 reservation 消费不变量时采用。

无论选择哪种方案，都必须冻结：

- `UNIQUE(consumer_name, source_kind, source_id)` 或等效回调唯一键；
- 同 callback ID、不同 payload digest 的数据库级冲突；
- callback schema/version 词表；
- Executor 身份认证和 external execution ID 与 Run 的一致性；
- started/completed/failed/interrupted 的合法乱序与重复语义；
- 回调接收、业务变化、AuditEvent、Outbox、Inbox完成的同事务边界；
- callback 内容的隐私 allowlist 与摘要规则；
- 0018 之后的增量迁移和可逆降级策略。

建议迁移编号（仅建议，尚未创建）：`20260722_0019_callback_inbox_evolution`。

## 5. 当前保留状态

- Run 最高只到 `dispatched`。
- `running` 及终态回调继续 fail-closed。
- 没有生成回调 AuditEvent、Artifact 或外部发布。
- Phase 2-B.8-D2 未启动。
- 未扫描、读取、复制或运行任何用户数据集和模型。

`READY_FOR_MODEL_ONBOARDING = false`
