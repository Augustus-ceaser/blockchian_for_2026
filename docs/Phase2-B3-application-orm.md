# MedTrust Space Phase 2-B.3-B1 Application ORM + Migration

文档版本：v1.0  
日期：2026-07-22  
状态：已完成并通过 PostgreSQL 16.14 实库验证  
数据库基线：`docs/Phase2-database-design-v3.md`

## 1. 实现结论

本阶段实现 Application 聚合的三张核心表：

1. `applications`
2. `application_items`
3. `application_snapshots`

同时为既有 `data_products` 增加 `(space_id, provider_organization_id, id)` 复合候选键，使 ApplicationItem 能在数据库层同时证明“同空间、同提供方、同产品”。

本轮没有实现 `application_requested_actions`、`application_requested_output_types`、`application_attachments`。这三张表虽然存在于 v3 冻结设计中，但按本轮批准范围留给后续增量。因此，当前提交快照中的 `requested_actions`、`requested_output_types` 和 `attachments` 为空数组，不代表相关材料已经完整落库。

本轮也没有实现 Review、Contract、Compute、API、通用 CRUD、JWT 授权或真实医疗数据访问。

## 2. 代码位置

| 文件 | 职责 |
|---|---|
| `backend/app/modules/applications/models.py` | 三表 SQLAlchemy 2.0 typed ORM、关系、CHECK、UNIQUE、复合 FK 与索引。 |
| `backend/app/modules/applications/services.py` | 提交申请、规范化快照、SHA-256 摘要和 ORM Unit of Work 不变量保护。 |
| `backend/app/modules/applications/__init__.py` | Application 公共模型与命令导出。 |
| `backend/app/modules/catalog/models.py` | 为 DataProduct 补充空间/提供方/产品复合候选键。 |
| `backend/alembic/env.py` | 将 Application metadata 纳入 Alembic。 |
| `backend/alembic/versions/20260722_0005_applications.py` | 增量创建三表、数据库 CHECK、复合 FK、触发器和对称降级。 |
| `backend/tests/test_application_models.py` | SQLite 快速领域、关系、约束和快照测试。 |
| `backend/tests/integration/test_applications_postgresql.py` | PostgreSQL 16 复合约束、触发器与直 SQL 防绕过测试。 |

当前 Alembic head：`20260722_0005`。  
当前 ORM metadata：17 张表，其中 Application 精确为 3 张表。

## 3. 聚合关系

```text
Space + Applicant Organization + Provider Organization
                         ↓
                    Application
                         ↓ 1..n
                  ApplicationItem
                         ↓
        DataProduct + DataProductVersion

                    Application
                         ↓ 0..1
                ApplicationSnapshot
```

一份 Application 可以包含同一 Space、同一 Provider 下的多个 DataProductVersion。WSI、临床变量、随访等同一版本内部资源仍属于 DataResource，不会被错误拆成多个 ApplicationItem。

## 4. 三表职责

### 4.1 applications

保存一次使用申请的稳定身份与申请上下文：

- Space、申请组织、申请用户和提供组织；
- 申请编号、用途、法律或伦理依据；
- 算法名称、版本和摘要；
- 请求时长和运行次数；
- 状态、提交/决定/撤回时间；
- 演示标识、创建者、更新时间和乐观版本号。

状态集合：

```text
draft
submitted
prechecking
provider_review
approved
rejected
withdrawn
```

本阶段只公开 `submit_application()`，不会提前提供任意状态 PATCH 或审核命令。后续状态由 Review 域编排后再开放。

### 4.2 application_items

每条 Item 固定引用一个具体 DataProductVersion，并保存申请当时看到的：

- 产品版本摘要；
- 默认策略摘要；
- 请求范围 JSON 文档；
- 展示顺序。

三组复合 FK 分别保证：

1. Item 与 Application 同 Space、同 Provider；
2. Item 引用的 Product 属于该 Space 和 Provider；
3. Item 引用的 Version 确实属于该 Product。

同一 Application 内，同一 DataProductVersion 只能出现一次；同一申请内位置号也不能重复。

### 4.3 application_snapshots

提交时生成一对一、不可覆盖的完整申请快照：

- `schema_version`；
- 规范化 manifest；
- `sha256` 快照摘要；
- 捕获时间与捕获用户。

快照固定申请主体、用途、算法、使用时长、运行上限、Item 顺序、版本摘要、策略摘要和请求范围。摘要由确定性 JSON 序列化后计算，不随 ORM 更新自动漂移。

## 5. 重复申请规则

本阶段没有设置跨 Application 的 `(applicant_organization_id, data_product_version_id)` 唯一约束。

- 同一 Application 内重复申请同一 Version：数据库拒绝；
- 同一组织在不同 Application 中申请同一 Version：允许。

后一种情况在用途、算法、期限或研究方案不同的业务中是合理的。未来可用幂等键、进行中申请风险提示或策略服务控制重复提交，但不能用全局唯一约束误伤合法申请。

## 6. 提交与不可变保护

### 6.1 受控提交命令

`submit_application()` 在同一事务内验证：

1. Application 当前为 draft；
2. 至少存在一个 Item；
3. 每个 Item 引用的 Version 存在且属于对应 Product；
4. Version 已 approved；
5. Version 摘要和默认策略摘要与申请记录一致；
6. Version 存在 active Publication。

验证成功后生成 ApplicationSnapshot，并把 Application 转为 submitted、写入 `submitted_at`。`APPROVED` 仍不授予数据访问权；后续必须进入 Contract 和受控使用链路。

### 6.2 ORM 防线

SQLAlchemy `before_flush` 防止：

- 新申请跳过 draft；
- submitted 后修改申请核心内容；
- 非 draft 状态增删改 Item；
- 修改或删除 Snapshot；
- 删除非 draft Application；
- 将 `requested_scope` 或 `manifest` 写成非对象值。

### 6.3 PostgreSQL 防线

Migration 增加四项数据库保护：

1. `trg_application_lifecycle`：限制状态跳转和提交后内容修改；
2. `trg_application_item_draft`：只允许 draft 阶段增删改 Item；
3. `trg_application_snapshot_immutable`：禁止更新或删除 Snapshot；
4. `trg_application_requires_snapshot`：事务提交时保证非 draft Application 已存在 Snapshot。

JSONB 对象形状由 PostgreSQL migration CHECK 保证；ORM 快速测试使用通用 JSON 类型，并由领域服务检查 Python 对象形状。这样既保留 PostgreSQL 强约束，也不把 `jsonb_typeof` 方言函数泄漏到 SQLite metadata。

## 7. 删除策略

- ORM relationship 未配置 `cascade="all, delete"`；
- draft Application 可按受控流程清理，Item 的数据库 CASCADE 只服务于该草稿清理；
- submitted 及后续申请不能物理删除；
- Snapshot 使用 RESTRICT 且有数据库触发器保护；
- Product、Version、Organization、User 和 Space 引用均使用 RESTRICT。

## 8. 测试覆盖

Application 快速测试覆盖：

1. 创建申请；
2. 一份申请包含多个产品版本；
3. 同申请重复 Version 被拒绝；
4. 不同申请允许引用同一 Version；
5. 跨 Space Item 被拒绝；
6. Provider 不一致被拒绝；
7. 提交生成确定性 Snapshot，后续修改被拒绝；
8. draft Application 提前创建 Snapshot 被拒绝；
9. 非法初始状态被拒绝。

PostgreSQL 16 集成测试覆盖：

- 三表、索引、复合 FK、四个触发器与函数实际存在；
- 同空间/同提供方复合约束真实生效；
- draft Application 通过直 SQL 提前插入 Snapshot 被数据库拒绝；
- 直接 SQL 更新或删除 Snapshot 被数据库拒绝；
- 0005 migration 的 upgrade/downgrade 与历史 Catalog 并发规则共同回归。

最终完整验证：

```text
43 passed
0 failed
0 skipped
```

验证环境：PostgreSQL 16.14，专用可丢弃数据库 `medtrust_application_verify`。验证包含真实 `0005 → 0003 → 0005` migration cycle，最终数据库恢复到 `20260722_0005 (head)`。

其他检查：

```text
compileall：通过
pip check：通过
pytest -W error：通过
Alembic 0004 ↔ 0005 离线 SQL：通过
metadata 表数：17
最长约束/索引标识符：59 bytes
```

## 9. 明确边界

本阶段证明的是 Application 数据模型、提交快照和数据库不变量可以真实运行，不等于已经实现完整的数据使用审批或可信授权。

当前仍没有：

- ReviewTask / ReviewDecision；
- 数字合约与执行策略；
- 数据访问授权；
- 受控计算或用户代码执行；
- 真实医院、患者、WSI 或 PACS 数据；
- 电子签名、审计存证或合规认证；
- Application HTTP API 和前端 API 接入。

## 10. 下一阶段建议

先按 v3 冻结设计补齐 `application_requested_actions`、`application_requested_output_types` 和 `application_attachments`，扩展提交快照摘要，再进入 ReviewTask / ReviewDecision。不要直接跳到 Contract；否则数字合约将引用一个材料和审核证据尚未完整落库的申请。
