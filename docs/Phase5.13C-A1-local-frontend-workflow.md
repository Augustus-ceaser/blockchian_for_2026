# Phase 5.13C-A1 本地前端工作流

## 页面

- `/local/login`：本地账号登录。
- `/local`：Connector、证书、Heartbeat、Capability 和禁用边界。
- `/local/assets`、`/local/assets/new`：资产列表和结构化创建。
- `/local/assets/:assetId`：版本、Bundle 和当前状态。
- `/local/assets/:assetId/versions/:versionId`：不可变 metadata 与审核状态。
- `/local/assets/:assetId/versions/:versionId/quality`：质量画像和禁止字段扫描。
- `/local/reviews`、`/local/reviews/:reviewId`：reviewer 队列和一次性决定。
- `/local/assets/:assetId/bundles/:bundleId`：批准摘要与 mTLS 同步。
- `/local/sync-history`、`/local/audit`：同步和本地审计历史。

## 角色边界

Curator 可创建、追加版本、填写质量摘要、提交审核、生成批准 Bundle 和同步。
Reviewer 只能读取待审证据并作一次决定。所有写路由由服务端 Session 角色守卫；
创建者/提交者自审返回 `403 LOCAL_ASSET_SELF_REVIEW_FORBIDDEN`。

## 正式链

正式验收资产 `A1-LOCAL-ASSET-FINAL` 及 v1/v2 均完全通过浏览器页面建立。
两版分别完成质量画像、提交、独立批准、Bundle 和 mTLS 同步。中央只保留
metadata mirror、两版历史和 Quality Snapshot；没有路径、患者标识、原始文件名、
原始数据、模型权重或执行许可。
