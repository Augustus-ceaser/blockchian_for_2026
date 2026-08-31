# Phase 2-B.5-B1 Contract Core ORM + Migration

> 完成日期：2026-07-22  
> Alembic head：`20260722_0008`  
> 实库基线：PostgreSQL 16，26 张表

## 1. 本批结论

本批实现 Contract 核心四表，并未实现完整数字合约执行能力：

1. `contracts`：稳定协议系列与来源准入证据；
2. `contract_revisions`：协议内容草稿及未来生命周期主体；
3. `contract_parties`：Revision 内组织参与方；
4. `contract_objects`：固定到具体 `DataProductVersion` 的协议标的。

`Contract` 没有 `status` 或 `current_revision_id`。生命周期权威字段只存在于 `ContractRevision.status`，避免双重真相源。

## 2. 文件清单

| 类型 | 文件 |
| --- | --- |
| ORM | `backend/app/modules/contracts/models.py` |
| 服务与 Session 守卫 | `backend/app/modules/contracts/services.py` |
| 模块导出 | `backend/app/modules/contracts/__init__.py` |
| Alembic 模型发现 | `backend/alembic/env.py` |
| Migration | `backend/alembic/versions/20260722_0008_contract_core.py` |
| SQLite 领域测试 | `backend/tests/test_contract_models.py` |
| PostgreSQL 集成测试 | `backend/tests/integration/test_contracts_postgresql.py` |
| 迁移循环测试 | `backend/tests/integration/test_zz_catalog_migration_cycle_postgresql.py` |

## 3. 数据库变化

### 3.1 新增四表

```text
Contract
  └─ ContractRevision
       ├─ ContractParty
       └─ ContractObject ──> DataProductVersion
```

### 3.2 上游兼容键

为 `contract_objects(data_product_version_id, product_snapshot_digest)` 的证据外键，新增：

```text
UNIQUE data_product_versions(id, snapshot_digest)
```

ApplicationSnapshot 继续复用既有候选键：

```text
(application_id, application_snapshot_id, application_snapshot_digest)
  -> application_snapshots(application_id, id, snapshot_digest)
```

### 3.3 关键唯一性

- 一个 Application 最多一个 Contract 系列；
- Contract 编号在 Space 内唯一；
- Revision 编号在 Contract 内唯一；
- 同一 Revision 不能重复绑定相同产品版本；
- 一个 Contract 同时最多一个开放候选 Revision；
- 一个 Contract 同时最多一个 active/suspended Revision。

## 4. 不变量保护

保护采用两层实现：

- SQLAlchemy `before_flush`：为领域测试和应用命令提供明确错误；
- PostgreSQL PL/pgSQL trigger：阻止直接 SQL 绕过。

数据库安装四个函数/触发器：

| 函数 | 保护内容 |
| --- | --- |
| `guard_contract_source` | Application 必须 approved；Contract 来源证据、编号不可篡改；禁止删除系列 |
| `guard_contract_revision_core` | 新 Revision 必须从 draft 开始；非 draft 不可修改；draft 可撤回 |
| `guard_contract_party_core` | Party 仅可在 draft 修改；provider/consumer 必须匹配来源申请 |
| `guard_contract_object_core` | Object 仅可在 draft 修改；版本与摘要必须来自申请；授权范围只能收窄 |

所有 JSONB 类型、digest 形状和复合外键由 PostgreSQL migration 强约束；跨方言 ORM 测试由 Session 守卫验证 JSON object 与 canonical SHA-256。

## 5. B1 状态边界

冻结词表保持 v5：

```text
draft, proposed, signed, active, suspended,
expired, terminated, superseded, withdrawn
```

但 B1 **不开放** `draft -> proposed`。原因是 v5 要求 proposal 前必须具备完整 Policy、Constraint 和 Connector Binding，而这些对象明确不在本批范围。当前实际能力为：

```text
draft -> draft edit
draft -> withdrawn
```

任何 proposal 尝试都会明确失败，而不是生成结构不完整、看似可签署的 Revision。后续 Policy/Binding 批次应在同一事务内校验完整性、生成 `content_digest`，再开放 proposal。

## 6. 验证结果

已完成：

- Python compile 与全模型导入；metadata 精确为 26 表；
- SQLite Contract 专项测试：5 passed；
- PostgreSQL Contract 专项测试：2 passed；
- PostgreSQL 真实 `0008 -> 0007 -> 0008` 升降级；
- 全后端回归：59 passed，2 skipped；
- 破坏性迁移循环：1 passed，最终恢复 `20260722_0008`。

覆盖场景包括：Contract/Revision 创建、Revision 编号唯一、版本摘要复合绑定、终态不可修改、Contract 来源证据不可修改、Party 组织匹配、授权范围子集、proposal 门禁、四个触发器与候选键存在性。

## 7. 明确未实现

本批没有实现：

- `Policy` / `PolicyConstraint`；
- `PolicyExecutionBinding`；
- `ContractSignature`；
- Contract API 或 CRUD；
- Contract 创建/提案完整事务服务；
- Compute、Artifact、Audit；
- CA 电子签名、真实 Connector 命令或数据访问授权。

因此，当前 Contract Core 仍是可验证的数据模型与草稿边界，不构成可执行授权。

## 8. 下一步

下一批应先冻结并实现 Contract Policy/Constraint，而不是直接进入 Compute。只有当最终策略能够证明“相对 Application/Review 只收窄”，并能与 Connector Binding 形成完整提案证据时，才应开放 `draft -> proposed`。
