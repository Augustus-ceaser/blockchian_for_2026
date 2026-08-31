# Phase 5.5 执行就绪、资格校验与 ComputeJob 创建交付报告

阶段基准日期：2026-07-24  
最终验收日期：2026-07-25

## 1. 阶段结论

Phase 5.5 已完成：

```text
active Contract
-> 医院确认数据执行就绪
-> 模型方确认固定模型资产就绪
-> 平台执行资格检查
-> 不可变 Execution Eligibility Snapshot
-> 需求企业创建真实 ComputeJob
-> 原子预留派发前运行槽位
-> 停止在未派发状态
```

最终边界：

```text
ComputeJob = 2
ComputeRun = 0
Artifact = 0
invalid audit chains = 0
```

未启动 Dispatcher、Executor、Coordinator、Callback、推理、结果生成、下载或 Phase 5.6。

## 2. 核心设计

- 复用 `ContractReadinessConfirmation` 保存不可变就绪确认。
- 新增 append-only readiness revocation 和 eligibility invalidation。
- 新增不可变 `ExecutionEligibilitySnapshot`，绑定合同版本、数据版本、模型版本、Connector 能力、固定 registry、策略和审核要求。
- ComputeJob 绑定资格快照及其 digest，不会静默切换到新数据或新模型。
- 创建 ComputeJob 与创建 ComputeRun 完全分离；本阶段不触发 `compute.dispatch`。
- `run_count` 在本阶段表示原子预留派发前槽位，不表示执行已消费或已完成。`run_count=1` 时并发创建只允许一个有效任务。

## 3. 数据与模型就绪

医院只能为本组织数据合同确认 `data_ready`；模型方只能为本组织固定模型确认 `model_ready`。服务端校验 active 合同、固定版本、摘要、节点归属、在线状态、能力、有效期、只读输入和禁止下载边界。

就绪确认记录锁定 Contract/Revision、数据或模型版本、资源或模型 digest、Connector 或固定执行资产、声明版本、确认人、组织、时间和证据摘要。创建 ComputeJob 后不允许撤销 readiness。

## 4. 平台资格检查

资格矩阵由服务端生成，结果为 `PASS`、`WARNING` 或 `BLOCKER`，覆盖合同、就绪、能力绑定、运行次数、数据模型兼容、输出规则、网络、只读输入、审核要求、审计链和环境边界。

`hard_isolation=false` 继续作为 WARNING 明示，不被隐藏，也不在当前工程演示中阻断任务创建。

只有零 BLOCKER 时生成不可变资格快照。相同有效输入可幂等返回既有快照；关键事实变化会使旧快照失效。

## 5. ComputeJob 与运行次数

ComputeJob 创建时：

- 仅需求企业可写
- 必须绑定当前有效资格快照
- 以数据库事务原子预留 `slot_ordinal`
- 重复 idempotency key 返回原任务
- 并发 `run_count=1` 只允许一个成功
- 不创建 ComputeRun
- 不派发消息
- 不创建 Artifact

该槽位是派发前容量占用，不是已完成执行次数。派发、Run 启动、失败释放或取消语义留给 Phase 5.6。

## 6. Migration 0030

`20260724_0030_phase5_execution_readiness.py`：

- 新增 `contract_readiness_revocations`
- 新增 `execution_eligibility_snapshots`
- 新增 `execution_eligibility_invalidations`
- 为 ComputeJob 增加资格快照和派发前槽位绑定
- 扩展 AuditEvent 词汇
- 为两份历史 active 合同回填 6 条 accepted 固定能力绑定

迁移使用 Python SHA-256 计算回填摘要，不依赖 `pgcrypto`。空库迁移、现有库增量迁移和独立完整迁移循环均通过，历史 migration 未修改。

## 7. API 与权限

统一前缀 `/api/v1`：

- `GET /execution-readiness`
- `GET /execution-readiness/{contract_id}`
- `POST /execution-readiness/{contract_id}/data-readiness`
- `POST /execution-readiness/{contract_id}/model-readiness`
- `POST /execution-readiness/{contract_id}/eligibility-check`
- `POST /execution-readiness/{contract_id}/jobs`
- `POST /execution-readiness/readiness/{confirmation_id}/revoke`
- `GET /execution-readiness/{contract_id}/audit-events`

所有写操作要求 `Idempotency-Key`，权限和状态均由后端领域服务验证。

## 8. 审计事件

关键事件：

- `contract.readiness.confirmed`
- `contract.readiness.revoked`
- `execution.eligibility.passed`
- `execution.eligibility.blocked`
- `execution.eligibility.invalidated`
- `compute.job.created`
- `compute.job.pre_dispatch_slot_reserved`

没有生成 `compute.run.reserved`、`compute.dispatch`、执行完成或 Artifact 事件。

## 9. 前端与浏览器

新增 `/execution` 和 `/execution/:contractId`，覆盖四角色列表/详情、就绪确认、资格矩阵、快照、任务、证据时间线和技术抽屉。

页面明确显示“ComputeRun 未创建”“Artifact 未生成”和 `hard_isolation=false`。

390x844 真实浏览器检查发现并修复了长 UUID、SHA 和 Ant Descriptions 固有宽度造成的页面级横向溢出。最终 `documentClientWidth=375`、`documentScrollWidth=375`、`bodyScrollWidth=375`。

## 10. 验证结果

- 后端权威回归：148 passed，2 skipped，0 failed
- skipped：两个既有 PathMNIST 真实执行资产环境门禁，本阶段未运行推理
- Phase 5.5 PostgreSQL 专项：通过
- Python compileall：通过
- 前端测试：27 passed
- 前端 typecheck：通过
- 前端 build：通过，3701 modules
- OpenAPI：86 paths，89 operations
- Alembic：`20260724_0030`
- 业务表：51
- UTF-8 / mojibake 扫描：通过
- 敏感信息与本地路径 diff 扫描：通过
- `git diff --check`：通过

## 11. 真实验收

两份历史 active 合同均通过正式 API 完成就绪、资格检查和任务创建，未使用手工 SQL 修改业务状态。

| 合同 | Eligibility | ComputeJob |
|---|---|---|
| `CON-A688F28D` | `6503f79f-ccde-4674-a931-38f5ff2e76d5` | `f12b639f-b963-4f48-84f1-69e3c18cd8cf` |
| `CON-9F32FFBB` | `85155c55-b627-4b81-9202-b56d32462814` | `983de58a-76ab-4c8c-badc-a44093c01246` |

2026-07-25 真实浏览器验收确认：

- API 模式列表显示两份合同和两个待派发任务
- 详情显示 PASS/WARNING 矩阵、零 BLOCKER、资格快照和槽位
- 技术证据抽屉显示 Event ID、actor、organization、前后哈希、Outbox 和脱敏 evidence
- 明确显示未派发、ComputeRun 未创建、Artifact 未生成
- 390x844 无页面级横向溢出

## 12. 已知限制与回滚

- `hard_isolation=false`
- 单机工程演示，不是生产级隔离或跨主机消息系统
- Outbox 可为 pending；这不等于执行已发生
- 不支持任意模型、脚本、镜像、路径或真实医院数据
- Phase 5.6 必须重新授权后才能开发派发、执行、回调和 Artifact

代码回滚基线：`v0.7-phase5.4-digital-contract-lifecycle`。

数据库存在 Phase 5.5 证据时不得粗暴降级；应保留不可变审计事实并执行受控回滚。
