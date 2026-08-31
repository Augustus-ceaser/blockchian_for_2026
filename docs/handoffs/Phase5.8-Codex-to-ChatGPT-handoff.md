# CODEX → CHATGPT 阶段回传

## 1. 阶段

- 阶段名称：Phase 5.8 全链路路演编排、视觉统一与稳定性封板
- 完成状态：正式完成
- 基准日期：2026-07-25，星期六

## 2. Git

- Branch：`main`
- 实现 Commit：`3efb90ce4cb1decb5c22883a7a57a84145c2f53b`
- 冻结 Commit：由最终 annotated tag 指向本报告所在封板提交
- Tag：`v0.11-phase5.8-roadshow-seal`
- 工作区：封板后 clean
- 旧标签：`v0.3` 至 `v0.10` 未移动

## 3. 技术基线

- Alembic：`20260725_0031`
- Migration：Phase 5.8 无新增 migration
- 业务表：51
- 后端测试：155 passed
- skipped：2 个明确环境门控
- failed：0
- 前端测试：39 passed
- typecheck：通过
- build：通过，3703 modules
- OpenAPI：99 paths / 102 operations
- 重复 operation ID：0
- 无效审计链：0

## 4. 路演产品

- 路演入口：`/roadshow`
- 8 分钟模式：已实现并完成三轮准备验收
- 15 分钟模式：已实现并完成一轮完整验收
- 全局业务链：12 个真实状态节点
- 角色切换：四角色切换保留当前业务链上下文
- 当前讲解：可显示、隐藏和恢复
- 实时事件流：活动链每 5 秒读取，完成链停止轮询
- 组件链：Platform、Dispatcher、Coordinator、Fixed Executor、Scanner、Callback、Audit
- 审计关键视图：支持关键事件与全部技术事件
- 系统健康：真实显示 Frontend、Backend、PostgreSQL、MinIO、Dispatcher、Coordinator、Executor、Callback 和 Audit chain

## 5. 视觉和视口

- 390×844：通过，无页面级横向溢出
- 1366×768：通过
- 1920×1080：通过
- 100% 缩放：通过
- 125% 等效缩放：通过
- 页面级横向溢出：无
- 视觉统一：路演命令栏、12 节点链、证据区、讲解区和健康区已统一

## 6. 路演验收

- 8 分钟流程次数：3
- 15 分钟流程次数：1
- 备用对象切换：通过
- 实时主链：`APP-AD02DD51`，3/12，下一步为运营方生成数字合约
- 完成态备用链：`APP-BD5902BE`，12/12
- 完成态备用链：`APP-57F74162`，12/12
- 页面错误：0
- API 错误：0
- 手工 SQL：未用于业务状态准备或修改
- 假状态：未使用
- 新会话默认链：优先选择 active 实时主链

## 7. 最终安全终态

- ComputeJob：2 succeeded
- ComputeRun：2 succeeded
- Artifact：2
- quarantined Artifact：2
- ReleasePackage：2 available
- DownloadGrant：2 exhausted，均为 1/1
- 首次下载：2 次成功
- 二次拒绝：2 次并进入审计
- 原始 Artifact 下载：不存在
- 安全结果包：每个精确包含 3 个白名单文件
- 无效审计链：0

## 8. 文档

- 8 分钟脚本：`docs/ROADSHOW-8MIN-SCRIPT.md`
- 15 分钟脚本：`docs/ROADSHOW-15MIN-SCRIPT.md`
- 操作清单：`docs/ROADSHOW-OPERATOR-CHECKLIST.md`
- 故障手册：`docs/ROADSHOW-FAILOVER-GUIDE.md`
- 录屏镜头：`docs/ROADSHOW-RECORDING-SHOTLIST.md`
- 对象地图：`docs/ROADSHOW-OBJECT-MAP.md`
- 只读审计：`docs/Phase5.8-roadshow-experience-audit.md`
- 交付报告：`docs/Phase5.8-roadshow-seal-delivery.md`

## 9. 边界

- `hard_isolation=false`
- 非临床验证
- 非真实医院生产接入
- 非 CA 电子签名
- 非国家可信数据空间官方测评
- 未实现：任意模型、任意代码、任意输出、计费结算、生产级硬隔离、真实患者数据接入

## 10. 阻塞和人工确认

- 阻塞：无
- 已知限制：Executor 显示 `unknown`，因为当前没有独立持久心跳，未伪造健康状态
- 已知限制：数据库与 MinIO 不是分布式原子事务
- 已知限制：前端生产包仍有大 chunk 警告，不影响当前路演功能
- 需要人工确认：真实试点前的合规、部署、安全、医院接口和组织协议

## 11. 下一阶段建议

- 建议：停止继续扩展路演功能，进入 Phase 6 真实试点准备
- 理由：工程闭环和专家演示闭环已经完成，下一风险来自真实机构、合规和部署条件
- 不应继续开发：任意执行、原始数据下载、放宽一次性授权、伪造硬隔离、计费交易或临床宣称
