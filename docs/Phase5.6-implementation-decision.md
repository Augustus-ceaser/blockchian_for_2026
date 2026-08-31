# Phase 5.6 实施决策

基准日期：2026-07-25

## 1. 当前执行链

现有正式链路已经完整存在：

```text
ComputeJob
-> validate_compute_job
-> prepare_compute_run
-> reserve_compute_run
-> compute.run.reserved + Outbox(compute.dispatch)
-> OutboxDispatcher
-> ConsumerInboxEntry(execution-coordinator)
-> ExecutionCoordinatorService
-> LocalBuiltInExecutorAdapter
-> ExecutionCallbackInboxEntry
-> ExecutionCallbackWorker
-> ComputeRun/ComputeJob terminal state
-> quarantined Artifact
```

Phase 5.6 不创建第二套 Dispatcher、Run、Callback 或 Artifact 模型。

## 2. 复用 Phase 4

- 复用固定 `pathmnist_resnet18_v1` registry、固定 20 个测试索引和 CPU 推理代码。
- 复用 Outbox 原子认领、持久化 Inbox、Coordinator 幂等提交和 Callback Inbox 去重。
- 复用 ComputeJob/ComputeRun 正式状态机和数据库唯一约束。
- 复用 Artifact `quarantined` 初始状态及与 Release Package 的严格分离。

## 3. 派发触发点

新增运营方产品化派发命令。命令在一个事务内重新核验 Phase 5.5 eligibility、合同、readiness、版本/digest、Connector/执行能力、run_count 和现有 Run，然后调用现有：

```text
validate_compute_job
prepare_compute_run
reserve_compute_run
```

`compute.run.reserved` 是当前正式的派发请求事实，Outbox destination 为 `compute.dispatch`。Dispatcher 只会执行已产生该事实的任务。单独启动 Worker 不会执行仅处于 `created` 的历史 Job。

## 4. Job 与 Run 状态

- Job：`created -> validating -> ready -> running -> succeeded/failed/interrupted`
- Run：`prepared -> reserved -> dispatched -> running -> succeeded/failed/interrupted`
- 同一 Job 的并发派发通过 Job 行锁、Run 非终态唯一索引和幂等 AuditCommand 收敛。
- Phase 5.5 的派发前槽位在 Job 创建时预留；Run reservation 确认同一额度，不重复消耗。

## 5. Worker

继续使用现有四个后台进程：

- Outbox Dispatcher
- Execution Coordinator
- 固定 PathMNIST Executor（Coordinator 内的 allowlisted adapter）
- Callback Worker

启动脚本必须显式启用固定 PathMNIST 模式，不使用任意 entrypoint、脚本或镜像。

## 6. 输出与隔离

固定候选输出调整为：

- `aggregate_metrics.json`
- `confusion_matrix.csv`
- `execution_summary.json`

`output_manifest` 作为 Callback/Inbox 技术元数据，不再作为第四个候选文件。输出验证继续拒绝路径穿越、未知文件、重复文件和非白名单类型。

执行器扫描成功后把三个文件写入专用 MinIO quarantine bucket/prefix。Artifact 只保存不透明对象引用、manifest digest、大小和策略证据，不暴露凭据或本地路径。

## 7. 幂等

- 重复派发：返回现有 Run，不创建第二个有效 Run。
- 重复 Outbox：Consumer Inbox 去重。
- 重复 Executor 提交：submission idempotency key 返回同一 receipt。
- 重复 Callback：Callback Inbox 去重，不重复创建 Artifact 或推进状态。
- Artifact 唯一约束和 audited command 共同防止重复结果对象。

## 8. Migration

当前模型、状态、事件词汇和约束已能表达 Phase 5.6，不需要 migration。若实现过程中发现数据库无法表达必要不可变事实，将停止并重新评估，不修改历史 migration。

## 9. Release 与下载边界

- 不调用 Artifact review/release 服务。
- 不创建 `ApprovedResultPackage`。
- 不创建 `ResultDownloadGrant`。
- 不写 release bucket/prefix。
- 前端不显示下载按钮。

## 10. 安全边界

`hard_isolation=false`。

当前能力是固定公开数据、固定白名单模型、固定 entrypoint、本地 CPU 推理、逻辑只读与前后 digest 校验。不是生产级可信执行环境、隐私计算、无网络沙箱、临床验证或医疗器械性能验证。

## 11. 回滚

- 代码回滚到 `v0.8-phase5.5-execution-readiness`。
- 已产生的 Run、Callback、Artifact 和 AuditEvent 是不可变证据，不使用手工 SQL 删除。
- 对象存储只清理本阶段专用 quarantine bucket，绝不触碰 release bucket。

