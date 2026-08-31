# Phase 5.2 模型产品全生命周期交付

日期：2026-07-24

## 1. 交付范围

本阶段只完成固定白名单模型产品垂直切片：

```text
模型提供方新建四步表单
-> 保存或更新草稿
-> 提交上架审核
-> 运营方退回或批准
-> 正式发布
-> 需求企业目录可见
-> AuditEvent、Outbox 和哈希链证据可查
```

未实现计算需求、数字合约新功能、执行任务新功能、结果审核新功能、任意模型/权重/镜像/脚本上传、真实医院数据接入或生产级硬隔离。

## 2. 复用设计

- 复用现有 `ModelProduct`、`ModelVersion`、`ModelPublication`
- 复用 Marketplace 的提交、批准和发布状态机
- 运营待办由 `ModelVersion.status='under_review'` 派生
- 复用 Phase 5.1 的四步表单、详情、审核、证据面板、技术抽屉和请求防重模式
- 不创建模型审核影子表，不允许前端直接修改状态

## 3. 固定资产与表单

浏览器只能选择平台返回的固定 PathMNIST registry 资产。后端重新加载 registry，并逐项校验：

- `entrypoint_id`
- model digest 和 registry digest
- runtime
- 输入/输出 Schema 版本
- CPU、内存和超时限制
- 网络禁止、输入只读、无动态依赖、无任意代码、资产 ready

四步表单覆盖基本信息、版本与运行、输入输出 Schema 与许可、执行能力确认。“填充 PathMNIST 固定模型样例”只填表，不自动保存、提交、发布或产生 AuditEvent。

## 4. API

新增通用模型产品生命周期接口：

- `GET /api/v1/model-assets`
- `POST /api/v1/model-products`
- `GET /api/v1/model-product-management`
- `GET /api/v1/model-product-review-queue`
- `GET /api/v1/model-product-catalog`
- `GET/PATCH /api/v1/model-product-versions/{version_id}`
- `POST /api/v1/model-product-versions/{version_id}/submit`
- `POST /api/v1/model-product-versions/{version_id}/return`
- `POST /api/v1/model-product-versions/{version_id}/approve`
- `GET /api/v1/model-product-versions/{version_id}/audit-events`

创建、提交和批准重放返回相同权威结果。后端校验角色、组织、状态、registry 和安全策略；前端按钮隐藏不是授权控制。

## 5. 状态机与 AuditEvent

状态继续使用现有 Marketplace 规则：

```text
draft -> under_review -> approved -> published
under_review -> draft
```

迁移 `20260724_0027` 增加：

- `model_product.version.created`
- `model_product.version.updated`
- `model_product.version.returned`

提交、批准和发布事件复用既有词汇。迁移还移除历史 `(entrypoint_id, model_digest, registry_digest)` 全局唯一约束，使多个目录/许可产品能够引用同一不可变白名单资产；registry 仍是技术权威，所有绑定仍由后端精确校验。表数量保持 48。

0027 downgrade 在存在重复 registry 引用时拒绝恢复唯一约束，避免破坏已登记事实。

## 6. 权限、幂等与安全

- 模型提供方只能创建、查看和编辑本组织草稿
- 空间运营方只能审核 `under_review` 版本，不能修改模型方内容
- 需求企业只能读取存在有效 Publication 的已发布目录
- 医院数据方不能创建或编辑模型产品
- 非法 digest、伪造 entrypoint、资源限制漂移和非白名单 runtime 被拒绝
- 模型下载、反编译、二次分发、动态脚本和未授权网络请求被拒绝
- UI/API 不接收或展示权重、本地路径、凭据、Token、原始输入或敏感 payload
- 创建、更新、提交、退回、批准和发布均使用稳定命令与审计幂等键

模型上架只代表进入目录，不代表需求企业已取得具体项目使用许可。

## 7. 验证结果

- Python compileall：通过
- 后端全量回归：139 passed，5 skipped
- Phase 5.2 PostgreSQL 集成测试：通过
- Alembic head/current：`20260724_0027`
- 空库完整迁移：通过
- 完整迁移往返：1 passed
- 现有 Phase 4 演示库增量迁移 `0026 -> 0027`：通过
- `medtrust` 业务表：48
- 历史 registry 全局唯一约束：不存在
- OpenAPI：62 paths
- 测试库 159 条 AuditEvent：0 条无效空间链
- 演示数据库审计链：0 条无效空间链
- 前端测试：11 passed
- 前端 typecheck：通过
- 前端生产构建：通过，3698 modules
- UTF-8、敏感路径/秘密扫描和 `git diff --check`：通过

Pytest 仍有本机 `.pytest_cache` 写权限警告，不影响测试或产品行为。

## 8. 浏览器验收

流程 A：

- 产品：`Phase 5.2 浏览器验收模型 A`
- 编号：`MP-221B1C9A`
- 版本 ID：`8cf4030e-094f-5b0e-8ee7-395a87642134`
- 完成创建、更新、提交、运营退回、版本说明修订、刷新持久化、重新提交、批准和发布
- 详情显示真实创建、更新、提交、退回、批准和发布事件，审计链有效

流程 B：

- 产品：`Phase 5.2 浏览器验收模型 B`
- 编号：`MP-4CD527A0`
- 版本 ID：`973cfacc-5d09-52c3-9289-8114c36887a2`
- 完成创建、提交和直接批准发布
- 审计中心按 `model_version` 精确过滤 4 条事件并验证链有效

需求企业目录无需重置即可看到两个已发布模型。目录卡片不包含 digest、entrypoint、权重、路径或凭据；技术证据抽屉只展示真实事件、状态、哈希、Correlation ID 和 Outbox 事实。

## 9. Phase 5.1 回归

- 医院数据产品管理仍显示 Phase 5.1 已发布产品 A/B
- 数据产品详情、使用策略、Connector 和审计证据正常
- 需求企业数据目录仍只显示已发布产品
- Phase 5.1 PostgreSQL 集成测试包含在 139 项通过结果中

## 10. 已知限制与回滚

- `hard_isolation=false`
- 单机工程演示，不是生产消息或生产认证基础设施
- 不接入真实医院、患者数据或临床流程
- 不支持任意模型上传、训练、容器上传、脚本执行或权重下载
- 数据库哈希链是内部篡改检测证据，不是第三方可信存证

代码回滚到 Phase 5.1：

```powershell
git switch --detach v0.4-phase5.1-data-product-lifecycle
```

数据库回滚仅用于受控开发环境：

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic downgrade 20260724_0026
```

存在新增模型审计事件或重复 registry 引用时，0027 downgrade 会拒绝执行。不得删除审计事实来强行降级，应恢复完整备份或保留 0027。

Phase 5.2 冻结标签：`v0.5-phase5.2-model-product-lifecycle`。Phase 5.0 和 Phase 5.1 标签保持不变。
