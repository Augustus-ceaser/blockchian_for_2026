# Phase 5.1 医院数据产品全生命周期交付

日期：2026-07-24

## 1. 交付范围

本阶段只完成医院数据产品垂直切片：

```text
医院新建四步表单
-> 保存或更新草稿
-> 提交上架审核
-> 运营方退回或批准
-> 正式发布
-> 需求企业目录可见
-> AuditEvent、Outbox 和哈希链证据可查
```

未实现模型产品创建、计算需求创建、数字合约新功能、任意模型/数据上传或生产级硬隔离。

## 2. 页面与路由

- `/data-products`：医院产品管理或运营审核队列
- `/data-products/new`：四步数据产品表单
- `/data-products/:versionId/edit`：退回后草稿编辑
- `/data-products/:versionId`：详情、状态、审核和操作证据
- `/data-catalog`：仅展示存在有效 Publication 的已发布产品
- `/audit?subjectType=data_product_version&subjectId=...`：按产品版本过滤完整审计链

表单四步分别覆盖基本信息、数据构成与质量、使用策略与输出边界、Connector 绑定与提交确认。“填充公开演示样例”只填充前端表单，不写数据库。

## 3. 数据映射

没有为表单字段盲目增加数据库列：

- 基本和来源信息：`linkage_metadata`、`provenance_summary`
- 数据构成：`scope_metadata`
- 质量信息：`quality_report`
- 用途、运行和输出边界：`default_policy_template`
- 资源和 Connector：现有 `DataResource`、`DataProductSource`
- 目录发布：现有 `DataProductPublication`

中央平台不保存原始图像、患者信息、本地绝对路径、Connector 凭据、模型权重或任意执行脚本。

## 4. API

新增通用数据产品生命周期接口：

- `POST /api/v1/data-products`
- `PATCH /api/v1/data-product-versions/{version_id}`
- `POST /api/v1/data-product-versions/{version_id}/submit`
- `POST /api/v1/data-product-versions/{version_id}/return`
- `POST /api/v1/data-product-versions/{version_id}/approve`
- `GET /api/v1/data-product-management`
- `GET /api/v1/data-product-review-queue`
- `GET /api/v1/data-product-catalog`
- `GET /api/v1/data-product-connectors`
- 产品、版本详情和审计证据查询接口

创建、提交和批准重放返回相同权威对象或事件；写操作继续使用幂等键和 single-flight 防重复提交。

## 5. 状态机

继续复用现有 Catalog 规则：

```text
draft -> under_review -> approved
under_review -> draft
approved -> published Publication
```

前端不直接修改状态。运营审核队列来自 `DataProductVersion.status='under_review'`，没有误用依赖 ApplicationSnapshot 的 `ReviewTask`。

## 6. AuditEvent 与迁移

迁移 `20260724_0026` 只增加三个缺失事件类型：

- `data_product.version.created`
- `data_product.version.updated`
- `data_product.version.returned`

提交、批准和发布事件继续复用既有词汇。迁移同时更新数据库事件约束和事件形状守卫，历史迁移未修改，表数量保持 48。

详情页展示最近事件和技术证据抽屉；审计中心支持按当前产品版本过滤。技术证据仅展示真实持久化的事件 ID、对象 ID、主体、组织、状态变化、前后哈希、证据摘要、Correlation ID 和 Outbox 状态。

## 7. 权限

- 医院数据方：只能创建本组织产品、编辑本组织草稿、查看退回原因和提交审核
- 空间运营方：只能审核 `under_review` 产品，不能冒充医院修改内容
- 需求企业：只能查看存在有效 Publication 的已发布目录
- 模型提供方：不能创建或编辑数据产品

`X-Demo-Identity` 仍只是原型身份适配器，不是生产认证方案。

## 8. 验证结果

- Python compileall：通过
- 后端全量回归：138 passed，5 skipped
- Phase 5.1 PostgreSQL 集成测试：通过
- Alembic head/current：`20260724_0026`
- 空库完整迁移和迁移循环：通过
- `medtrust` 业务表：48
- OpenAPI：52 paths
- 演示数据库审计链：1/1 有效，0 无效
- 前端测试：8 passed
- 前端 typecheck：通过
- 前端生产构建：通过，3696 modules
- `git diff --check`：通过

## 9. 浏览器验收

流程 A：

- 产品：`Phase 5.1 浏览器验收数据产品 A`
- 编号：`DP-85C581C8`
- 版本 ID：`7991f233-73b1-5aa8-8765-7e48b980483b`
- 完成创建、提交、运营退回、医院修改、重新提交、批准和发布
- 审计中心按对象过滤显示 7 条事件，空间审计链有效

流程 B：

- 产品：`Phase 5.1 浏览器验收数据产品 B`
- 编号：`DP-98A682AA`
- 版本 ID：`f5ad1750-f10f-508a-9d52-5670402c5a7f`
- 完成创建、提交和直接批准发布
- 需求企业目录无需重置即可同时看到 A、B 两个产品

桌面页面和高缩放窄布局完成视觉检查；详情、目录和技术证据抽屉无内容重叠。浏览器控制接口未提供设备视口尺寸切换，因此未把高缩放检查表述为真实移动设备截图。

浏览器控制台在最终热更新后没有新的 Phase 5.1 表单、Drawer 或 Timeline 警告；既有 Phase 4 页面仍可能触发 Ant Design `List` 弃用提示。

## 10. 已知限制与回滚

- `hard_isolation=false`
- 不接入真实医院或患者数据
- 不支持任意文件、模型、脚本或路径输入
- “发起申请”仍属于后续阶段，本阶段目录按钮保持禁用
- 数据库内哈希链是内部篡改检测证据，不是第三方存证

代码回滚到 Phase 5.0 基线：

```powershell
git switch --detach v0.3-phase5.0-baseline
```

数据库回滚仅用于受控开发环境：

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic downgrade 20260723_0025
```

0026 downgrade 会移除新增事件词汇；存在这些事件时数据库会拒绝降级，必须保留审计事实或恢复完整备份，禁止手工删除审计记录。

Phase 5.1 冻结标签：`v0.4-phase5.1-data-product-lifecycle`。Phase 5.0 基线标签 `v0.3-phase5.0-baseline` 保持不变。
