# CODEX → CHATGPT 阶段回传

## 1. 阶段

- 阶段名称：Phase 5.7 Artifact 多方审核、安全结果包与一次性下载
- 完成状态：正式完成
- 基准日期：2026-07-25，星期六

## 2. Git

- Branch：`main`
- 实现 Commit：`45f32b539c54734e6b03a36cba806728f07d421f`
- 冻结 Tag：`v0.10-phase5.7-controlled-result-release`
- 旧标签：`v0.3` 至 `v0.9` 未移动

## 3. 数据库

- Alembic：`20260725_0031`
- Migration：新增两个审计事件词汇，不新增业务表
- 业务表：51
- ComputeJob：2 succeeded
- ComputeRun：2 succeeded
- Artifact：2 quarantined
- 审核任务/决定：6 / 6
- 结果包：2 available
- 下载授权：2 exhausted，均为 1/1

## 4. 后端

- 测试：151 passed
- skipped：2 个明确环境门控
- failed：0
- Python 编译：通过
- OpenAPI：95 paths / 98 operations
- 权限：医院、模型方、平台和需求企业均按合同组织校验
- 并发：同一 token 只允许一个成功消费
- 原始 Artifact 下载 API：不存在

## 5. 前端

- 测试：32 passed
- typecheck：通过
- build：通过
- modules：3702
- 新增结果中心列表、详情、三方审核表单、package、下载和审计时间线
- 390×844：文档宽度 375，无横向溢出

## 6. 浏览器验收

- 两个 Artifact 均完成医院、模型方、平台 required review
- 两个独立 package 均生成成功
- 两个 package 均成功下载一次
- 两个 token 二次使用均被拒绝
- Artifact 始终为 quarantined
- `hard_isolation=false` 始终可见
- 未使用手工 SQL 修改业务状态

## 7. 结果包

- Package A：`3bbf064f-6283-4ffa-a5db-f90a4a51f0b4`
- Package B：`009844a4-0cbf-44ff-8014-45abf04cd24e`
- release bucket：2 个 ZIP
- 每个 ZIP 只包含：

```text
aggregate_metrics.json
confusion_matrix.csv
execution_summary.json
```

## 8. 审计与安全

- `artifact.review.plan.created`：2
- `artifact.multiparty_review.decided`：6
- `result.package.created`：2
- `result.download.grant.created`：2
- `result.download.completed`：2
- `result.download.rejected`：2
- 无效审计链：0
- token 明文未持久化，只保存 digest
- quarantine 仍为原 6 个对象

## 9. 修改摘要

- 新增通用结果发布 API 和四角色结果中心。
- 从 MinIO quarantine 读取并严格验证不可变 Callback manifest。
- 强制医院、模型方和平台三方审核，平台最后。
- 生成与 Artifact 分离的三文件安全 package。
- 实现组织/用户/package 绑定的一次性下载和拒绝审计。

未实现：

- Artifact 原始文件下载或 release
- 任意输出、任意模型、任意脚本或镜像
- 计费、结算、生产隔离、真实医院接入
- Phase 5.8

## 10. 阻塞和风险

- 阻塞：无
- 已知限制：`hard_isolation=false`
- 已知限制：数据库和 MinIO 不是分布式原子事务，失败写入可能产生不可发现孤立对象
- 继续开发必须单独授权

## 11. 下一阶段建议

- 先冻结并只读审计 Phase 5.7 基线
- 如进入 Phase 5.8，必须先明确具体产品目标
- 不应自动进入计费、任意执行、生产隔离、真实医院或临床范围
