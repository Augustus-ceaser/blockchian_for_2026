# CODEX -> CHATGPT 阶段回传

## 1. 阶段

- 阶段名称：Phase 5.5 执行资产就绪、资格校验与 ComputeJob 创建产品化
- 完成状态：完成
- 授权基准日期：2026-07-24
- 最终验收日期：2026-07-25
- 阶段边界：停止在真实 ComputeJob 已创建但尚未派发；未进入 Phase 5.6

## 2. Git

- Branch：`main`
- 实现 Commit：`0d646870bf70ba5ddc0d58bd989b6c490b313e5c`
- 冻结 Commit：回传文档提交由标签 `v0.8-phase5.5-execution-readiness` 稳定指向，避免在提交内容中写入无法自引用的提交哈希
- Tag：`v0.8-phase5.5-execution-readiness`
- 工作区：冻结提交和标签创建后 clean
- 旧标签：`v0.3` 至 `v0.7` 均保持原提交不变

## 3. 数据库

- Alembic：`20260724_0030`
- Migration：`20260724_0030_phase5_execution_readiness.py`
- 业务表：51
- 新增表：`contract_readiness_revocations`、`execution_eligibility_snapshots`、`execution_eligibility_invalidations`
- 增量迁移：Phase 5.4 演示库从 0029 升级到 0030 通过
- 空库迁移：从空库完整升级到 0030 通过
- 迁移循环：独立数据库完整升降级后恢复到 0030
- 历史 active 合同：迁移回填两份合同共 6 条 accepted 固定能力绑定

## 4. 后端

- 测试：148 passed，2 skipped，0 failed
- skipped 变化原因：启用了既有破坏性环境门禁测试；剩余两项是已有 PathMNIST 真实执行资产门禁，本阶段未删除测试、未运行推理
- Python 编译：compileall 通过
- OpenAPI：86 paths，89 operations
- 权限：医院、模型方、平台和需求方的写权限均由后端按合同组织和角色校验
- 幂等：readiness、资格检查和 ComputeJob 创建均支持安全重试
- 并发：原子派发前槽位保证 `run_count=1` 时不能创建两个有效待执行任务

## 5. 前端

- 测试：27 passed
- typecheck：通过
- build：通过，3701 modules
- 移动端：390x844 无页面级横向溢出
- 新页面：`/execution`、`/execution/:contractId`
- 页面能力：四角色执行准备、数据/模型就绪、资格矩阵、不可变快照、任务详情、审计证据和技术抽屉

## 6. 浏览器验收

- active 合同数量：2
- data ready 数量：2
- model ready 数量：2
- eligibility snapshot 数量：2
- ComputeJob 数量：2
- ComputeRun 数量：0
- Artifact 数量：0
- 成功流程次数：2
- 合同：`CON-A688F28D`、`CON-9F32FFBB`
- 任务：`f12b639f-b963-4f48-84f1-69e3c18cd8cf`、`983de58a-76ab-4c8c-badc-a44093c01246`
- 是否使用手工 SQL：否

## 7. 审计与安全

- AuditEvent：记录 readiness 确认/撤销、资格通过/阻断/失效、ComputeJob 创建和派发前槽位预留
- 无效审计链：0
- 敏感信息：未提交密钥、Token、数据库密码、原始医疗数据、模型权重、Connector 凭据或本地绝对路径
- hard_isolation：`false`，在资格矩阵中明确显示为 WARNING
- 执行边界：未产生 `compute.dispatch`、ComputeRun、Artifact 或伪造执行日志

## 8. 修改摘要

- 主要修改：新增 readiness 撤销、不可变执行资格快照及失效记录，扩展 ComputeJob 资格和槽位绑定，增加通用 API、四角色页面、测试、迁移和交付文档
- ComputeJob 规则：仅需求方可基于当前有效快照创建；创建时原子预留派发前槽位，但不代表执行完成
- 资产锁定：任务固定合同版本、数据版本、模型版本、Connector/固定执行资产、策略和资格 digest
- 未实现：Dispatcher、Executor、Coordinator、Callback、推理、ComputeRun、Artifact、下载、计费、任意代码/模型上传、真实医院数据或生产级硬隔离

## 9. 阻塞和风险

- 阻塞：无
- 已知限制：单机工程演示；`hard_isolation=false`；Outbox pending 不表示任务已执行
- 运行次数边界：Phase 5.5 只定义派发前槽位预留；派发、Run 启动、失败释放和取消恢复语义留待后续独立设计
- 人工确认：任何 Phase 5.6 工作必须重新授权，并先审计派发、执行、回调、Artifact 隔离和运行次数结算

## 10. 下一阶段建议

- 建议阶段：Phase 5.6 任务派发、固定执行器运行、Artifact 隔离与执行证据产品化
- 理由：Phase 5.5 已形成真实、不可变、可审计且未派发的 ComputeJob
- 不应提前开发：任意代码或模型上传、真实医院数据、下载令牌、计费、生产级硬隔离或全站 UI 重构
- 当前动作：停止，不自动进入 Phase 5.6
