# Phase 5.6 受控派发、固定执行与 Artifact 隔离交付报告

阶段基准日期：2026-07-25

## 结论

Phase 5.6 已完成：

```text
显式运营派发 -> durable Outbox/Inbox -> 唯一 ComputeRun
-> 固定 PathMNIST/ResNet-18 CPU 推理
-> 三文件输出白名单 -> Callback -> MinIO quarantine
-> quarantined Artifact
```

最终状态：

```text
ComputeJob = 2 succeeded
ComputeRun = 2 succeeded
Artifact = 2 quarantined
ReleasePackage = 0
DownloadGrant = 0
invalid audit chains = 0
```

未进入 Artifact 审核、Release Package、ZIP、下载授权、任意模型或脚本执行，以及 Phase 5.7。

## 实现

- 新增 `POST /api/v1/execution-readiness/jobs/{job_id}/dispatch`；仅空间运营方可派发，重复调用返回同一 Run。
- 复用既有 `validate_compute_job -> prepare_compute_run -> reserve_compute_run -> compute.dispatch -> Outbox/Inbox -> Coordinator -> Callback` 正式链。
- 修复 Phase 5.5 Job 预占槽位与其派生 Run 被重复计算的问题。Coordinator 与 Callback 授权复核同时排除当前 `Run` 和来源 `Job`。
- Executor 只从冻结请求中选择一个、并经固定 registry 再校验的输出类型。Phase 5.5 的 `aggregate_statistics` 不再被历史 `model_artifact` 硬编码覆盖。
- Callback Worker 逐项验证三个输出文件的名称、路径、大小和摘要，并写入专用 MinIO quarantine bucket；Artifact 仅保存不透明 `minio-quarantine/...` 引用。

允许文件：

```text
aggregate_metrics.json
confusion_matrix.csv
execution_summary.json
```

## 真实执行证据

- 公开 PathMNIST 固定 20 图
- 固定 ResNet-18
- CPU
- 正确预测：19
- Accuracy：0.95
- Mean confidence：0.960102856159
- `network_access=false`
- `hard_isolation=false`

两个 Artifact 对应六个真实 MinIO 对象，前缀为：

```text
quarantine/{run_id}/{manifest_digest}/
```

真实重复 Callback 返回 `created=false`，未新增 Run 或 Artifact。

## 验证

- Python compileall：通过
- 后端完整回归：147 passed，5 skipped，0 failed，共 152 tests
- Phase 5.6 PostgreSQL 专项：通过
- 前端测试：27 passed
- 前端 typecheck：通过
- 前端 build：通过，3701 modules
- OpenAPI：87 paths，90 operations
- Alembic：`20260724_0030`
- 业务表：51
- preflight：通过；运行中端口产生 2 个预期 warning
- `git diff --check`：通过

桌面真实浏览器确认两条 succeeded 任务、真实时间线、运行指标、quarantined Artifact 和无下载按钮。390px 响应式由前端自动化断言覆盖；本轮内置浏览器接口不支持精确调整到 390×844，因此未虚报手工移动端截图。

## 边界与回滚

- `hard_isolation=false`
- 不接入真实医院数据
- 不允许任意模型、权重、镜像、脚本、路径或 entrypoint
- Artifact 保持 quarantined，不可下载
- 数据库与对象存储不是分布式原子事务

代码回滚基线：`v0.8-phase5.5-execution-readiness`。不得删除不可变审计事实或未知对象。
