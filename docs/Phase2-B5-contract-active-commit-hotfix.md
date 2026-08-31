# Phase 2-B.5-D3 Contract Active Commit Hotfix

> 状态：已完成并通过 PostgreSQL 16 真实验证。本批只纠正 Contract 延迟一致性触发器，不实现 Artifact、Audit/outbox、API、前端或真实计算。

## 1. 结论

新增纠正迁移：

```text
20260722_0011_compute_jobs_runs
  -> 20260722_0012_contract_active_commit_fix
```

没有修改已经应用过的0010或0011，也没有新增、删除业务表。当前Alembic head为`20260722_0012`，`medtrust` schema仍为32张实表。原Artifact迁移编号顺延为0013。

合法的完整active Contract图现在可以真实COMMIT，并可作为ComputeJob的可靠上游；Audit/outbox仍未实现，因此Run预留和真实启动继续fail-closed。

## 2. 根因与最小复现

0010将同一个函数挂在两个记录结构不同的延迟约束触发器上：

```text
contract_signatures
  -> trg_contract_signature_consistency

contract_revisions
  -> trg_contract_revision_signed_consistency

共同调用：guard_contract_revision_signed_consistency_v6()
```

v6函数使用下列表达式选择Revision id：

```sql
CASE WHEN TG_TABLE_NAME='contract_signatures'
     THEN NEW.contract_revision_id
     ELSE NEW.id
END
```

在`contract_revisions`触发上下文中，`NEW`没有`contract_revision_id`字段。即使运行分支应选择`NEW.id`，PL/pgSQL仍会解析不存在的记录字段，导致延迟检查在COMMIT阶段失败。

全新PostgreSQL 16数据库从空库升级到0011后，完整构造合法链路并提交，得到：

| 项目 | 结果 |
| --- | --- |
| 异常类型 | `asyncpg.exceptions.UndefinedColumnError` |
| SQLSTATE | `42703` |
| 消息 | `record "new" has no field "contract_revision_id"` |
| 失败触发器 | `trg_contract_revision_signed_consistency` |
| 失败函数 | `medtrust.guard_contract_revision_signed_consistency_v6()` |
| 触发时点 | `DEFERRABLE INITIALLY DEFERRED`约束在COMMIT时执行 |

使用`SET CONSTRAINTS`分别强制检查进一步确认：签名触发器通过，Revision触发器稳定复现42703。失败事务没有留下部分active状态。

## 3. 修复方式

0012删除旧的两个触发器及共享v6触发函数，创建：

```text
assert_contract_revision_signed_consistency_v7(uuid)
  <- guard_contract_signature_consistency_v7()
  <- guard_contract_revision_signed_consistency_v7()
```

- Signature专用入口只读取`NEW.contract_revision_id`；
- Revision专用入口只读取`NEW.id`；
- 两者把明确的Revision id交给共享assert helper；
- 两个原触发器名称、触发事件和`DEFERRABLE INITIALLY DEFERRED`属性保持不变。

降级到0011时，迁移会恢复原v6函数及原触发器；再次升级会重新安装v7结构。因此历史数据库和新数据库遵循同一条可追溯升级路径。

## 4. 未放松的安全不变量

共享v7 helper保留v6的原判定：

1. proposed Revision的最后一份必需签名到齐时，必须在同一事务将Revision推进到signed；
2. signed、active、suspended、expired、terminated Revision必须拥有全部必需方的verified签名；
3. 所有签名必须针对同一个Revision `content_digest`；
4. Signature继续append-only，不能UPDATE或DELETE；
5. active前仍由既有守卫验证Policy、Binding回执、Connector在线状态、精确Capability版本、参与组织、产品版本、Review事实和有效期；
6. 直接SQL不能绕过proposal、signature或active守卫。

修复只改变“如何安全取得Revision id”，没有改变签名计数、状态集合、摘要匹配或active准入规则。

## 5. PostgreSQL专项验证

| 验证项 | 结果 | 主要覆盖 |
| --- | --- | --- |
| 同一事务组装完整合同、signed、active并COMMIT | 通过 | 新Hotfix集成测试 |
| 多事务逐步组装、签署、接收Binding、active并COMMIT | 通过 | 新Hotfix集成测试 |
| active合同创建并提交合法ComputeJob | 通过 | 新Hotfix集成测试 |
| Audit/outbox缺失时Run预留 | 仍以`AuditEvidenceUnavailable`拒绝 | Hotfix + Compute专项 |
| 缺少必需签名，直接SQL推进signed并COMMIT | 延迟触发器拒绝，事务回滚后仍为proposed | 新Hotfix集成测试 |
| 签名内容或摘要修改 | append-only/FK拒绝 | Contract Signature PostgreSQL专项 |
| 缺少Policy | proposal服务拒绝；直接SQL又被proposal证据守卫拒绝 | 新Hotfix + Policy专项 |
| Binding未accepted或无回执 | active拒绝 | Contract Signature PostgreSQL专项 |
| Connector离线 | active数据库守卫拒绝 | 新Hotfix集成测试 |
| Capability disabled或精确版本不存在 | active守卫/复合FK拒绝 | 新Hotfix + Policy专项 |
| Review事实不完整 | active数据库守卫拒绝 | 新Hotfix集成测试 |
| 合同参与组织suspended | active数据库守卫拒绝 | 新Hotfix集成测试 |
| 合同引用的数据产品suspended | active数据库守卫拒绝 | 新Hotfix集成测试 |
| active后修改合同内容 | PostgreSQL不可变守卫拒绝 | Contract Signature PostgreSQL专项 |
| Compute run_count并发限额1 | 一个事务成功、一个被数据库拒绝；不再停用Contract触发器 | Compute PostgreSQL并发专项 |

## 6. 迁移与回归结果

- 历史0011库复现：稳定得到SQLSTATE 42703；
- 0011升级0012：合法active合同真实COMMIT成功；
- `0012 -> 0011 -> 0012`真实循环：通过；
- 空数据库完整`upgrade head`：通过，最终0012、32表、3个v7 consistency函数；
- 两个延迟触发器最终均为enabled，并分别指向各自v7入口；
- Python compileall：通过；
- 依赖检查：无破损依赖；
- 全后端回归：89/89通过，包含Catalog/Compute并发及迁移循环开关。

## 7. 文件清单

| 文件 | 作用 |
| --- | --- |
| `backend/alembic/versions/20260722_0012_contract_active_commit_fix.py` | 纠正迁移及对称降级 |
| `backend/tests/integration/test_contract_active_commit_hotfix_postgresql.py` | 合法/非法COMMIT与active守卫专项 |
| `backend/tests/integration/test_compute_postgresql.py` | 移除临时禁用Contract触发器的种子绕行 |
| `backend/tests/integration/test_zz_catalog_migration_cycle_postgresql.py` | 增加0012到0011再到0012函数/触发器循环 |
| `docs/Phase2-B5-contract-active-commit-hotfix.md` | 根因、修复和验证证据 |

## 8. 当前边界与后续顺序

本批没有实现Artifact或Audit。下一步顺序保持：

```text
0012 Contract触发器纠错（已完成）
  -> 0013 Artifact / ArtifactReview
  -> Audit / transactional outbox
  -> Run真实启动与内置模拟执行器
```

在Audit/outbox完成前，`assert_compute_audit_ready_v7()`仍固定抛出`AuditEvidenceUnavailable`，不能用普通日志或模拟记录替代可靠审计证据。
