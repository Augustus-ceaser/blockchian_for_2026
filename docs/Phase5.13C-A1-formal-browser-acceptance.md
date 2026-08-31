# Phase 5.13C-A1 正式浏览器验收

## 结论

```text
Phase 5.13C engineering core complete = true
Phase 5.13C formal acceptance = true
Phase 5.13D started = false
Phase 5.13D ready = true
```

实现提交：`ddaf700 feat: complete connector local asset review workspace`

## 正式运行证据

- 本地迁移：`phase5.13C_0002`；中央迁移保持 `20260729_0051`。
- Curator/Reviewer：两个本地账号、独立随机 Session、HttpOnly、
  SameSite=Strict、服务端角色守卫、登出撤销。
- 正式资产：1；Versions：2；Quality Profiles：2；Submissions：2；
  Reviews：2；Metadata Bundles：2；成功同步：2。
- 中央 mirror：1；历史版本：2；Job/Run/Artifact：0/0/0。
- 创建、版本、字典摘要、质量画像、提交、审核、Bundle 和同步全部通过前端。
- 自审 UI 不提供决定入口；后端返回
  `403 LOCAL_ASSET_SELF_REVIEW_FORBIDDEN` 并写拒绝审计。
- 本地审计：66 条，链有效；其中包含四档独立会话验收登录事件。
- 中央审计函数返回有效；canonical 主链仍为 353 条。

## 安全与 canonical 保护

中央 Bundle 中原始数据、本地路径、患者标识、原始文件名和模型权重均为 0。
没有自动创建 DataProduct，没有增加 Job、Run、Artifact 或 canonical MinIO 对象。

accepted Phase 5.13C 报告中的 protected-state manifest 与验收后实查均为：
Applications 3、Contracts 3、Jobs 3、Runs 2、Artifacts 2、ReleasePackages 2、
DownloadGrants 2、DataProducts 7、ModelProducts 4、relations 7、evidences 8、
materialization plans 0、AuditEvent 353、MinIO objects 30、audit valid。

该规范化 manifest 的验收前/后 SHA-256 均为：

`9cbda914f47bc4df04ecb819ca08f0e8d77c7798414f1d343ae2a233cd742ff6`

## 浏览器

390×844、768×1024、1366×768、1920×1080 均通过。每档覆盖本地首页、
资产列表、资产详情、两版 Version、两版 Quality、两版 Bundle、审核详情、
同步历史、本地审计和中央镜像。最终：

- 页面横向溢出：0
- Console error：0
- failed request：0
- 外部请求：0
- 敏感信息暴露：0

## 回归

- Connector：4 passed。
- Backend：184 passed、66 skipped；skipped 为未配置的专用 PostgreSQL/
  受控执行环境，不记为通过。
- Frontend：75 passed；typecheck、production build 通过。
- Python compile、Compose config、PowerShell syntax、`git diff --check` 通过。
- Alembic current/head：`0051`；空库升级通过。
- `alembic check` 仍报告项目既有 ORM/schema drift，本轮未扩大、未伪称通过。
- UTF-8/乱码、secret/private-key 扫描通过。

## 边界

`hard_isolation=false`。本阶段仍是 loopback-only、Local Test CA、
metadata-only 工程验收，不是医院生产接入，不运行模型，不读取原始数据，
不产生 Artifact。未创建或移动 Tag，未推送 remote。
