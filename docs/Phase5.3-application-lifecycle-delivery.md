# Phase 5.3 计算需求与数据-模型组合申请交付报告

交付日期：2026-07-24

## 1. 阶段结论

Phase 5.3 已完成以下垂直业务切片：

```text
需求企业选择已发布数据产品固定版本
-> 选择已发布模型产品固定版本
-> 服务端兼容性检查
-> 保存和编辑申请草稿
-> 提交不可变申请快照
-> 平台预审
-> 医院数据使用审核
-> 模型使用审核
-> Application approved
-> 等待现有数字合约阶段
```

本阶段没有创建 ComputeJob，没有扩展合同签署、三方就绪、执行、结果审核、下载、计费或生产隔离。

## 2. 复用与新增

复用的权威对象：

- `Application`
- `ApplicationSnapshot`
- `ApplicationItem`
- `ApplicationModelSelection`
- `ReviewTask` / `ReviewDecision`
- `DataProductVersion` / `DataProductPublication`
- `ModelVersion` / `ModelPublication`
- `AuditEvent` / Transactional Outbox

新增代码：

- `backend/app/modules/applications/lifecycle.py`
- `backend/app/api/routes/applications.py`
- `frontend/src/roadshow/ApplicationLifecyclePages.tsx`
- Phase 5.3 后端和前端测试

没有新增业务表。兼容性历史保存在 Application 现有参数和提交快照中，不建立重复事实源。

## 3. 五步申请向导

1. 选择已发布数据产品和具体版本。
2. 选择已发布固定白名单模型产品和具体版本。
3. 执行服务端兼容性检查，展示 PASS、WARNING、BLOCKER。
4. 填写需求名称、项目类型、用途、数据范围、运行次数、期限、环境、输出和审核要求。
5. 预览参与方、锁定版本、申请范围、兼容性结果和声明后提交。

PathMNIST 样例只填充当前表单，不自动保存、提交、批准或创建计算任务，也不硬编码数据库 ID。

## 4. 兼容性快照

规则版本：`phase5.3/compatibility-rules/v1`

快照至少固定：

- 数据产品和版本
- 模型产品和版本
- 数据、模型和请求元数据摘要
- 规则版本和检查时间
- PASS、WARNING、BLOCKER 明细
- 输入 digest
- 允许用途、输出、运行次数和期限判断
- Connector 和固定执行能力提示
- 演示产品非临床边界
- `hard_isolation=false` 提示

数据、模型或关键申请字段变化后，旧兼容性结果失效。提交时后端重新计算输入 digest，并拒绝过期结果、BLOCKER 或未确认的 WARNING。

兼容性检查是申请前规则校验，不代表临床有效性、安全认证或生产级隔离认证。

## 5. 状态与审核

提交后创建三项真实 ReviewTask：

1. `application_precheck`
2. `data_provider_review`
3. `model_provider_review`

审核按 sequence 顺序推进。每个角色只能处理本组织和本审核类型的任务，并可保存意见、范围、条件和证据摘要。

- 全部 required review 批准：Application 进入 `approved`
- 任一 required review 拒绝：Application 进入 `rejected`
- 退回补充：保留原申请和审核历史，并生成可编辑替代草稿

获批页面只显示下一步为数字合约，不显示或提供创建 ComputeJob 的动作。

## 6. API

统一前缀：`/api/v1`

- `GET /application-options`
- `POST /application-drafts`
- `PATCH /application-drafts/{application_id}`
- `POST /application-drafts/{application_id}/compatibility`
- `POST /application-drafts/{application_id}/submit`
- `GET /application-management`
- `GET /application-review-queue`
- `POST /application-review-tasks/{task_id}/decide`
- `GET /applications`
- `GET /applications/{application_id}`
- `GET /applications/{application_id}/audit-events`

所有写操作要求 `Idempotency-Key`，状态变化由后端领域服务完成。

## 7. Migration 0028

`20260724_0028` 完成：

- 增加 Application 创建、更新、兼容性检查、退回、拒绝和最终批准事件词汇
- 同步数据库 AuditEvent CHECK 和 `guard_audit_event_v8()`
- 将模型方审核顺序调整为第三项 required review
- 允许 `ApplicationModelSelection` 仅在 draft 状态受控修改
- draft 切换数据提供方时，将 ApplicationItem 组合外键设为事务内延迟校验

历史 migration 未修改，业务表仍为 48 张。

## 8. 权限与敏感边界

- 需求企业只能创建、查看和编辑本组织草稿
- 医院和模型方在申请提交前不能通过直接 URL 查看草稿
- 医院只能审核涉及本组织已发布数据产品的申请
- 模型方只能审核涉及本组织已发布模型产品的申请
- 运营方不能代替医院或模型方批准
- 只有 active Publication 对应的 approved 版本可选
- 不暴露原始数据、患者级信息、模型权重、Connector 凭据、Token、本地路径或完整敏感 payload

## 9. 审计与幂等

创建、更新、兼容性检查、提交、审核决定、退回、拒绝和最终批准均产生真实 AuditEvent/Outbox 证据。

对象级证据面板展示：

- Event ID
- Application ID
- Actor / Organization
- 状态前后值
- ReviewTask ID
- compatibility digest
- previous/current/evidence hash
- correlation ID
- Outbox 目标和状态

相同命令重放返回原结果；相同键不同 payload 返回冲突。重复提交和重复审核不会重复推进状态或写入事件。

## 10. 验证结果

- Python compileall：通过
- 后端全量回归：142 passed，5 skipped
- 前端测试：18 passed
- 前端 typecheck：通过
- 前端生产构建：通过，3699 modules
- OpenAPI：72 paths
- Alembic head/current：`20260724_0028`
- 空库完整迁移：通过
- `0027 -> 0028` 增量迁移：通过
- `0028 -> 0027 -> 0028` migration cycle：通过
- 业务表：48
- 演示数据库无效审计链：0
- Phase 5.3 浏览器申请对应 ComputeJob：0；演示数据库 ComputeJob 总数仍为 0
- UTF-8 和 `git diff --check`：通过

Pytest 仍有本机 `.pytest_cache` 写权限警告，不影响测试或产品行为。

## 11. 浏览器验收

流程 A：

- 名称：`Phase 5.3 浏览器申请 A`
- 编号：`APP-9F32FFBB`
- Application ID：`9f32ffbb-5951-5967-8242-b53499d4d546`
- 结果：approved

流程 B：

- 名称：`Phase 5.3 浏览器申请 B`
- 编号：`APP-A688F28D`
- Application ID：`a688f28d-531c-5ca3-9513-7b609c5f1cfc`
- 结果：approved

两条流程均通过真实 UI 完成数据/模型选择、兼容性检查、草稿保存、提交和三方审核，没有手工 SQL 修改状态。

两条流程的兼容性结果均为：

```text
12 PASS / 2 WARNING / 0 BLOCKER
```

详情页明确显示下一步进入数字合约，并且没有 ComputeJob 操作。证据抽屉已验证 ReviewTask、状态迁移、哈希链和 Outbox 状态。

390x844 窄屏复核通过：页面无文档级横向溢出，兼容性宽表在内容区内部滚动，重载后无新的控制台警告或错误。

## 12. 已知限制与回滚

- `hard_isolation=false`
- 单机工程演示，不是生产级隐私计算或消息基础设施
- 不接入真实医院或真实患者数据
- 不支持任意模型、代码、镜像或数据上传
- 申请 approved 不等于合同 active，也不等于执行就绪
- Phase 5.4 及后续能力必须单独授权

代码回滚使用 Phase 5.2 标签：

```text
v0.5-phase5.2-model-product-lifecycle
```

对已产生 Phase 5.3 AuditEvent 的数据库执行 downgrade 会被 migration 明确拒绝；数据库回滚前必须按受控流程处理现有证据。
