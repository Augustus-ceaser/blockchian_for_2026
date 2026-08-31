# Phase 5.13C-A1 Connector 本地角色与 Session 设计

## 边界

这是 Hospital Connector Alpha 的轻量本地身份层，不复用中央演示账号，
不声称医院 IAM、生产 SSO 或临床生产安全。`hard_isolation=false` 保持不变。

## 数据对象

`phase5.13C_0002` 增加：

- `local_users`：用户名、显示名、PBKDF2 密码哈希、固定角色、状态、失败次数、
  临时锁定和登录时间。
- `local_sessions`：用户外键、随机 Session 的 SHA-256 摘要、签发/过期/撤销/
  最后访问时间和 User-Agent 摘要。

数据库和日志均不保存明文密码或明文 Session。测试账号只从未跟踪环境配置
引导创建。Cookie 使用 `HttpOnly`、`SameSite=Strict`，是否设置 `Secure`
由 Connector 的本地 HTTPS 配置决定。

## 角色

- `local_asset_curator`：建立草稿、版本、字典摘要和质量画像，提交审核，
  对已批准版本生成 Bundle 并同步。
- `local_asset_reviewer`：只读检查待审版本和质量证据，作出一次性审核决定。

角色由服务端 Session 解析，不接受表单或前端状态传入角色。所有写路由同时
检查角色和对象状态。

## 强制规则

- 创建者或提交者不能审核自己的版本。
- 自审返回 `403 LOCAL_ASSET_SELF_REVIEW_FORBIDDEN` 并写入拒绝审计。
- 已提交版本不可修改；已批准版本、质量画像和审核决定不可修改。
- Bundle 只能从已批准版本生成，且只能由 curator 生成和同步。
- reviewer 不能创建版本、Bundle 或同步；curator 不能作审核决定。
- 登出立即撤销服务端 Session；过期或撤销 Cookie 不再授权。

## 凭据处理

本地账号密码通过未跟踪配置注入，启动时只在缺少用户时生成密码哈希。
验收文档、截图、日志和 Git 不记录密码、Cookie 或 Session Token。
