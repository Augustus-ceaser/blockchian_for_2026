# MedTrust Space 15 分钟完整演示脚本

## 节奏

| 时间 | 内容 | 页面与动作 |
|---|---|---|
| 0:00-1:20 | 四账号与产品供给 | 四个 Profile 分别登录；运营端 `/roadshow` 选择 15 分钟模式 |
| 1:20-3:20 | 组合申请与兼容性 | Application 详情；解释 PASS/WARNING/BLOCKER、用途、次数和输出白名单 |
| 3:20-5:00 | 三方申请审核 | 依次说明平台预审、医院数据使用审批、模型使用审批 |
| 5:00-7:00 | 数字合约 | 查看策略来源矩阵、run_count 最小值、有效期最早值、输出交集和四方确认 |
| 7:00-8:30 | 执行资格 | 展示数据 ready、模型 ready、平台资格矩阵与 Eligibility Snapshot |
| 8:30-10:30 | 受控执行 | 展示 Dispatcher、Coordinator、固定 Executor、Callback 和真实时间线 |
| 10:30-12:00 | Artifact 隔离 | 强调 Run succeeded 与 Artifact quarantined 的分离 |
| 12:00-13:30 | 三方结果审核 | 展示医院、模型方、平台的不同责任和平台最后审核约束 |
| 13:30-14:30 | 安全结果包与下载 | 展示精确三文件、一次下载成功和二次重用拒绝 |
| 14:30-15:00 | 审计和边界 | 切换“关键事件/全部技术事件”，确认 Audit chain valid 与 `hard_isolation=false` |

可扩展展示：医院或模型端提交下架申请，运营端打开 `/lifecycle` 查看服务端影响分析；不要在主演示产品上执行归档。

## 必须展示的证据

- 数据和模型固定版本及短摘要。
- Application 编号、approved 状态和三方审核事实。
- Contract 编号、active 状态、4/4 确认。
- readiness 3/3 和 Eligibility Snapshot。
- Job/Run succeeded、固定 20 图工程验证。
- Artifact `quarantined`。
- 结果审核 3/3 approved。
- Package `available` 且文件精确为：

```text
aggregate_metrics.json
confusion_matrix.csv
execution_summary.json
```

- Grant `exhausted`、1/1。
- `result.download.completed` 与 `result.download.rejected`。
- 无效审计链为 0。

## 备用切换

实时主链任一步超过 30 秒无进展时：

1. 返回 `/roadshow`。
2. 选择标记为“完成态备用案例”的 Application。
3. 明确说明已切换到正式流程生成的备用对象。
4. 从同一节点继续讲解，不冒充实时对象。
