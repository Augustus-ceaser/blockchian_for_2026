# Phase 2-B.5-D1 Contract Policy / Constraint / Binding ORM + Migration

> 完成日期：2026-07-22  
> Alembic head：`20260722_0009`  
> 实库基线：PostgreSQL 16，29 张表

## 1. 本批结论

本批实现 ContractRevision 下的三类机器可解释规则对象：

1. `policies`：主体、固定数据对象、动作和效果；
2. `policy_constraints`：时间、次数、用途、输出和执行环境等类型化限制；
3. `policy_execution_bindings`：Policy 到具体 ConnectorCapability 精确版本的执行绑定。

0009 同时把 0008 的“无条件阻止 proposal”替换为完整性门禁。只有通过显式 proposal 服务并满足最小 deny/audit 策略、申请范围收窄、同空间 Connector、verified capability 和 required Binding 完整性时，Revision 才能从 `draft` 进入 `proposed`。

`proposed` 仍不代表签署、生效或数据访问授权。

## 2. 文件清单

| 类型 | 文件 |
| --- | --- |
| ORM | `backend/app/modules/contracts/models.py` |
| 领域守卫与 proposal 服务 | `backend/app/modules/contracts/services.py` |
| 模块导出 | `backend/app/modules/contracts/__init__.py` |
| Connector 能力状态形态 | `backend/app/modules/connectors/models.py` |
| Migration | `backend/alembic/versions/20260722_0009_contract_policy.py` |
| SQLite 领域测试 | `backend/tests/test_contract_models.py` |
| PostgreSQL 专项测试 | `backend/tests/integration/test_contract_policy_postgresql.py` |
| Contract PostgreSQL 回归 | `backend/tests/integration/test_contracts_postgresql.py` |
| 迁移循环 | `backend/tests/integration/test_zz_catalog_migration_cycle_postgresql.py` |

## 3. 数据库变化

实表数由 26 增至 29：

```text
ContractRevision
  └─ Policy
       ├─ PolicyConstraint
       └─ PolicyExecutionBinding
            └─ ConnectorCapability
                 PK(connector_id, capability_code, capability_version)
```

Binding 以复合外键固定精确能力版本：

```text
(connector_id, required_capability_code, required_capability_version)
  -> connector_capabilities(connector_id, capability_code, capability_version)
```

V1 只接受：

```text
compute_executor        -> controlled_compute_execution / 1.0
egress_controller       -> egress_policy_enforcement / 1.0
audit_evidence_emitter  -> audit_evidence_emit / 1.0
```

不是 `>=1.0`，也不解析语义版本范围。

## 4. Policy 与默认拒绝

Policy 没有独立 `status`、版本号或生效时间；其生命周期由 ContractRevision 承担。合法 type/effect 组合固定为：

```text
permission  -> permit
prohibition -> deny
obligation  -> require
```

每个 consumer Party 与 ContractObject 在 proposal 前至少必须具备：

- permit `execute_controlled_compute`；
- deny `export_raw_data`；
- deny `reidentify_subject`；
- deny `redistribute_data`；
- require `write_audit_log`。

未显式 permit 的行为默认拒绝。`export_artifact` 只有在申请包含候选输出且 Policy 带有获准 `output_type` Constraint 时才可加入。

## 5. Constraint 类型矩阵

PostgreSQL 安装 IMMUTABLE 函数 `validate_policy_constraint_v1`，校验 JSONB 顶层类型、词表、值、单位和 canonical 排序。V1 支持：

| 名称 | 操作符 | 值 |
| --- | --- | --- |
| purpose_code | in | 排序去重后的申请动作数组 |
| algorithm_digest | eq | SHA-256 摘要 |
| environment_mode | eq | controlled_compute |
| run_count | lte | 正整数/count |
| effective_until | before | RFC3339 UTC |
| output_type | in | 排序去重后的输出类型数组 |
| output_review_required | eq | true |
| retention_seconds | lte | 非负整数/seconds |
| region | in | 排序去重后的字符串数组 |
| network_zone | eq | 非空字符串 |
| audit_level | gte | full |

`after` 只保留在顶层操作符词表中，V1 没有合法组合。`data_scope`、数据版本、原始路径、患者筛选、SQL、脚本和自定义表达式均被拒绝；数据范围继续由 ContractObject 权威表达。

## 6. Binding 生命周期

结构规格只允许在 draft 修改：

```text
pending -> accepted | rejected
accepted -> revoked
```

required capability 必须存在；proposal 时还必须处于 `verified`、具有 `verified_at`，且参数声明能证明相应 fail-closed 执行能力。accepted/rejected/revoked 回执字段受状态形态约束，已形成的回执不能在同一状态原地改写。

Binding accepted 只表示 Connector 承接策略规格，不产生执行授权。真正 `signed -> active` 的重查和门禁属于 0010。

## 7. Proposal 服务

新增 `propose_contract_revision`，在同一事务中：

1. 读取并验证 provider、consumer、ContractObject；
2. 验证 purpose/output 不扩大 Application；
3. 验证最小 Policy 集合和 required Binding role；
4. 验证 Connector 同 Space、owner 为允许 Party、Connector 已核验；
5. 验证精确 ConnectorCapability `1.0` 及能力参数；
6. 生成每条 Policy digest；
7. 生成 handoff evidence、handoff digest 与 Revision content digest；
8. 把 Revision 更新为 `proposed` 并冻结结构。

普通 ORM 字段赋值不能绕过该服务；直接 SQL 由 PostgreSQL trigger 兜底。0009 仍明确拒绝 `signed` 和 `active` 转换。

## 8. 验证结果

已完成：

- Python compile、ORM 映射和 metadata：29 张表；
- Contract SQLite 专项：8 passed；
- Contract Policy PostgreSQL 专项与既有 Contract/Connector 回归：12 passed；
- PostgreSQL 真实 `0008 -> 0009`，表数 26 -> 29；
- PostgreSQL 真实 `0009 -> 0008 -> 0009` 升降级循环；
- 全后端回归：63 passed，2 skipped；
- Alembic 最终 head：`20260722_0009`。

覆盖：Policy/Constraint/Binding 创建、复合能力外键、非法能力拒绝、Constraint JSONB 校验、最小策略门禁、proposal 摘要、proposed 后结构不可变、直接 SQL 防绕过、migration downgrade 恢复 0008 proposal blocker。

## 9. 明确未实现

本批没有实现：

- `contract_signatures`；
- proposed -> signed 命令；
- signed -> active 命令；
- CA 或真实电子签名；
- Contract、Policy 或 Binding API；
- Compute、Artifact、Audit；
- 真实 Connector 下发或用户代码执行；
- 数据下载、访问令牌或患者数据处理。

因此，当前能力是“可验证的合同策略提案”，不是生产级授权或可信计算平台。

## 10. 下一步

下一批应进入 0010：只实现 `ContractSignature`、签署证据和 signed/active 守卫。激活前必须再次检查 required Binding accepted、Connector/Capability 当前有效性和完整签署；仍不应提前进入 Compute。
