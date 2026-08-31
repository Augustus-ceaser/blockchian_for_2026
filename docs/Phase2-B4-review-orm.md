# MedTrust Space Phase 2-B.4-B Review ORM + Migration 实现说明

> 日期：2026-07-22  
> 状态：已实现并通过 PostgreSQL 16 实库验证  
> Alembic head：`20260722_0007`

## 1. 实现范围

本阶段只实现 Review 域的两个持久化对象：

- `ReviewTask`：固定 ApplicationSnapshot 审核目标、责任组织、领取用户、顺序和路由摘要；
- `ReviewDecision`：保存每个 Task 唯一、只增不改不删的最终结论。

没有实现 Contract、Compute、Artifact、Audit、HTTP API、规则引擎或真实访问授权。`approved` 仅是审核结论，不产生数据使用权。

## 2. 代码位置

| 内容 | 路径 |
| --- | --- |
| typed ORM | `backend/app/modules/reviews/models.py` |
| 生命周期与摘要服务 | `backend/app/modules/reviews/services.py` |
| 模块导出 | `backend/app/modules/reviews/__init__.py` |
| Alembic revision | `backend/alembic/versions/20260722_0007_reviews.py` |
| SQLite 快速不变量测试 | `backend/tests/test_review_models.py` |
| PostgreSQL 集成测试 | `backend/tests/integration/test_reviews_postgresql.py` |

## 3. 数据库变化

实际 ORM 表从 20 张增至 22 张；37 张仍是 Phase 2 完整逻辑目标，不表示已经全部落库。

新增：

1. `review_tasks`
2. `review_decisions`

同时为 `applications` 增加候选键：

```text
UNIQUE (id, space_id)
```

该键用于 ReviewTask 的复合外键，确保 Task 与 Application 属于同一 Space。

## 4. 关键关系

```text
ApplicationSnapshot
        ↓ evidence composite FK
ReviewTask
        ↓ one-to-zero-or-one
ReviewDecision
```

数据库固定以下边界：

- `(application_id, application_snapshot_id, target_digest)` 必须对应同一快照证据；
- `(space_id, assignee_organization_id)` 必须对应空间参与组织；
- 领取用户必须属于责任组织；
- Decision 的用户、组织和目标摘要必须与 Task 当前分配完全一致；
- 同一 Snapshot 的同一 `review_type` 最多一个 Task；
- 每个 Task 最多一个 Decision。

## 5. 状态与不可变保护

ReviewTask：

```text
pending -> claimed -> decided
    └──────────────> cancelled
claimed -> pending
```

终态 `decided/cancelled` 不可原地重开。ReviewDecision 只允许 INSERT；ORM 事件和 PostgreSQL trigger 均拒绝 UPDATE/DELETE。

数据库使用延迟约束触发器保证事务提交时：

- `task_status = decided` 时必须恰有一条 Decision；
- 非 `decided` Task 不得存在 Decision。

这允许服务在同一事务中先插入 Decision、再关闭 Task，同时拒绝只改 Task 状态或只插 Decision 的半成品事务。

## 6. 摘要格式修正

冻结文档早期字段表将 Review digest 写为 `char(64)`，但现有 `ApplicationSnapshot` 权威实现使用：

```text
sha256:<64 lowercase hex>
```

因此落库前将 `target_digest`、`routing_rule_digest` 和 `decision_digest` 统一为 `text` 加正则 CHECK。该修正是类型兼容修正，不改变领域关系。

## 7. 数据库级保护

`0007` 新增三类函数和四个触发器：

- `guard_review_task_lifecycle`：校验创建路由、责任组织、任务迁移和结构字段不可变；
- `guard_review_decision`：校验领取人、责任组织、摘要、活跃成员资格和追加式写入；
- `require_review_decision_consistency`：事务末校验 Task/Decision 终态一致性。

V1 未引入 ReviewPolicy/ReviewRoute 表。合规与伦理责任组织仍由后续服务端治理配置解析，数据库只验证其是已准入空间参与方。

## 8. 验证结果

已完成：

- Python `compileall`；
- SQLAlchemy 全域 metadata：22 张表，Review 两表可解析；
- Review SQLite 快速测试：4 passed；
- Review PostgreSQL 专项测试：3 passed；
- 全后端回归（启用普通 PostgreSQL 集成测试）：52 passed，2 skipped；
- `alembic downgrade 20260722_0006` 后重新 `upgrade head`；
- `alembic current`：`20260722_0007 (head)`；
- Alembic offline SQL 生成成功。

两个 skip 是既有的显式破坏性 Catalog 并发竞态测试和全迁移循环测试，需要单独环境变量开启；本轮已经真实完成 `0007 -> 0006 -> 0007` 的专项升降级。

## 9. 当前边界与下一步

本阶段尚未实现：

- 审核计划自动生成与 sequence 屏障编排；
- reviewer 上下文角色和职责分离的完整授权服务；
- Application 审核汇总投影；
- Contract draft 创建；
- Review API。

下一阶段不应直接把 `approved` 当访问授权。应先设计 Contract 如何引用获批 Snapshot 和 Decision digest，并把申请范围收窄为可执行策略。
