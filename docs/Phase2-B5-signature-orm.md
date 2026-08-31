# Phase 2-B.5-D2 Signature + Active Guard ORM 与 Migration

> 完成日期：2026-07-22  
> Alembic head：`20260722_0010`  
> 实库基线：PostgreSQL 16，30 张表

## 1. 本批结论

本批新增演示签署证据 `contract_signatures`，并开放两条彼此独立的 ContractRevision 生命周期转换：

```text
proposed --必需签署方确认同一content_digest--> signed
signed   --重新验证当前治理与执行条件--------> active
```

`signed` 只证明必需合同方确认了同一份不可变 Revision 内容；它不产生数据访问权，也不会自动进入 `active`。`active` 必须通过独立命令重新验证 Review 准入、合同策略、Binding 回执、Connector 当前状态和精确能力版本。

## 2. 文件清单

| 类型 | 文件 |
| --- | --- |
| ORM | `backend/app/modules/contracts/models.py` |
| 签署、生效与 Review 准入服务 | `backend/app/modules/contracts/services.py` |
| 模块导出 | `backend/app/modules/contracts/__init__.py` |
| Migration | `backend/alembic/versions/20260722_0010_contract_signatures_guards.py` |
| SQLite 领域测试 | `backend/tests/test_contract_models.py` |
| PostgreSQL 专项测试 | `backend/tests/integration/test_contract_signature_postgresql.py` |
| Contract PostgreSQL 回归 | `backend/tests/integration/test_contracts_postgresql.py` |
| 迁移循环 | `backend/tests/integration/test_zz_catalog_migration_cycle_postgresql.py` |

## 3. ContractSignature

新增表 `contract_signatures`，每条记录固定以下事实：

- Revision、ContractParty 和签署组织；
- 实际签署用户及其组织成员关系；
- 被签署的 `signed_content_digest`；
- 演示授权快照 `authority_snapshot`；
- 签署证据引用、签署时间和验证时间；
- canonical JSON 生成的 `signature_digest`。

V1 只接受：

```text
signature_type      = demo
verification_status = verified
```

该表不保存私钥，不验证 CA，不声称具备法律意义上的电子签名能力。签名通过复合外键同时约束 Revision、Party、组织、成员和 Revision 内容摘要；同一 Party 不能对同一内容重复签署。

## 4. proposed -> signed

`sign_contract_revision` 在同一事务中完成：

1. 确认 Revision 处于 `proposed` 且已有内容摘要；
2. 确认签署组织就是指定 ContractParty；
3. 确认组织、用户和组织成员关系当前有效；
4. 确认成员持有组织上下文角色 `contract_signer`；
5. 生成演示授权快照和签名摘要；
6. 追加签名，不修改或覆盖既有签名；
7. 只有所有 `is_required=true` Party 均签署同一 `content_digest` 后，原子地把 Revision 推进到 `signed`。

PostgreSQL 使用普通触发器和延迟一致性触发器兜底，拒绝签名 UPDATE/DELETE，也拒绝“签名齐全但 Revision 未转 signed”或“Revision 已 signed 但签名不完整”的半成品事务。

## 5. signed -> active

`activate_contract_revision` 不复用签署结果作为授权结论，而是重新检查：

- Revision 当前为 `signed`，且处于有效时间窗口；
- Application 仍为 `approved`，ApplicationSnapshot 与 Contract 固定摘要一致；
- Review plan 与所有 required ReviewDecision 可重新计算且仍与 Eligibility Evidence 完全一致；
- Space、合同组织、SpaceParticipant 与空间角色当前有效；
- ContractObject 固定的 DataProductVersion 仍为 `approved`，DataProduct 仍为 `active`；
- 至少存在 Policy，且每条 Policy digest 可重算验证；
- required Binding 均为 `accepted`；
- Connector 当前为 `verified + online` 且有心跳；
- ConnectorCapability 仍为 `verified`，并满足固定的 `1.0` 能力参数；
- 同一 Contract 不存在另一条 `active` 或 `suspended` Revision。

任何一项失败都保持 `signed`，不会产生部分生效状态。

## 6. “缺少 Policy”如何处理

本实现采用更强的不变量，而不是制造一个非法的“已签署但无 Policy”状态：

```text
缺少Policy -> draft不能proposed -> 不能签署 -> 不能active
```

Revision 进入 `proposed` 后，既有 D1 触发器又禁止删除或修改 Policy。因此测试覆盖的是“缺少 Policy 无法进入可签署链路”，而不是通过绕过前置约束伪造 signed Revision 再测试 active。

## 7. 数据库保护

`20260722_0010` 包含：

- `guard_contract_signature_append_only_v6`：签名只追加、校验签署权限快照；
- 对既有 `guard_contract_revision_core` 的增量状态守卫：只开放 `proposed -> signed -> active`；
- `guard_contract_revision_activation_v6`：数据库层复核策略、Binding、节点、能力、参与方、产品和审核事实；
- `guard_contract_revision_signed_consistency_v6`：事务末校验签名集合与 Revision 状态一致。

Migration 已真实完成 `0010 -> 0009 -> 0010`，降级会恢复 0009 对 signed/active 的阻断，升级后重新安装全部 D2 守卫。

## 8. 验证结果

已完成：

- Python compile 与 SQLAlchemy metadata：30 张表；
- Contract SQLite 专项：12 passed；
- Signature/Activation PostgreSQL 专项及 Contract schema 回归：3 passed；
- PostgreSQL 真实 `0010 -> 0009 -> 0010` 迁移循环：passed；
- 全后端回归：68 passed，2 skipped；
- Alembic 最终 head：`20260722_0010`。

覆盖签署闭合但不自动生效、缺少必需签署方、缺少 Policy 的前置阻断、Binding 未接受、能力状态不足、直接 SQL 篡改签名、active 后 Revision 内容修改和完整升降级。

测试产生一个非业务性警告：当前 Windows 目录权限阻止 pytest 写入 `.pytest_cache`；不影响测试执行和数据库结果。

## 9. 明确未实现

本批没有实现：

- CA、真实电子签名或法律效力验证；
- Contract、Signature 或 Activation API；
- ComputeJob、Artifact、结果出域审核；
- AuditEvent 或哈希链存证；
- 真实 Connector 下发、执行令牌、数据下载；
- 用户代码、AI 模型、隐私计算或患者数据处理。

因此，`active` 当前表示“业务模型和数据库守卫确认合同具备执行资格”，不是已经执行计算，也不是医院数据已经交付。

## 10. 下一步

下一阶段应先设计 Compute 领域：ComputeJob 必须引用 active ContractRevision 下已经固化的 ContractObject、Policy 和 Binding，不得自行扩大动作、数据范围或输出类型。仍应先做领域模型冻结，再决定 ORM 与执行模拟边界。
