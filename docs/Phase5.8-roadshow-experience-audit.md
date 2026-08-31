# Phase 5.8 全链路路演体验只读审查

审查日期：2026-07-25

## 1. 结论

Phase 5.1 至 Phase 5.7 的权威业务能力已经完整，但当前“路演入口”仍主要依赖早期 Phase 4 固定对象投影，与两条已完成的 Phase 5 真实业务链不一致。

本阶段不需要新增业务表，不需要 migration，不需要修改 Phase 5.1 至 Phase 5.7 状态机。需要的最小改造是：

```text
现有通用 Phase 5 API 和数据库事实
-> 一个受组织权限约束的只读业务链聚合 API
-> /roadshow 编排入口和 sessionStorage 演示上下文
-> 复用现有详情页、矩阵、审核、执行、结果和审计组件
```

不得继续把旧 `/api/v1/roadshow/overview`、`/api/v1/roadshow/workflow`、`/api/v1/overview` 或 `/api/v1/audit-events` 当作 Phase 5.8 主链事实源。

## 2. 审查基线

- Branch：`main`
- HEAD：`6490d9074c2036de512c3af6eb4cc4cbc19581eb`
- Phase 5.7 实现：`45f32b539c54734e6b03a36cba806728f07d421f`
- Tag：`v0.10-phase5.7-controlled-result-release`
- Alembic：`20260725_0031`
- 业务表：51
- OpenAPI：95 paths / 98 operations，无重复 operation ID
- 前端：32 tests、typecheck、build 通过，3702 modules
- preflight：0 warning
- 应用启动：保留现有数据库状态启动成功
- PostgreSQL、MinIO：健康

后端审计回归发现一个测试稳定性缺陷：Phase 5.7 集成测试隐式要求测试库预先存在两个 quarantined Artifact，而 Phase 5.6 集成测试只到 dispatch，不会生成 Callback 或 Artifact。该问题属于测试基线不可重复，不是产品状态回归。

## 3. 现有完整流程操作路径

当前通用页面路径：

```text
/data-products
-> /data-products/:versionId
-> /model-products
-> /model-products/:versionId
-> /applications
-> /applications/:applicationId
-> /contracts
-> /contracts/:contractId
-> /execution
-> /execution/:contractId
-> /results
-> /results/:artifactId
-> /audit
```

当前写操作均由 Phase 5.1 至 Phase 5.7 正式 API 完成，前端没有通用状态 PATCH。主要命令包括：

- 数据产品保存、提交、退回、批准发布；
- 模型产品保存、提交、退回、批准发布；
- 申请兼容性检查、提交和三方审核；
- 合约生成、四方确认和激活；
- 数据 ready、模型 ready、平台资格检查、Job 创建和运营派发；
- Artifact 三方审核、结果包生成、一次性授权和下载消费。

## 4. 当前点击次数

早期 Phase 4 文档定义了 29 个业务动作。按当前页面和角色切换方式完成一次新链路，还需要：

- 约 15 次责任角色切换；
- 每次角色切换通常需要打开 Select 并选择角色，共约 30 次界面交互；
- 至少 10 至 15 次列表、详情和返回导航；
- 表单填写、确认 Checkbox、查看矩阵和打开证据抽屉尚未计入。

因此当前完整真实流程的最低界面交互约为：

```text
29 个业务动作
+ 30 个角色切换交互
+ 10~15 个页面导航
= 69~74 次基础交互
```

若现场完整填写数据、模型和申请表单，实际交互会超过 100 次。该数量不适合 8 分钟主路演。

## 5. 8 分钟内无法完整展示的原因

1. 旧路演层与 Phase 5.7 数据脱节，会把已完成链路显示为“合同未生成”。
2. 角色切换后固定跳回 `/overview`，丢失当前业务对象。
3. 没有跨页面持续存在的主链对象编号和阶段导航。
4. 29 个业务动作之外还有大量角色、列表和详情导航。
5. 真实推理、Worker 调度和 Callback 时间不可完全预测。
6. 执行事件分散在 Job、Run、Callback、Artifact 和 Audit 页面。
7. 审计中心不能按一条 Application/Contract 链一次性聚合全部对象。
8. 已完成对象没有“按真实时间回放关键事件”的演示视图。
9. 操作成功反馈通常只有短 Toast，缺少对象编号、下一角色和证据事件。

## 6. 页面视觉不一致

- 早期 Phase 4 页面使用大型 Hero、Metric Card 和三列路线图。
- Phase 5.1 至 5.7 页面使用 `phase51-heading`、详情 70/30 网格、Ant Table、Descriptions 和 Drawer。
- 不同阶段重复定义状态颜色和中文标签，同一状态可能显示不同颜色。
- 详情页顶部字段、主操作、对象编号、所属组织和下一步位置不统一。
- 执行、Artifact 和下载页的核心反差未形成统一视觉结构。
- 部分表格以宽表为主，部分使用 Card/List，扫描路径不稳定。
- 业务编号、UUID 和 digest 的截断、复制和等宽字体规则不统一。

## 7. 角色切换问题

当前身份保存在：

```text
localStorage: medtrust.phase4.identity
```

问题：

- 不符合 Phase 5.8 使用 sessionStorage 保存非敏感演示会话的要求；
- 切换身份固定导航到 `/overview`；
- 不保留 Application、Contract、Artifact 等当前链路 ID；
- 不根据后端真实状态推导下一责任方；
- 切换时页面级错误由组件卸载清理，但没有统一会话级清理语义；
- 当前顶部只显示角色名称，没有待办数、主任务和下一动作。

后端仍会校验 `X-Demo-Identity`、用户、组织、SpaceParticipant 和角色，因此前端切换本身不是授权缺陷。

## 8. 缺少的流程上下文

所有路演页面缺少持续显示的 12 节点主链：

```text
数据产品
-> 模型产品
-> 计算申请
-> 数字合约
-> 执行准备
-> 计算任务
-> 受控运行
-> 隔离结果
-> 多方审核
-> 安全结果包
-> 一次性下载
-> 审计完成
```

当前页面不能稳定回答：

- 当前演示的是哪一条 Application/Contract 链；
- 当前节点由哪个对象和状态证明；
- 下一责任角色是谁；
- 点击节点后应进入哪个详情页；
- 当前对象是实时演示还是备用完成案例。

## 9. 后台事件展示不足

可复用事实已经存在：

- AuditEvent；
- Outbox；
- ComputeRun 时间戳；
- Callback Inbox；
- Artifact；
- review/package/grant/download 事件。

但当前旧路演 `/workflow` 只返回最近 25 条：

```text
sequence / type / result / occurred_at
```

缺少 actor、organization、subject、对象编号、组件、状态变化、证据摘要、哈希和 Outbox 状态。执行详情虽然显示 Callback 和 AuditEvent，但没有形成：

```text
Platform -> Dispatcher -> Coordinator -> Executor
-> Scanner -> Artifact -> Callback -> Audit
```

的可读组件链。

## 10. 可能误导专家的表述

必须修复或避免：

- 旧路演显示“合同未生成”，而真实 Phase 5 合同已 active；
- 旧路线图 `phaseDone('download')` 永远为 false；
- 旧路演使用固定 Phase 4 对象，容易被理解为当前真实链；
- “已发布”不能用于源 Artifact；Artifact 必须继续显示 quarantined；
- “签署”必须同时显示平台内结构化确认，不是 CA 电子签名；
- 0.95 Accuracy 只能描述固定 20 图工程验证，不能称临床准确率；
- 哈希链只能称篡改检测线索，不能称第三方不可篡改存证；
- `hard_isolation=false` 必须在路演顶部和执行证据中持续可见。

## 11. 当前移动端问题

现有 Phase 5.3 至 5.7 已分别修复 390×844 页面级横向溢出，但新增全局主链可能重新引入宽度问题。

需要重点验证：

- 主链只在自身容器横向滚动，不能扩大 document；
- 右侧讲解/证据面板在窄屏改为 Drawer 或折叠区；
- 策略、兼容性和资格矩阵局部滚动；
- 顶部角色和模式控件换行后不遮挡内容；
- Toast 不覆盖主操作和状态；
- 1366×768 下固定顶部不能占用过多垂直空间；
- 125% 缩放下按钮文字、状态和对象编号不溢出。

## 12. 当前路演故障风险

| 风险 | 影响 |
|---|---|
| 旧 Roadshow 固定对象与当前 Phase 5 对象不同 | 展示错误状态和错误下一步 |
| `/api/v1/overview`、`/api/v1/audit-events` 依赖旧 demo baseline | 当前返回 503 |
| Phase 5.7 测试依赖预建 Artifact | 空库完整回归不可重复 |
| 无正式 prepare/status 脚本 | 演示前无法快速判断是否需要 reset |
| 角色切换回 overview | 现场迷失、重复导航 |
| 下载授权已 exhausted | 不能再次演示首次下载 |
| 推理和 Worker 时间波动 | 8 分钟流程可能超时 |
| 当前无备用对象标识 | 中断后难以在 30 秒内切换 |
| 事件轮询策略未统一 | 可能无限轮询或请求风暴 |
| API 原始英文异常可能直接显示 | 专家看到内部错误文本 |

## 13. 可复用组件

前端可直接复用或窄化提取：

- `RoadshowProvider` 的后端身份上下文；
- `startAbortableLoad`；
- `createSingleFlight`；
- Phase 5.1/5.2 四步表单；
- Phase 5.3 兼容性矩阵；
- Phase 5.4 策略收敛矩阵；
- Phase 5.5 资格矩阵、Job/Run/Callback 详情；
- Phase 5.7 Artifact、三方审核、Package、Grant 和审计时间线；
- `phase51-detail-grid` 的 70/30 详情布局；
- Ant Design Steps、Timeline、Descriptions、Drawer、Tag、Alert、Skeleton/Spin。

后端可复用：

- 各阶段已有组织权限判断；
- Application、Contract、Execution、Result 的现有 projection 查询；
- 对象级 AuditEvent 查询；
- Audit 哈希链验证函数；
- health ready；
- 现有 start/stop/reset/preflight 脚本。

## 14. 不需要修改后端的改造项

- `/roadshow` 路由与模式选择；
- sessionStorage 路演会话；
- 全局主链导航组件；
- 当前讲解面板；
- 角色切换保留当前链路；
- 8/15 分钟脚本和视图偏好；
- 统一状态标签、ID、digest 和复制样式；
- 执行成功与 Artifact quarantined 视觉对比；
- Package available 与 Grant exhausted 视觉对比；
- 页面成功反馈文案；
- 文档、操作清单、故障手册和录屏镜头；
- `prepare_roadshow.ps1` 与 `status_roadshow.ps1` 的基础设施检查部分。

## 15. 需要最小只读 API 的改造项

建议新增只读前缀：

```text
GET /api/v1/roadshow-experience/chains
GET /api/v1/roadshow-experience/chains/{application_id}
GET /api/v1/roadshow-experience/chains/{application_id}/events
GET /api/v1/roadshow-experience/health
```

职责：

- 聚合现有对象 ID、编号、状态和关联；
- 返回当前责任角色和下一动作导航建议；
- 返回按当前业务链过滤的关键/完整真实事件；
- 返回真实组件链状态；
- 返回只读系统健康和预检结果；
- 按当前组织和角色验证可见性；
- 不返回 Token、MinIO key、本地路径、凭据或完整敏感 payload；
- 不执行任何业务写入。

## 16. Migration 结论

**Phase 5.8 不需要 migration。**

理由：

- Roadshow Session 只保存前端展示上下文；
- 所有业务状态已经存在；
- 当前责任方可从现有状态、allowed actions、ReviewTask 和组织责任推导；
- 业务链事件可以通过现有对象关联查询 AuditEvent；
- 系统健康属于实时只读检查，不是业务事实；
- 不应为了路演偏好建立数据库事实源。

## 17. 实施冻结

允许：

- 新增只读聚合 API；
- 新增前端编排层和共享展示组件；
- 增加测试、脚本和文档；
- 修复 Phase 5.7 测试基线不可重复；
- 改善错误映射、加载、轮询停止和响应式。

禁止：

- 新业务表或 migration；
- 修改核心状态机、策略收敛、run_count、Artifact 隔离、结果白名单或下载授权；
- 平行假后端；
- 手工 SQL 准备正式路演状态；
- 前端定时器伪造事件；
- 自动把对象推进到成功；
- 保存 Token、下载授权或敏感值到浏览器持久存储。
