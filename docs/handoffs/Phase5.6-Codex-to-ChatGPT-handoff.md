# CODEX → CHATGPT 阶段回传

## 1. 阶段

- 阶段名称：Phase 5.6 受控派发、固定执行与 Artifact 隔离
- 完成状态：正式完成
- 基准日期：2026-07-25，星期六

## 2. Git

- Branch：`main`
- 实现 Commit：`e39acbc7ef0224d1474c42cf381ba9a1ed0855ae`
- 冻结 Commit：`c999c4f77156db5962bda5e7a8817d119335db7b`
- Tag：`v0.9-phase5.6-controlled-execution`
- 工作区：clean
- 旧标签：`v0.3` 至 `v0.8` 未移动

## 3. 数据库

- Alembic：`20260724_0030`
- Migration：本阶段无新增 migration
- 业务表：51
- 增量迁移：通过
- 空库迁移：通过
- 独立 migration cycle：通过

## 4. 后端

- 测试：147 passed
- skipped：5 个环境门控
- failed：0
- Python 编译：通过
- OpenAPI paths：87
- OpenAPI operations：90
- Worker：Backend、Dispatcher、Coordinator、Callback Worker 均健康
- 权限：只有空间运营方可派发
- 幂等：重复派发复用 Run；重复 Callback 不新增 Artifact

## 5. 前端

- 测试：27 passed
- typecheck：通过
- build：通过
- modules：3701
- 移动端：390px 响应式自动化断言通过；本轮内置浏览器不支持精确手工缩放到 390×844
- 新页面：执行详情展示 Run、Callback、指标和 quarantined Artifact

## 6. 浏览器验收

- ComputeJob：2
- ComputeJob 最终状态：2 succeeded
- ComputeRun：2
- ComputeRun 最终状态：2 succeeded
- Artifact：2
- quarantined Artifact：2
- Release Package：0
- 下载授权：0
- 成功流程次数：2
- 重复 Callback：`created=false`
- 是否使用手工 SQL 修改业务状态：否

## 7. 真实推理

- 数据集：公开 PathMNIST 固定测试子集
- 模型：固定 ResNet-18
- 图像数量：20 / Run
- 设备：CPU
- Accuracy：0.95
- Mean confidence：0.960102856159
- 数据 digest：执行前后一致
- 模型 digest：验证通过
- 非白名单输出：0

## 8. 审计与安全

- AuditEvent：Run reserved/dispatched/started/completed 与 Artifact created 完整
- 无效审计链：0
- Outbox/Inbox：durable 且幂等
- MinIO：2 个 Run 共 6 个 quarantine 对象
- 敏感信息：未提交凭据、资产绝对路径或运行输出
- hard_isolation：`false`
- 网络隔离边界：合同禁止网络，当前为本地白名单工程执行器，不宣称生产级硬隔离

## 9. 修改摘要

- 新增运营方显式派发 API 和执行详情投影。
- 修复 Job 预占槽位与 Run 双重计数。
- 固定输出类型绑定到冻结请求与 registry。
- 三文件输出写入真实 MinIO quarantine。
- Artifact 保持 quarantined，无结果包和下载。

未实现：

- Artifact 多方审核
- Release Package
- ZIP
- 下载授权
- 任意模型、脚本、镜像或 entrypoint
- 真实医院数据和生产隔离
- Phase 5.7

## 10. 阻塞和风险

- 阻塞：无
- 已知限制：`hard_isolation=false`；数据库与对象存储不是分布式原子事务
- 人工确认：下一阶段必须单独授权

## 11. 下一阶段建议

- 建议阶段：Phase 5.7 Artifact 多方审核、安全结果包与一次性下载闭环
- 理由：当前结果已生成但严格保持 quarantined
- 不应提前开发：计费、任意执行、真实医院接入、生产隔离
