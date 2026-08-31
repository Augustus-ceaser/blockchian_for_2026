# Phase 5.7 Artifact 多方审核、安全结果包与一次性下载交付报告

阶段基准日期：2026-07-25

## 结论

Phase 5.7 已完成：

```text
2 quarantined Artifact
-> 医院出域审核
-> 模型方技术确认
-> 平台合规审核
-> 2 个独立安全结果包
-> 每包成功下载 1 次
-> 每个 token 二次使用被拒绝并审计
```

最终状态：

```text
ComputeJob = 2 succeeded
ComputeRun = 2 succeeded
Artifact = 2 quarantined
ArtifactReviewTask = 6 decided
ArtifactReviewDecision = 6 approved
ApprovedResultPackage = 2 available
ResultDownloadGrant = 2 exhausted, 1/1
result.download.completed = 2
result.download.rejected = 2
invalid audit chains = 0
```

本阶段没有重新运行推理，没有新增 Job 或 Run，没有调用 `release_artifact`，也没有提供原始 Artifact 下载。

## 实现

- 新增通用 `/api/v1/result-*` API：Artifact 列表/详情、审核计划、审核决定、结果包、下载授权、下载消费和对象级审计。
- 复用 `ArtifactReviewTask`、`ArtifactReviewDecision`、`ApprovedResultPackage` 和 `ResultDownloadGrant`，未增加影子业务表。
- 医院、模型方和平台审核均为 required；平台审核必须最后执行。
- 从 Phase 5.6 MinIO quarantine 读取 Callback 固化的三文件 manifest，逐项验证 bucket、prefix、文件名、大小、digest、JSON 和 CSV。
- 结果 ZIP 精确包含：

```text
aggregate_metrics.json
confusion_matrix.csv
execution_summary.json
```

- DownloadGrant 只保存 token SHA-256 digest，绑定 package、Space、请求组织和用户，最大下载次数为 1。
- 下载消费使用 `SELECT FOR UPDATE`；并发使用同一 token 时只有一个请求成功。
- 新增 migration `20260725_0031`，只扩展两个 AuditEvent：
  - `artifact.review.plan.created`
  - `result.download.rejected`

## 真实浏览器验收

两个 Artifact 均通过真实四角色页面完成：

| Artifact | Contract | Package | Grant |
|---|---|---|---|
| `743a2b9d-e5a6-4c0f-a819-f41d4bcafc13` | `CON-57F74162` | `3bbf064f-6283-4ffa-a5db-f90a4a51f0b4` | `7d02cc21-09f1-4a86-9c9c-03e2e354643a` |
| `5833fed4-3c8d-47d5-923f-95fd2b4f892f` | `CON-BD5902BE` | `009844a4-0cbf-44ff-8014-45abf04cd24e` | `b8d72255-4fbd-45da-bcd8-1ad8201ed226` |

浏览器确认：

- 两个 Artifact 始终显示“隔离中”和“原始下载禁止”。
- 每个 Artifact 显示 3/3 required review approved。
- 两个 package 均只列出三个允许文件。
- 两个 grant 均显示“已使用”和 `1/1`。
- 页面各触发一次“验证二次使用被拒绝”。
- 审计时间线显示 review plan、三方决定、package、grant、download completed 和 download rejected。
- 390×844 视口下 `innerWidth=390`、文档宽度 `375`，无页面级横向溢出。

## 数据库与 MinIO

- Alembic：`20260725_0031`
- 业务表：51，未新增业务表
- quarantine bucket：仍为 6 个 Phase 5.6 对象，两个 Run 各 3 个
- release bucket：2 个 ZIP
- 两个 ZIP 顶层文件均精确等于三文件白名单
- Artifact `release_status` 均为 `quarantined`
- package 均为 `available`
- grant 均为 `exhausted`，`download_count=1`，`max_downloads=1`

## 验证

- Python compileall：通过
- 后端严格完整回归：151 passed，2 skipped，0 failed，共 153 tests
- 已实际执行 Catalog 并发、Compute 并发和独立 migration cycle
- skipped：外部 PathMNIST smoke 环境、Phase 3 独立演示库
- 前端测试：32 passed
- 前端 typecheck：通过
- 前端 build：通过，3702 modules
- OpenAPI：95 paths，98 operations，无重复 operation ID
- Phase 5.7 必需路径：全部注册
- 原始 Artifact 下载路径：不存在
- `git diff --check`：通过

## Git 与边界

- 实现 Commit：`45f32b539c54734e6b03a36cba806728f07d421f`
- 冻结标签目标：`v0.10-phase5.7-controlled-result-release`
- Phase 5.0 至 Phase 5.6 标签不得移动

继续保持：

- `hard_isolation=false`
- 非临床、非生产级隐私计算、非医疗器械验证
- 不接入真实医院或患者数据
- 不支持任意模型、权重、镜像、脚本、路径或 entrypoint
- 数据库与对象存储不是分布式原子事务

代码回滚基线为 `v0.9-phase5.6-controlled-execution`。不可变审核、package、grant 和下载审计事实不得通过手工 SQL 删除。
