# Phase 5.13C-A1 Connector 本地工作台修改前差距审计

## 审计基线

- 审计时间：2026-07-29
- 起始提交：`811615f1e168bfd4140c5fdb589f7d3122a2af47`
- Connector 本地迁移：`phase5.13C_0001`
- 中央迁移：`20260729_0051`
- 本文记录代码修改前状态，不回写为完成状态。

## 当前入口与身份机制

Connector 本地应用由 `hospital-connector/app/main.py` 提供服务端 HTML。
现有路由只有 `/local`、`/local/assets`、`/local/audit` 以及注册、轮询、
心跳、证书轮换、fixture 建立和 metadata 同步表单。应用没有本地用户、
密码哈希、Session、Cookie、角色守卫或服务端对象级授权。

`/local/seed-public-fixture` 一次性直接创建两版资产、质量画像、审核记录
和 Bundle，并硬编码 `local.curator`、`local.reviewer`。该入口适合早期工程
冒烟，不满足正式人工业务链，不能用于 A1 正式验收资产。

## 人工步骤差距

| 人工步骤 | 修改前页面/路由 | 修改前角色 | 修改前操作 | API-only 或捷径 | 需要补齐 | 缺口类型 |
|---|---|---|---|---|---|---|
| 本地登录 | 无 | 无 | 无 | 是 | 是 | 身份与 UI |
| 独立登出/Session 撤销 | 无 | 无 | 无 | 是 | 是 | 身份与 UI |
| 本地首页 | `/local` | 匿名 | 注册、轮询、心跳、fixture、同步 | 否 | 是 | 权限与 UI |
| Asset 列表 | `/local/assets` | 匿名 | 只读表格 | 否 | 是 | 权限与 UI |
| 创建 Asset | 无 | 无 | 无 | fixture 捷径 | 是 | 业务入口与 UI |
| 创建 Version | 无 | 无 | 无 | fixture 捷径 | 是 | 业务入口与 UI |
| Data Dictionary 摘要 | 无 | 无 | 无 | fixture 硬编码 | 是 | 业务入口与 UI |
| Quality Profile | 无 | 无 | 无 | fixture 硬编码 | 是 | 业务入口与 UI |
| 禁止字段扫描结果 | 无 | 无 | 无 | fixture 硬编码 | 是 | 安全展示与 UI |
| 提交审核 | 无 | 无 | 无 | fixture 直接批准 | 是 | 业务状态与 UI |
| 审核队列 | 无 | 无 | 无 | 是 | 是 | 业务入口与 UI |
| 审核详情/决定 | 无 | 无 | 无 | fixture 硬编码 | 是 | 业务状态与 UI |
| 自审阻断 | 无 | 无 | 无 | 未实现 | 是 | 领域规则与 UI |
| Bundle 生成 | 无 | 无 | 无 | fixture 自动生成 | 是 | 业务入口与 UI |
| Bundle 详情 | 无 | 无 | 无 | 是 | 是 | UI |
| 触发 metadata 同步 | `/local/sync-metadata` | 匿名 | 同步全部待同步 Bundle | 否 | 是 | 权限与对象操作 |
| Sync History | 无 | 无 | 无 | 是 | 是 | UI |
| Local Audit | `/local/audit` | 匿名 | JSON | 否 | 是 | 权限与可读 UI |
| 中央 Asset 列表 | 中央 `/connectors` 页面内表格 | 中央身份 | metadata mirror 只读 | 否 | 是 | 详情与历史 UI |
| 中央版本历史 | 后端已有镜像数据，页面未完整呈现 | 中央身份 | 无完整历史视图 | API-only | 是 | UI |

## 修改前安全结论

- 普通本地页面没有认证，任何能访问 loopback 端口的进程都能触发写操作。
- 没有服务端角色判断，前端也无法证明 curator/reviewer 独立性。
- 没有创建者与审核者约束，`LOCAL_ASSET_SELF_REVIEW_FORBIDDEN` 不存在。
- fixture 生成流程不能作为正式浏览器验收证据。
- 位置引用未进入中央 Bundle，这是既有正确边界，后续必须保持。
- 中央镜像与 DataProduct、计算和 Artifact 表隔离，这是既有正确边界，后续必须保持。

## 结论

Phase 5.13C 的 metadata-only registry 工程核心成立，但 A1 修改前
`formal_frontend_workflow_complete=false`。需要新增轻量本地认证和
`phase5.13C_0002`，并补齐受角色保护的逐步页面与服务端领域校验。
不需要修改中央业务状态机，也不应创建新的中央迁移。
