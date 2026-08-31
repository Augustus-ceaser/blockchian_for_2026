# Phase 2-B.8-E PathMNIST 受控冒烟测试

日期：2026-07-23  
状态：通过  
权威测试库：`medtrust_pathmnist_smoke_authoritative_20260723`（专用非生产库）  
Alembic head：`20260722_0020`  
实表：38

## 结论

固定 PathMNIST test split 20 张图与固定官方 ResNet-18 权重已完成一次端到端受控 CPU 推理：

```text
Active ContractRevision
→ ComputeJob
→ ComputeRun reserved
→ AuditEvent + Outbox
→ Dispatcher
→ Consumer Inbox
→ Coordinator
→ Local Built-in Executor
→ Execution Callback Inbox
→ Run running / succeeded
→ Artifact quarantined
```

本次没有创建下载链接、没有发布 Artifact、没有导出原图/逐样本结果/特征/权重，也没有修改历史 migration、禁用触发器或扩大 Policy。

## 资产与固定登记

- Dataset digest：`sha256:81823f52dc622e69db2db4c72f8e8e617938dd6864d3c1f23d4e49724a28ea72`
- Model digest：`sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0`
- Model registration digest：`sha256:cde1049d2777ce5d05fc6dfbe3cd03ecaea4890bb055abe7f5f46b80c4b29736`
- Dataset registration digest：`sha256:5ca3141fa3efbb1ae00e050d266fccff710aa5018cdddd77094f7ccb37c35009`
- Compatibility digest：`sha256:18a303a2bbdbab6108248c1a71d2055645f7d9a4e0da123a34f6d7ead25e4197`
- Smoke plan digest：`sha256:7159827874700a003afb76009bfe0bdf4f7500b337988f3d7a75f832cd203dbd`
- Entrypoint：代码内固定 `pathmnist_resnet18_v1`；不接受用户 Python 路径、Shell 命令或模型代码。

## 权威运行结果

- Run ID：`9e615302-a2d1-49c3-8be3-f9685cead351`
- Artifact ID：`07d98c50-c55d-4027-9a84-b9251ee95ea7`
- Run：`succeeded`
- run_count：`reservation_ordinal=1`，`run_limit_snapshot=1`
- Artifact：`quarantined`
- 样本数：20
- 准确率：0.95（19/20）
- 平均置信度：0.960102856159
- 预测分布：`[4, 0, 2, 1, 1, 3, 2, 2, 5]`
- Prediction digest：`sha256:bc76bb795f4c7a47ce7ad41dca7fcbc7ac33f3f82a9eee6ac36385a822bb678d`
- CPU 推理时间：约 0.20 秒（单次工程测量）
- 进程峰值常驻内存：约 430.76 MiB

混淆矩阵：

```text
[[4,0,0,0,0,0,0,0,0],
 [0,0,0,0,0,0,0,0,0],
 [0,0,2,0,0,1,0,0,0],
 [0,0,0,1,0,0,0,0,0],
 [0,0,0,0,1,0,0,0,0],
 [0,0,0,0,0,2,0,0,0],
 [0,0,0,0,0,0,2,0,0],
 [0,0,0,0,0,0,0,2,0],
 [0,0,0,0,0,0,0,0,5]]
```

## 流程与安全验收

| 验收项 | 结果 |
|---|---|
| run_count 原子消耗 | 通过；1/1 |
| 重复 compute.dispatch 投递 | 通过；Consumer Inbox 仅 1 行，未重复执行 |
| 重复 completed Callback | 通过；Callback Inbox 2 行（started/completed），Artifact 仅 1 个 |
| 合同非 active 时拒绝 | 全量回归通过 |
| Connector 离线 / Capability disabled | 全量回归通过 |
| Policy deny 不可人工覆盖 | PostgreSQL 专项回归通过 |
| Artifact 默认隔离 | 通过；`quarantined`，无 release evidence |
| Audit 链 | 7 个关键事件，按 Space 验证有效 |
| Outbox | 9/9 `published`，0 个未投递 |
| 本地路径/凭据泄漏扫描 | Audit、Callback Inbox、Artifact storage reference 命中数均为 0 |
| 全后端回归 | 134 passed，4 个明确环境门禁测试 skipped |

首批事件完整包含：

```text
contract.revision.activated
compute.job.created
compute.run.reserved
compute.run.dispatched
compute.run.started
compute.run.completed
artifact.created
```

## 输出边界

执行工作区仅产生以下四个 JSON 文件，并在验证后清理临时工作区：

```text
aggregate_metrics.json
class_distribution.json
execution_summary.json
output_manifest.json
```

数据库只保留隔离 Artifact 的摘要、策略评估和不含宿主机路径的 opaque storage reference。Consumer Inbox 本身不持久化业务 payload，只保存来源和摘要证据。

## 失败与修正记录

- 第一次测试断言在数据库触发器分配 `reservation_ordinal` 后没有刷新 ORM 对象；修正为刷新后校验，未改数据库规则。
- 第二次测试把完成后的 Job 错误预期为 `ready`；实际 Callback 正确将 Job 推进为 `succeeded`。只修正测试断言，未绕过任何状态守卫。
- 最终权威结果在全新专用库中重新执行，严格得到 1 个 Run、1 个 quarantined Artifact 和 0 个未投递 Outbox。

## 明确限制

这是工程原型的受控推理测试，不是临床验证、生产级隐私计算、可信数据空间国家测评或医疗器械性能验证。当前 Local Built-in Executor 是固定入口的进程内原型，`hard_isolation=false`；它不支持任意用户模型、训练、完整数据集、WSI、患者级输出或自动发布。
