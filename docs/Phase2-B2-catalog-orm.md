# MedTrust Space Phase 2-B.2.3 Catalog ORM + Migration

文档版本：v1.0  
日期：2026-07-22  
状态：已完成并通过 PostgreSQL 16.14 实库验证  
数据库基线：`docs/Phase2-database-design-v2.md` v0.2.1

## 1. 实现范围

本阶段严格实现五张 Catalog 表：

1. `data_products`
2. `data_product_versions`
3. `data_resources`
4. `product_sources`（领域类名 `DataProductSource`）
5. `data_product_publications`

没有实现 Application、Contract、Compute、Audit、API、通用 CRUD、真实资源访问或患者数据存储。

## 2. 代码位置

| 文件 | 职责 |
|---|---|
| `backend/app/modules/catalog/models.py` | 五表 SQLAlchemy 2.0 typed ORM、关系、CHECK、UNIQUE、复合 FK 和索引。 |
| `backend/app/modules/catalog/services.py` | 最小状态命令、来源 Connector 校验、提交审查校验和 ORM flush 不可变防线。 |
| `backend/app/modules/catalog/__init__.py` | Catalog 公共对象与命令导出。 |
| `backend/alembic/versions/20260722_0004_catalog.py` | 从 Connectors revision 增量创建五表、四个防绕过触发器及对称降级。 |
| `backend/tests/test_catalog_models.py` | SQLite 快速关系、约束、状态与服务测试。 |
| `backend/tests/integration/test_catalog_postgresql.py` | 专用已迁移 PostgreSQL 16 数据库集成测试入口。 |

Alembic `env.py` 已导入 Catalog metadata。当前 head：`20260722_0004`。

## 3. 表关系

```text
Space + Provider Organization
          ↓
     DataProduct
          ↓  (space_id, data_product_id)
  DataProductVersion
          ↓  (space_id, version_id)
      DataResource
          ↓
 DataProductSource → Connector

DataProduct + approved DataProductVersion
          ↓
DataProductPublication
```

父对象关系只建立一条复合 FK；不会在同一父 ID 上同时叠加单列 FK。直接 `space_id → spaces.id` 继续保留为租户引用。

## 4. 已实现数据库约束

### 4.1 产品与版本

- `(space_id, product_code)` 唯一。
- `(data_product_id, version_no)` 唯一。
- `(data_product_id, version_label)` 唯一。
- `(data_product_id, snapshot_digest)` 唯一；draft 的 NULL 摘要不冲突。
- `(space_id, data_product_id)` 复合 FK 保证 Version 与 Product 同空间。
- Version 非 draft 时必须存在 `snapshot_digest`。
- `approved_at` 和 `approved_by` 必须同时为空或同时存在。

### 4.2 资源与来源

- `(data_product_version_id, resource_code)` 唯一。
- `(data_product_version_id, position_no)` 唯一。
- `(space_id, data_product_version_id)` 复合 FK 保证 Resource 与 Version 同空间。
- ProductSource 使用 `(data_resource_id, connector_id, local_resource_alias)` 复合主键。
- Source 必须引用真实 Connector；领域服务进一步检查同空间、所有组织、核验状态、运行状态和 `product_publish` 能力。

V1 尚无联合提供授权对象，因此 Source Connector 必须属于 Product provider。多中心联合产品将在联合授权证据模型完成后再开放，不能先用布尔字段绕过。

### 4.3 目录发布

- Publication 通过两组复合 FK 同时保证 Product、Version 和 Space 一致。
- 每个 Product 最多一个 active Publication。
- 每个 Version 最多一个 active Publication。
- status 和 visibility 使用 CHECK。
- withdrawn 状态必须保存操作者和时间；非 withdrawn 状态不能混入撤回字段。

## 5. 不可变与状态保护

### 5.1 受控命令

Catalog 服务只提供以下状态动作：

- `submit_version_for_review`
- `return_version_to_draft`
- `approve_version`
- `retire_version`
- `publish_version`
- `withdraw_publication`
- `add_product_source`

没有开放任意 `PATCH status`。

提交审查前，服务验证：

- Version 和默认策略摘要存在；
- Version 与 Resource JSON 文档包含 `schema_version`；
- 至少一个 DataResource；
- 每个 Resource 有摘要；
- 每个 Resource 至少一个 ProductSource。

摘要由上游规范化过程显式提供，本阶段不会在 ORM 更新时自动改变摘要。

### 5.2 ORM flush 防线

SQLAlchemy `before_flush` 防止：

- Version 跳过合法状态转换；
- under_review 原地修改内容；
- approved/retired 内容更新；
- 非 draft Version 增删改 Resource 或 Source；
- 删除非 draft Version；
- 删除 Publication 历史；
- 修改 Publication 的标的、可见性或发布身份。

该防线覆盖正常 ORM Unit of Work，但不能拦截所有 bulk SQL。

### 5.3 PostgreSQL 触发器

Migration 增加四个触发器作为直接 SQL 的纵深防御：

1. `trg_product_version_immutable`
2. `trg_catalog_resource_draft`
3. `trg_catalog_source_draft`
4. `trg_catalog_publication`

函数和触发器分别执行，避免 asyncpg 对多语句 prepared statement 的限制。触发器保证数据库写入绕过 ORM 时，仍不能篡改受审/批准版本、非草稿资源和来源，或发布未批准版本。

## 6. 删除策略

- ORM relationship 未配置 `cascade="all, delete"`。
- Product 有 Version 后由 FK `RESTRICT` 阻止删除。
- Version → Resource、Resource → Source 的数据库 CASCADE 仅服务于已获准的草稿清理。
- 非 draft Version 的删除由 ORM 防线和 PostgreSQL 触发器拒绝。
- Publication 历史永不通过业务会话删除，只能 active → withdrawn/expired。

## 7. JSONB 边界

JSONB 仅用于：

- Version 的范围、匿名关联、质量、默认策略和来源摘要；
- Resource 的 schema、范围和质量描述。

关系、状态、业务编码、版本、分类、模态、格式、摘要和发布时间均为关系字段。Catalog 不保存患者级行、患者映射、真实 WSI/PACS 路径、凭据或私钥。

## 8. 测试覆盖

快速测试覆盖：

1. Product、Version、Resource、Source 创建和发布/撤回链路；
2. 重复 `version_no` 拒绝；
3. 重复 `version_label` 拒绝；
4. Product 与 Version 跨 Space 复合 FK 拒绝；
5. under_review 内容修改拒绝；
6. draft Version 发布拒绝；
7. 跨 Space Source Connector 拒绝。
8. draft Version 通过数据库 FK 级联清理 Resource/Source；
9. approved Version 删除拒绝。

完整测试结果：

```text
22 passed, 4 skipped
```

四个 skipped 测试均需要 `MEDTRUST_TEST_DATABASE_URL` 指向已迁移的专用 PostgreSQL 测试库，其中新增一个 Catalog 集成测试会验证完整图写入和直接 SQL 篡改被触发器拒绝。

迁移静态验证：

```text
增量 upgrade：5 CREATE TABLE，4 CREATE FUNCTION，4 CREATE TRIGGER
增量 downgrade：5 DROP TABLE，4 DROP FUNCTION
Alembic head：20260722_0004
metadata 表数：14
最长约束/索引标识符：59 bytes（低于 PostgreSQL 63 bytes）
```

`compileall`、`pytest -W error` 和 `pip check` 均通过。

## 9. 环境限制

当前机器没有可用的 PostgreSQL/Docker 环境，因此尚未实际执行：

- `alembic upgrade head` 连接 PostgreSQL 16；
- JSONB、部分唯一索引和 PL/pgSQL 触发器实库运行；
- 迁移锁与真实并发发布冲突。

离线 SQL 生成、SQLite 快速测试和可选集成测试入口不能替代专用 PostgreSQL 16 验证。进入 Application 域前，建议先在可清理的测试库执行完整 upgrade → integration tests → downgrade → upgrade 回归。

## 10. 下一阶段边界

Catalog 实现完成后可以评审 Application 域。不要立即铺设所有 CRUD；Application 必须直接引用明确 `DataProductVersion`，保存 `requested_product_snapshot_digest`，并在提交时验证 active Publication。
