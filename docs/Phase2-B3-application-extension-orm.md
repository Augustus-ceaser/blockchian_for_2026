# MedTrust Space Phase 2-B.3-B2 Application 扩展 ORM + Migration

> 日期：2026-07-22  
> 状态：已实现并通过 PostgreSQL 16.14 实库验证  
> Alembic head：`20260722_0006`

## 1. 实现结论

本阶段完成 Application 使用意图层的三张扩展表，并把它们接入现有提交快照：

- `application_requested_actions`
- `application_requested_output_types`
- `application_attachments`

当前 Application 聚合由六张表组成：

```text
applications
├── application_items
├── application_requested_actions
├── application_requested_output_types
├── application_attachments
└── application_snapshots
```

Application 获批仍不代表获得数据访问权。本阶段未实现 Review、Contract、Compute、API、对象上传或扫描任务。

## 2. 代码位置

| 内容 | 位置 |
|---|---|
| typed ORM | `backend/app/modules/applications/models.py` |
| 聚合不变量与提交快照 | `backend/app/modules/applications/services.py` |
| 模块导出 | `backend/app/modules/applications/__init__.py` |
| Alembic migration | `backend/alembic/versions/20260722_0006_application_extensions.py` |
| 快速测试 | `backend/tests/test_application_models.py` |
| PostgreSQL集成测试 | `backend/tests/integration/test_applications_postgresql.py` |
| 迁移循环 | `backend/tests/integration/test_zz_catalog_migration_cycle_postgresql.py` |

## 3. 三表实现

### 3.1 ApplicationRequestedAction

复合主键：

```text
(application_id, action_code)
```

受控词表：

- `ai_training`
- `model_validation`
- `research_analysis`
- `drug_development`

`parameters` 为 JSONB object，默认：

```json
{"schema_version":"1.0"}
```

应用服务拒绝非 object、缺少字符串 `schema_version`、NaN、Infinity 和其他不能形成标准 JSON 的值。PostgreSQL migration 另有 JSONB CHECK。

### 3.2 ApplicationRequestedOutputType

复合主键：

```text
(application_id, output_type)
```

受控词表：

- `aggregate_statistics`
- `model_artifact`
- `feature_dataset`
- `risk_scoring_model`

`requires_manual_review` 由提交服务重新派生，不信任调用方传值。V1 当前使用固定平台风险登记：

| 输出类型 | 人工审核基线 |
|---|---|
| aggregate_statistics | false |
| model_artifact | true |
| feature_dataset | true |
| risk_scoring_model | true |

该登记不是完整的 Space/Product 策略引擎。未来接入策略服务时仍应遵守“任一规则要求审核则最终为 true”。

### 3.3 ApplicationAttachment

PostgreSQL 只保存附件元数据：

- 逻辑对象引用 `storage_ref`
- 内容摘要 `content_digest`
- 文件显示名、大小和材料类型
- 扫描状态
- 创建人和创建时间

不保存附件二进制、临时URL、MinIO凭据或患者数据。

扫描状态只包含：

```text
pending → clean
        ↘ rejected
```

新记录必须从 `pending` 开始。draft 期间只允许修改 `scan_status`；内容变化必须替换整行。`clean` 和 `rejected` 均不可回退。

## 4. Snapshot 扩展

`submit_application` 现在按稳定顺序加载：

1. Items：`position_no`
2. Actions：`action_code`
3. Outputs：`output_type`
4. Attachments：`attachment_type`、`content_digest`

完整 manifest 包含：

```text
Application头
Items
RequestedActions
RequestedOutputTypes + requires_manual_review + review_rule_digest
Attachments稳定元数据
```

附件 `storage_ref` 不进入 Snapshot。

规范化规则：

- UTF-8
- 对象键排序
- 稳定数组顺序
- `ensure_ascii=false`
- 紧凑分隔符
- `allow_nan=false`
- SHA-256，格式为 `sha256:` 加64位小写十六进制

每个 RequestedOutputType 的 `review_rule_digest` 固定提交时使用的V1规则文档。Snapshot `schema_version` 本阶段保持 `1.0`。

## 5. 提交不变量

可提交 Application 必须满足：

- 至少一个 ApplicationItem；
- 至少一个 RequestedAction；
- 至少一个 RequestedOutputType；
- Item 引用 approved 且 active publication 的具体版本；
- 产品和策略摘要未发生变化；
- 所有已登记附件均为 `clean`；
- 输出审核标记已由服务端重新派生。

状态和 Snapshot 在同一事务内推进；失败不会留下半提交 Snapshot。

## 6. 双层保护

### 6.1 ORM层

现有 SQLAlchemy `before_flush` guard 已扩展到三类新对象：

- 只有draft Application的组成对象可增删改；
- Action parameters进行结构和标准JSON校验；
- Output审核标记必须为boolean；
- Attachment校验摘要、大小、非空字段和状态迁移；
- submitted后的组件和Snapshot不可修改。

### 6.2 PostgreSQL层

`20260722_0006` 新增：

- 受控词表CHECK；
- JSONB object和schema_version CHECK；
- 附件SHA-256正则CHECK；
- 唯一约束和必要索引；
- `guard_application_component_draft()`；
- 三个draft-only触发器。

直接SQL不能绕过提交后不可变保护。

## 7. Migration验证

真实专用 PostgreSQL 16.14 测试库完成：

```text
空库 → upgrade 0001...0006
0006 → downgrade 0005（三张扩展表消失）
0005 → upgrade 0006（三张扩展表恢复）
0006 → downgrade 0003
0003 → upgrade head
最终head = 20260722_0006
```

ORM metadata 当前20表，实库同为20表。约束和索引最长名称59字节，低于 PostgreSQL 63字节限制。

当前全库 `alembic check` 仍受既有跨schema反射配置影响，会把历史FK误报为删除后重建，并把`public.alembic_version`误报为业务表。本阶段没有借机重构全局Alembic环境；针对0006三表进行了定向metadata/实库名称比对，21个预期名称无缺失，实库额外2项仅为migration专用JSONB CHECK。

## 8. 测试结果

完整验证结果：

```text
47 passed
0 failed
0 skipped
0 warnings
```

覆盖：

- 合法和非法Action/Output词表；
- 重复Action拒绝；
- Attachment摘要和状态机；
- Snapshot包含三类扩展对象；
- 数组稳定排序和digest稳定性；
- 服务端覆盖调用方低风险声明；
- PostgreSQL CHECK和draft-only触发器；
- 直接SQL篡改拒绝；
- Catalog并发发布回归；
- Alembic真实升降级循环。

## 9. 明确边界

本阶段没有实现：

- ReviewTask / ReviewDecision
- Contract / Policy执行
- ComputeJob / Artifact
- Application CRUD或HTTP API
- MinIO附件上传
- 恶意文件扫描引擎或ScanJob
- 完整Space/Product策略引擎
- 医疗真实数据处理

因此当前能力是“完整冻结申请内容和使用意图”，不是“申请已获授权”或“数据已经可用”。

## 10. 下一阶段建议

下一阶段可以进入 Review 领域冻结和实现，但Review目标必须是完整 `ApplicationSnapshot`，不能分别审核Action、Output或Attachment。Contract仍必须等待Application审核通过，并只能收窄获批范围。
