# Phase 2-B.6-B2 Artifact / ArtifactReview ORM 实现说明

> 状态：已实现并通过 PostgreSQL 16 真实验证。本批只落地隔离制品与一次终态审核证据；Audit/outbox、执行器、API、下载和真实结果出域仍未实现。

## 1. 本批结论

本批新增 `artifacts`、`artifact_reviews` 与迁移 `20260722_0013_artifacts_reviews`。当前 Alembic head 为 `20260722_0013`，`medtrust` schema 由 32 张实表增加到 34 张。

```text
succeeded ComputeRun
  -> Artifact(quarantined)
  -> ArtifactReview(pending -> claimed -> decided)
  -> release guard
  -> AuditEvidenceUnavailable（当前固定拒绝）
```

`Run succeeded`、`Review approved` 和 `Artifact released` 是三个不同事实。0013 没有把审核或发布状态写回 ComputeJob/ComputeRun，也没有开放任何下载或真实出域能力。

## 2. 实现文件

| 文件 | 作用 |
| --- | --- |
| `backend/app/modules/compute/models.py` | `Artifact`、`ArtifactReview` typed ORM、复合 FK、候选键、CHECK 与索引 |
| `backend/app/modules/compute/services.py` | 输出 Policy 评估、隔离制品登记、审核领取/决定、发布 fail-closed 与 Session 守卫 |
| `backend/app/modules/compute/__init__.py` | 两对象及最小领域命令导出 |
| `backend/alembic/versions/20260722_0013_artifacts_reviews.py` | 两表、PL/pgSQL 跨表守卫、终态保护与发布 Audit 门 |
| `backend/tests/test_artifact_models.py` | SQLite 快速领域不变量测试 |
| `backend/tests/integration/test_artifacts_postgresql.py` | PostgreSQL 直接 SQL、Policy deny、责任组织与发布门专项 |
| `backend/tests/integration/test_zz_catalog_migration_cycle_postgresql.py` | 0013→0012→0013 及完整迁移链循环 |

## 3. Artifact 边界

Artifact 只能登记在具体 `succeeded ComputeRun` 下，并通过复合关系保证 Run、Job 和 Space 一致。创建服务重新读取 Job 请求输出、ContractRevision、ContractObject、`export_artifact` permit/deny Policy 与 egress Binding/Connector/Capability，形成不可变 `output_policy_evaluation` 和摘要。

核心规则：

- 初始状态只能是 `quarantined`；
- 类型必须同时属于 Job 请求输出和 Contract permit 范围；
- deny 优先，命中 deny 的候选制品可留在隔离区，但不能被人工批准；
- 内容摘要、来源 Run/Job、类型、隔离引用、大小、分类、保留期限和生成时 Policy 证据不可原地修改；
- 同一 Run 的 `artifact_no` 唯一，同一 Run/类型/内容摘要不能重复登记；
- 只保存不透明 `storage_reference`，拒绝 URL、预签名查询参数、本地路径、令牌或密钥样式；
- 不保存患者数据路径、Connector 凭据、访问令牌、MinIO 密钥或可识别患者内容。

本批沿用 v7 数据库冻结字段名 `storage_reference` 和 `classification_level`，没有增加同义列。

## 4. ArtifactReview 边界

V1 每个 Artifact 最多一行 Review；该行从 `pending` 进入 `claimed`，最终进入 `decided/approved` 或 `decided/rejected`。责任组织必须同时是：

- ContractObject 对应 DataProduct 的 provider organization；
- ContractRevision 的 provider ContractParty；
- 当前 Space 中 admitted 且持有 provider 角色的参与组织。

领取用户必须是责任组织当前有效成员。Review 固定 Artifact 的 `content_digest`；终态决定必须保存责任组织、领取用户、决定时间、原因、证据包和决定摘要。`decided` 或 `cancelled` 后整行禁止 UPDATE/DELETE。

拒绝后的内容不能修改原 Artifact 再次送审；必须由同一或新的 succeeded Run 产生新的 Artifact ID 和内容摘要。

## 5. PostgreSQL 守卫

0013 创建：

| 数据库对象 | 职责 |
| --- | --- |
| `guard_artifact_v7()` / `trg_artifact_guard` | 来源 Run、Job/Space、输出范围、permit/deny 证据、默认隔离、opaque reference、不可变和状态顺序 |
| `guard_artifact_review_v7()` / `trg_artifact_review_guard` | 固定摘要、provider责任组织、领取成员、单终态、deny不可覆盖和终态不可改删 |
| `assert_artifact_release_ready_v7(uuid)` | 复核审核、Run、当前 Contract、Policy deny、egress Binding/Connector/Capability |
| `assert_artifact_release_audit_ready_v7()` | 在事务型 Audit/outbox 未实现前固定抛出 `AuditEvidenceUnavailable` |

这些守卫覆盖 ORM 服务和直接 SQL。没有为了复合 FK 反向修改 Contract、Connector 或 Compute 既有表，也没有修改历史 0010、0011、0012 migration。

## 6. 发布边界

`release_artifact` 在应用层先重查：

1. Artifact 仍为 quarantined；
2. 固定摘要的 Review 已终态 approved；
3. 来源 Run 仍为 succeeded；
4. 当前输出 Policy 仍 permit，且无任一 deny；
5. egress Binding 与创建证据一致，Connector/Capability 当前有效。

随后必须与可靠 Audit/outbox 在同一事务提交。该事实源尚不存在，因此应用服务和数据库触发器都抛出 `AuditEvidenceUnavailable`，Artifact 保持 quarantined。普通日志或模拟事件不能解除该门。

## 7. 测试策略

生产代码没有新增 Run 完成命令或执行器。为测试“succeeded Run 才能产生 Artifact”，专项测试仅在独立测试库中临时替换 Compute Audit 门，按既有 Run 状态机生成成功 Run，随即恢复固定拒绝函数；该路径不导出为生产服务。

Policy deny 专项需要构造 active Revision 在正常生命周期中不可追加的 deny 事实。测试仅以数据库 owner 在回滚事务内短暂关闭 Policy 结构触发器、写入 deny 测试种子并立即恢复，用于证明 ArtifactReview 触发器会拒绝人工 approved；生产 migration 和应用角色没有该旁路。

## 8. 验证结果

- Artifact/Review SQLite 快速测试：4/4 通过；
- Artifact/Review PostgreSQL 16 专项：3/3 通过；
- 0013 空库完整 `upgrade head`：通过；
- `0013 -> 0012 -> 0013` 真实循环：通过；
- 全迁移深层升降级循环：通过；
- Contract active COMMIT hotfix 回归：通过；
- Compute `run_count` 双事务并发回归：通过；
- 全后端回归：96/96 通过；
- 当前 Alembic head：`20260722_0013`；
- 当前实表：34。

测试输出仅有既有 `.pytest_cache` 目录写权限警告，不影响测试结论。

## 9. 明确未实现

- AuditEvent、事务型 outbox、哈希链或可靠审计回执；
- Run 真实启动、Connector 下发、Worker、执行器或模型运行；
- Artifact 文件上传、扫描、下载、授权 URL 或真实发布；
- Compute/Artifact API；
- 前端接入；
- 公开病理数据和既有病理模型。

下一阶段必须先完成 AuditEvent 与 transactional outbox。只有业务状态与审计事实能在同一 PostgreSQL 事务提交后，才能替换 Run 和 Artifact 当前的 fail-closed 门。
