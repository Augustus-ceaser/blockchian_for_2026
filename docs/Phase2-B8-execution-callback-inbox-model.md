# Phase 2-B.8-D1 Execution Callback Inbox 数据库冻结

日期：2026-07-22  
状态：冻结通过后方可进入 ORM / migration

## 1. 结论与边界

新增独立聚合 `ExecutionCallbackInboxEntry`，物理表为：

```text
medtrust.execution_callback_inbox_entries
```

它只承载外部执行器回调的可靠接收、幂等、租约和处理结果。它不替代：

- `consumer_inbox_entries`：内部 AuditEvent/Outbox 消费事实；
- `audit_events`：不可变业务证据；
- `outbox_messages`：可靠投递机制；
- `compute_runs`：执行状态权威；
- `artifacts`：隔离输出制品；
- Executor 自身的执行状态。

现有 Consumer Inbox 保持不变。Callback 首次接收不要求预先存在处理结果对应的 AuditEvent/Outbox，从而消除因果循环。

## 2. 事务与确认边界

接收事务：

```text
校验Callback Envelope
→ 清理并canonicalize allowlist payload
→ 验证Run与Space
→ INSERT或幂等读取Callback Inbox
→ COMMIT
→ 才返回ACK
```

处理事务分为三段：

```text
事务A：claim + lease + COMMIT
事务外：读取权威状态并构造命令
事务B：Run/Artifact变化 + AuditEvent + Outbox + Callback Inbox completed + COMMIT
```

事务B任一写入失败全部回滚。Callback Entry保持可重试，不能伪造 `completed`。

## 3. 字段冻结

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| id | UUID PK | 平台内部身份 |
| space_id | UUID NOT NULL | Space边界，处理时与Run重验 |
| compute_run_id | UUID NOT NULL FK RESTRICT | 回调目标Run |
| executor_namespace | varchar(96) | 受信执行器命名空间 |
| external_execution_id | varchar(256) | Executor任务身份；首次接收允许Run尚未写回 |
| callback_id | varchar(160) | 命名空间内稳定回调身份 |
| callback_type | varchar(32) | 冻结词表 |
| callback_schema_version | integer | V1固定为1 |
| occurred_at | timestamptz | Executor声明发生时间 |
| payload_snapshot | jsonb | 清理后的allowlist快照，最大64KiB |
| payload_digest | varchar(71) | canonical JSON SHA-256 |
| normalized_fact_digest | varchar(71) | 不含callback_id的语义事实摘要 |
| execution_evidence_digest | varchar(71) | 执行证据摘要 |
| authentication_evidence_digest | varchar(71) | 认证验证结果摘要，不保存秘密 |
| correlation_id | UUID | 关联Run/提交链 |
| causation_id | UUID NULL | 上游callback/event关联 |
| status | varchar(16) | received/processing/completed/dead_letter |
| attempt_count | integer | claim/reclaim时递增，0..10 |
| available_at | timestamptz | 下次可领取时间 |
| locked_at / lock_owner / lease_expires_at | nullable | Worker租约 |
| outcome_code | varchar(64) NULL | 幂等处理结果词表 |
| outcome_reference_type/id | nullable | 轻量结果引用；不建多态FK |
| processing_error | varchar(1024) NULL | 脱敏错误摘要 |
| received_at / processing_started_at / completed_at / terminal_at | timestamptz | 生命周期证据 |
| created_at / updated_at | timestamptz | 持久化时间 |
| row_version | integer | 所有权与并发写回版本 |

禁止保存密码、Token、Connector凭据、MinIO密钥、数据库连接串、患者级数据、真实WSI路径、完整环境变量或未清理堆栈。

## 4. Callback Envelope与canonicalization

V1类型：

```text
execution.started
execution.completed
execution.failed
execution.interrupted
```

`executor.accepted`不属于Callback；它继续由Coordinator SubmissionReceipt驱动 `reserved → dispatched`。

canonical JSON规则复用 `medtrust-jsonb-c14n/v1`：UTF-8、键排序、紧凑分隔符、`ensure_ascii=false`、`allow_nan=false`、只允许JSON标量/数组/对象、禁止敏感键，最大64KiB。摘要格式固定为 `sha256:<64 lowercase hex>`。

`normalized_fact_digest`覆盖：namespace、Run、external execution ID、callback type、occurred_at、清理后的payload摘要和execution evidence摘要，但不包含callback_id，因此不同callback ID发送同一事实仍可被识别。

## 5. 唯一约束与幂等

```text
UNIQUE(executor_namespace, callback_id)
UNIQUE(executor_namespace, compute_run_id, callback_type, normalized_fact_digest)
```

- 同namespace/callback_id且payload digest一致：返回已有Entry并安全ACK。
- 同namespace/callback_id但payload digest不同：永久 `IdempotencyConflict`，不ACK成功。
- 不同namespace允许相同callback_id。
- 同一语义事实使用新callback_id：返回已有事实或明确重复结果，不重复推进Run。
- 同一Run/类型但事实摘要不同可以可靠接收；处理阶段依据Run状态拒绝冲突事实，不能覆盖权威终态。

## 6. 状态机与租约

```text
received → processing → completed
                     ↘ dead_letter
processing → received
processing(expired) → processing(new owner)
```

`completed`和`dead_letter`为终态，禁止UPDATE/DELETE。领取使用 `FOR UPDATE SKIP LOCKED`。claim/reclaim递增attempt，旧owner或过期owner不能settle。回调处理不能发生在claim事务内。

`completed`仅代表本条回调已被平台幂等处理，不代表合同、Job、Run或模型整体完成。

## 7. 关系与数据库守卫

- 普通FK：`compute_run_id → compute_runs.id ON DELETE RESTRICT`。
- 插入/更新触发器验证 `space_id` 与Run一致。
- 首次接收允许Run的 `execution_reference` 尚未写回；处理时必须严格匹配 `external_execution_id`。
- 不建立必填入站AuditEvent/Outbox FK。
- 核心来源字段、payload/evidence摘要、发生时间创建后不可修改。
- 终态不可修改/删除；row_version每次允许转换恰好+1。
- 数据库不把Callback Inbox状态解释为Run状态。

## 8. 乱序与故障窗口

- started早于Coordinator写回：可靠接收，处理时返回received并延后重试；不判定模型失败。
- completed早于started：可靠接收，保持可重试；达到10次后dead-letter并人工调查。
- Coordinator写回失败：Coordinator通过submission idempotency恢复；Callback不得创建第二个Executor任务。
- 同callback ID不同payload：永久冲突。
- failed/completed竞争：数据库Run状态机与审计事务只允许一个合法终态；另一Callback保留冲突证据并终止处理。

## 9. 索引

- PK(id)
- unique namespace/callback_id
- unique semantic fact
- received claim：`(available_at, received_at, id) WHERE status='received'`
- processing lease：`(lease_expires_at, id) WHERE status='processing'`
- Run timeline：`(compute_run_id, occurred_at, id)`
- Space receive：`(space_id, received_at DESC)`
- external execution lookup：`(executor_namespace, external_execution_id)`

## 10. 删除与保留

业务API不提供删除。数据库触发器禁止DELETE。Callback只包含最小allowlist证据；后续归档必须通过独立治理设计，不能修改或清空已提交记录。

## 11. 迁移规划

新增：

```text
20260722_0019_execution_callback_inbox
```

迁移只新增表、索引、CHECK与守卫函数/触发器，不修改0010—0018，不修改 `consumer_inbox_entries`。实表由37增至38。降级在存在Callback记录时应拒绝，空表时按trigger/function/index/table顺序删除。

## 12. ORM前冻结结论

Stage 3B必须保持：

1. 接收事务提交后才ACK；
2. payload canonicalization与敏感键拒绝；
3. 两层幂等；
4. claim与处理事务分离；
5. Run/Space数据库守卫；
6. completed/dead-letter不可变；
7. 不修改内部Consumer Inbox；
8. 不解锁Run running或Artifact发布。

该设计不读取或运行用户模型/数据，且不宣称生产级Executor身份认证、沙箱、隐私计算或第三方可信存证。
