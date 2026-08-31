# MedTrust Space Phase 2-B.7-C：AuditEvent + Transactional Outbox ORM

> 日期：2026-07-22  
> 状态：已实现，并通过 PostgreSQL 16、并发、迁移循环与全后端回归验证  
> Alembic head：`20260722_0014`  
> `medtrust` 实表：36 张

## 1. 本批结论

本批只落地审计与可靠投递基础设施：

```text
AuditEvent（不可变业务证据）
    ↓ 同一 PostgreSQL 事务
OutboxMessage（至少一次投递记录）
```

新增两张表：

```text
audit_events
outbox_messages
```

未把该能力接入现有 Contract、Compute 或 Artifact 命令，因此：

- `ComputeRun` 的真实预留/启动仍由原有 Audit 门拒绝；
- `Artifact release` 仍由原有 Audit 门拒绝；
- 本批不包含 Dispatcher、Broker、API、前端、执行器或病理模型运行。

0014 提供的是可验证的基础设施，不代表端到端执行闭环已解锁。

## 2. 实现文件

| 文件 | 作用 |
| --- | --- |
| `backend/app/modules/audit/models.py` | `AuditEvent`、`OutboxMessage` SQLAlchemy 2.0 typed ORM、约束与索引 |
| `backend/app/modules/audit/services.py` | canonical JSON、摘要、按 Space 链追加、幂等、原子 Outbox、领取/租约/成功/失败/回收服务 |
| `backend/app/modules/audit/__init__.py` | 审计领域公开入口 |
| `backend/alembic/env.py` | 将 Audit metadata 纳入 Alembic |
| `backend/alembic/versions/20260722_0014_audit_events_outbox.py` | 两表、数据库函数、触发器和约束 |
| `backend/tests/test_audit_models.py` | 快速领域不变量测试 |
| `backend/tests/integration/test_audit_outbox_postgresql.py` | PostgreSQL 链、并发、直接 SQL、Outbox 租约与状态机专项测试 |
| `backend/tests/integration/test_zz_catalog_migration_cycle_postgresql.py` | 增加 0014→0013→0014 真实迁移循环 |

## 3. AuditEvent

### 3.1 权威边界

`AuditEvent` 是只追加事实。它保存：

- Space 内序号和前序摘要；
- 冻结事件类型、Actor、Subject、结果；
- command、correlation、causation 与幂等上下文；
- 最小化 evidence 快照及摘要；
- 当前事件摘要。

它不保存 `updated_at`、软删除字段、患者数据、真实 WSI/PACS/LIS/EMR 路径、Connector 凭据、对象存储密钥或访问令牌。

ORM Session 守卫和 PostgreSQL 触发器均拒绝 `UPDATE` 与 `DELETE`。

### 3.2 按 Space 原子链

`append_audit_event_with_outbox(...)` 在调用方事务内执行：

```text
SELECT spaces ... FOR UPDATE
→ 读取该 Space 最后事件
→ 分配连续 stream_sequence
→ 固定 previous_event_digest
→ 计算 evidence_digest / event_digest
→ 插入 AuditEvent
→ 插入该事件必需的全部 OutboxMessage
```

首条事件为 `sequence=1` 且 `previous_event_digest=NULL`。同一 Space 的并发追加由 Space 行锁串行化；不同 Space 不争用全局链头。事务回滚时事件与消息一并消失，不遗留断号。

### 3.3 幂等语义

数据库使用两组复合唯一约束：

```text
(space_id, idempotency_key, event_type, subject_type, subject_id)
(space_id, command_id, event_type, subject_type, subject_id)
```

因此，同一命令可以产生不同事件类型；相同命令、事件类型和 Subject 不能形成第二份事实。完全一致的重试返回已有事件和消息，不一致重试抛出 `IdempotencyConflict`。

## 4. Canonical JSON 与摘要

应用与 PostgreSQL 都实现 `medtrust-jsonb-c14n/v1`：

- object key 按 UTF-8 字节顺序稳定排序；
- array 保持顺序；
- UTF-8、无多余空白；
- 只接受 string、boolean、integer、null、object、array；
- V1 拒绝浮点数、NaN 和 Infinity；
- 最大 canonical 内容为 64 KiB；
- 输出摘要为 `sha256:<64 lowercase hex>`。

跨实现测试覆盖空对象、逆序 key、中文、emoji、嵌套对象、数组、显式 null 及非整数拒绝，Python 与 PostgreSQL 的 canonical bytes 和 digest 一致。

## 5. PostgreSQL 函数与触发器

0014 新增主要函数：

```text
medtrust.canonicalize_jsonb_v1(jsonb)
medtrust.sha256_text_v1(text)
medtrust.sha256_canonical_jsonb_v1(jsonb)
medtrust.audit_event_manifest_v1(audit_events)
medtrust.verify_audit_space_chain_v1(uuid)
medtrust.outbox_payload_v1(audit_events, uuid)
medtrust.outbox_target_allowed_v8(text, text, text)
```

主要触发器函数与触发器：

```text
guard_audit_event_v8 / trg_guard_audit_event_v8
guard_outbox_message_v8 / trg_guard_outbox_message_v8
guard_audit_event_outbox_targets_v8 / trg_guard_audit_event_outbox_targets_v8
```

数据库层校验：

- Actor 当前有效性和固定 system service 词表；
- Event Catalog 的 event/subject/result 组合；
- Subject 存在且属于同一 Space；
- 链序号、previous digest、causation 顺序和摘要；
- 敏感 evidence key 拒绝；
- AuditEvent 必需 Outbox 目标集合；
- Outbox payload、digest、幂等键和目标一致性；
- Outbox 核心字段不可变、状态机和终态不可回退。

`verify_audit_space_chain_v1` 是只读巡检函数。数据库哈希链用于篡改检测线索，不等同于第三方可信存证或法律意义上的不可篡改。

## 6. Outbox 领取、租约与重试

Outbox 采用至少一次投递语义，不承诺 exactly-once。消费者必须按 event/message 幂等。

本批实现可测试的领域原语：

```text
claim_outbox_batch
reclaim_expired_outbox
mark_outbox_published
mark_outbox_failed
```

领取使用 PostgreSQL：

```sql
SELECT ... FOR UPDATE SKIP LOCKED
```

状态机为：

```text
pending → processing → published
                     ↘ pending（可重试失败）
                     ↘ dead_letter（不可重试或第10次失败）

processing（租约过期）→ processing（新 Worker 接管并增加 attempt）
```

核心 payload、目标、事件引用和幂等字段创建后不可修改；`published` 与 `dead_letter` 为 V1 终态。`last_error` 会截断并清洗 Authorization、token、secret、签名和带查询参数 URL。

本批没有常驻 Dispatcher，也没有 Kafka/RabbitMQ。

## 7. 验证结果

### 7.1 快速测试

```text
tests/test_audit_models.py：3/3 通过
非 integration：63/63 通过
```

覆盖 canonical JSON、同命令多事件、完全重放、幂等冲突、链关系、原子 Outbox、投递状态和 ORM 不可变守卫。

### 7.2 PostgreSQL 专项

```text
tests/integration/test_audit_outbox_postgresql.py：4/4 通过
```

已验证：

- 首事件和后续事件链；
- 同一 Space 两个并发事务获得连续且不同的序号；
- 不同 Space 可独立并发；
- Python/PostgreSQL canonical 摘要一致；
- AuditEvent UPDATE/DELETE 被直接 SQL 拒绝；
- Event 与 Outbox 事务回滚后一并消失；
- 双 Worker `SKIP LOCKED` 不重复领取；
- 未过期租约不能接管，过期租约可接管；
- 第 10 次失败进入 `dead_letter`；
- `published`、`dead_letter` 不可回退；
- 敏感错误内容清洗；
- Run Audit 门仍 fail-closed。

### 7.3 迁移与回归

```text
0014 → 0013 → 0014：通过
空数据库 0001 → 0014：通过
空库最终 head：20260722_0014
空库最终实表：36
全后端常规回归：100 passed，3项环境开关测试跳过
破坏性迁移循环：1/1 通过
Catalog + Compute 并发专项：9/9 通过
```

全后端共收集 103 项。三个环境开关用例在常规回归中跳过，已分别启用对应开关补跑相关迁移与并发专项。

## 8. 仍被明确阻断的能力

0014 后，下列能力仍未开放：

```text
ComputeRun 真实预留/启动
Artifact 真实 release
Outbox 后台投递
真实执行器
Compute/Artifact API
前端真实 API 接入
病理数据或模型运行
```

原因不是表结构缺失，而是现有关键业务命令尚未在同一事务中写入业务状态、AuditEvent 和全部必需 OutboxMessage。该接入应在后续独立批次完成，不能把当前基础服务的存在直接解释为门禁已经解除。

## 9. 后续顺序

```text
0015 关键命令事务接入与数据库硬门禁
→ Outbox Dispatcher
→ Run 可靠启动与内置模拟执行器
→ Artifact 可靠发布
→ API
→ 前端替换 Mock
→ 少量公开病理数据与固定模型
```

在 0015 完成前，不应接入公开病例或已有模型。
