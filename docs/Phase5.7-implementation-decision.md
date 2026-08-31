# Phase 5.7 实施决策

基准日期：2026-07-25

## 1. 冻结基线

- 分支：`main`
- 当前 HEAD：`5b2ed2becd28475d592510413b01f055886c7eb1`
- Phase 5.6 实现提交：`e39acbc7ef0224d1474c42cf381ba9a1ed0855ae`
- Phase 5.6 冻结提交：`c999c4f77156db5962bda5e7a8817d119335db7b`
- annotated tag 对象：`3697d88e765d8b021c8d906369753bc84e5a683a`
- tag 解引用目标：`c999c4f77156db5962bda5e7a8817d119335db7b`
- `git tag --points-at e39acbc` 为空；不得据此误判标签丢失或移动
- 工作区：干净
- Alembic head/current：`20260724_0030`
- 业务表：51
- 当前业务状态：2 个 succeeded ComputeJob、2 个 succeeded ComputeRun、2 个 quarantined Artifact、0 个审核任务、0 个结果包、0 个下载授权
- MinIO quarantine：两个 Run 各三个对象，共六个对象
- 应用进程已停止；PostgreSQL 和 MinIO 运行中

## 2. skipped 测试审计

Phase 5.6 权威回归仍为 152 项中的 147 passed、5 skipped、0 failed。五个跳过项为：

1. Catalog destructive race：未设置 `MEDTRUST_RUN_CATALOG_CONCURRENCY_TEST=1`
2. Compute committed race：未设置 `MEDTRUST_RUN_COMPUTE_CONCURRENCY_TEST=1`
3. PathMNIST controlled smoke：显式执行资产环境未配置
4. Phase 3 demo API：`MEDTRUST_PHASE3_DEMO_DATABASE_URL` 未配置
5. migration cycle：`MEDTRUST_MIGRATION_CYCLE_DATABASE_URL` 未配置

从 Phase 5.5 冻结点到当前没有删除测试文件。Phase 5.6 新增一个 PostgreSQL 专项文件，并扩展三个既有测试文件。Phase 5.5 的 2 skips 是开启 destructive race 和 migration cycle 后的组合结果；Phase 5.6 报告采用常规完整回归口径，因此三个环境门禁重新表现为 skipped，并非测试被删除或失效。

## 3. 复用结论

直接复用：

- `ArtifactReviewTask`
- `ArtifactReviewDecision`
- `ApprovedResultPackage`
- `ResultDownloadGrant`
- `create_artifact_review_plan`
- `claim_artifact_review_task`
- `decide_artifact_review_task`
- `create_approved_result_package`
- `create_download_grant`
- `consume_download_grant`
- `MinioReleaseObjectStore`
- AuditEvent 哈希链、命令幂等和组织/空间角色校验

不新增第二套审核表、结果包表、下载令牌表或 Artifact 状态机。

不能原样复用的 Phase 4 演示适配：

- 固定 `latest_phase4_artifact` 只面向单个历史演示合同，不能处理当前两个真实 Artifact。
- `build_phase4_safe_files` 从本地运行目录读取，不是 Phase 5.6 的权威 quarantine 来源。
- Phase 4 路由没有按 Artifact/Contract 做通用资源寻址。
- 现有下载失败只返回错误，没有持久化重复、过期或越权拒绝证据。

## 4. 审核规则

每个 quarantined Artifact 创建三类任务：

- 医院数据出域审核：required
- 模型提供方技术确认：当前合同存在 `model_provider` 参与方时 required
- 平台合规审核：required，且只能在前两项 required 审核批准后决定

当前两个合同均有模型提供方，因此最终需要 2 次医院批准、2 次模型批准和 2 次平台批准。

任务绑定：

- Artifact ID 和 content digest
- ContractRevision
- responsible organization
- review type
- routing digest

既有表没有 ContractRevision 列；ContractRevision 由 Artifact -> Run -> Job 的不可变外键链确定，审核 decision evidence 同时固化合同、Artifact manifest 和批准文件摘要。该事实可由现有不可变关系完整重建，不增加冗余外键。

## 5. Artifact 与审核状态分离

`Artifact.release_status` 在本阶段始终为 `quarantined`。

审核进度只由 `ArtifactReviewTask` 和 `ArtifactReviewDecision` 表达。全部 required 审核通过只产生“允许生成独立结果包”的资格，不调用 `release_artifact`，不把 Artifact 改为 `released`，不提供原始 Artifact 下载。

## 6. 安全结果包

结果包必须从 Artifact 的 opaque quarantine reference 解析出受控 bucket/prefix，并由后端使用 MinIO 凭据读取。前端永远看不到 bucket、object key、凭据或内部路径。

读取时必须逐项验证：

- prefix 与 Artifact/Run 绑定
- 文件名精确等于三个允许项
- 每项大小和 digest 与已存 manifest/Artifact content digest 一致
- JSON 和 CSV 可解析
- 不存在额外对象

ZIP 顶层精确包含：

- `aggregate_metrics.json`
- `confusion_matrix.csv`
- `execution_summary.json`

不包含 README、内部 manifest、日志、原始图像、样本级结果、模型权重、源代码、环境变量、路径或凭据。

`ApprovedResultPackage` 是独立数据库对象，写入独立 release bucket。`Artifact` 保持 quarantined，两个对象不共享下载入口。

## 7. 下载授权与原子消费

继续使用 `ResultDownloadGrant`：

- 随机 256-bit 级 token
- 数据库只保存 SHA-256 digest
- 绑定 package、Space、请求企业 organization 和 requester user
- 默认 5 分钟有效
- `max_downloads=1`

消费使用 PostgreSQL `SELECT ... FOR UPDATE`。授权校验、package 校验、对象 digest 校验完成后才更新 `download_count/status`，并在同一事务写入 `result.download.completed`。并发使用同一 token 时只有一个事务可成功。

失败授权不得消耗 grant。重复、过期、撤销、跨组织、跨用户、package 不可用和 digest 不匹配需要明确拒绝。已知 grant 的拒绝证据在消费事务回滚后由独立审计事务写入，避免“为了审计失败而提交部分消费状态”。

## 8. Migration 决策

需要一个最小 migration `20260725_0031`，只扩展 AuditEvent 词汇：

- `artifact.review.plan.created`
- `result.download.rejected`

原因：

- 审核计划创建是正式业务事实，不能只靠当前表状态推断。
- 用户明确要求记录二次下载和其他拒绝证据；现有数据库 CHECK 和 audit guard 不允许表达该事件。

不新增业务表，不修改历史 migration，不改变 Artifact/Run/Job 状态机。

## 9. API 与权限

新增通用结果中心 API，按当前登录组织过滤：

- Artifact 列表和详情
- 创建审核计划
- 提交医院、模型、平台审核决定
- 生成结果包
- 创建一次性下载授权
- 消费下载授权
- Artifact/Package/Grant 审计时间线

权限：

- 医院只处理使用本组织数据的 Artifact
- 模型方只处理使用本组织模型的 Artifact
- 平台只能处理所在 Space 且前置 required 审核已通过的 Artifact
- 请求企业只能查看本组织合同结果、创建绑定自己的 grant 并下载自己的 package
- 无关组织不能查看 Artifact、审核、package 或 grant

没有任何原始 Artifact 文件下载 API。现有 `/artifacts/{artifact_id}` 只返回结构化元数据，且 `artifact_download_enabled=false`。

## 10. 幂等与并发

- 审核计划：Artifact 行锁 + `(artifact_id, review_type)` 唯一约束
- 审核决定：任务行锁 + command idempotency + 每任务唯一 decision
- package：Artifact 行锁 + `uq_result_packages_artifact`
- grant：命令幂等；每次显式创建生成独立短期授权
- download：grant 行锁保证一次性原子消费
- 拒绝审计：命令幂等，网络重试不重复写事件

React StrictMode 和按钮双击通过现有 single-flight 与后端幂等双重控制。

## 11. 不变边界

- 不新增 ComputeJob
- 不新增 ComputeRun
- 不重新运行推理
- 不修改既有 Run、Callback 或 Artifact 内容
- 不解除 Artifact 隔离
- 不创建公开或永久 MinIO URL
- 不暴露 token、bucket、object key、凭据或本地路径
- `hard_isolation=false`
- 非临床、非生产隐私计算、非医疗器械验证

## 12. 回滚

- 代码回滚到 `v0.9-phase5.6-controlled-execution`
- migration 0031 只回退新增 AuditEvent 词汇
- 已产生的审核决定、结果包、grant 和下载事件属于不可变业务证据，不使用手工 SQL 删除
- release bucket 中无数据库引用的孤立对象由受控清理工具按专用 bucket 清理
- 永不清理或修改 Phase 5.6 quarantine 对象
