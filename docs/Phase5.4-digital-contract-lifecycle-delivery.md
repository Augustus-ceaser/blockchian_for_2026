# Phase 5.4 数字合约编排、四方确认与生效交付报告

交付日期：2026-07-24

## 1. 阶段结论

Phase 5.4 已完成以下垂直业务切片：

```text
approved Application
-> 生成唯一数字合约草稿
-> 冻结申请、审核、数据版本和模型版本证据
-> 最严格策略收敛
-> 形成稳定 ContractRevision 和 canonical digest
-> 需求方、医院、模型方、平台依次确认同一版本和摘要
-> 后端权威激活
-> waiting_for_data_and_model_readiness
```

本阶段没有实现数据或模型就绪确认、Connector 准备、ComputeJob、ComputeRun、执行、Artifact、结果审核、下载、真实 CA 签名、计费或生产级隔离。

## 2. 复用与新增

复用的权威对象：

- `Contract`
- `ContractRevision`
- `ContractParty`
- `ContractObject`
- `ContractSignature`
- `Policy` / `PolicyConstraint`
- `ContractModelObject`
- `Application` / `ApplicationSnapshot`
- `ReviewTask` / `ReviewDecision`
- `AuditEvent` / Transactional Outbox

新增代码：

- `backend/app/modules/contracts/lifecycle.py`
- `backend/app/api/routes/contracts.py`
- `frontend/src/roadshow/ContractLifecyclePages.tsx`
- Phase 5.4 后端、PostgreSQL 和前端测试

没有新增业务表。`contracts.application_id` 的既有唯一约束继续保证一个 Application 只有一个 Contract 聚合。

## 3. 合约生成与冻结

只有最终状态为 `approved` 的 Application 可以生成合约。重复生成请求返回既有合约，不创建并行草稿。

合约冻结：

- Application ID、提交快照 ID 和快照摘要
- 数据产品、数据固定版本和版本摘要
- 模型产品、模型固定版本和版本摘要
- 需求方、医院、模型方和平台组织
- 用途、动作、数据范围、运行次数和有效期
- 申请输出、永久禁止输出和结果审核要求
- 平台、医院和模型方审核事实
- 兼容性规则版本和摘要

后续发布新的数据或模型版本不会改变已生成的 ContractRevision。

## 4. 最严格策略收敛

规则版本：`phase5.4/structured-contract/v1`

收敛原则：

- 运行次数取所有有效限制的最小值
- 有效期取所有有效期限的最早值
- 允许输出取申请、数据方、模型方和审核条件的交集
- 禁止输出取所有禁止项与系统永久禁止项的并集
- 任一策略要求只读输入，最终即为只读
- 网络只有在全部相关规则允许时才允许
- 任一规则要求医院出域审核或模型技术确认，最终合同即保留该要求

永久禁止项包括原始图像、患者级预测、原始特征、模型权重、Connector 凭据、源代码和任意文件。

输出交集为空、运行次数无有效交集或有效期无有效交集时产生 BLOCKER，不能发起确认。

## 5. 版本与摘要

每个可确认版本使用稳定 canonical representation 生成摘要：

- 字段顺序稳定
- 不包含无意义时间漂移
- 相同内容产生相同 digest
- 关键内容变化产生不同 digest
- 不包含密码、Token、凭据或本地路径

四方确认必须绑定同一个 ContractRevision ID、版本号和完整 digest。两条浏览器验收合约均只有一个 distinct signed digest。

## 6. 状态与四方确认

复用既有状态机：

```text
draft -> proposed -> signed -> active
```

产品语义：

- `draft`：合约草稿已生成
- `proposed`：等待平台内四方确认
- `signed`：四个必需方已确认同一版本和摘要
- `active`：后端激活守卫通过

确认顺序：

1. 需求企业
2. 医院数据方
3. 模型提供方
4. 空间运营方

平台确认必须最后执行。平台不能代替其他三方；缺少任一确认、版本或 digest 不一致、Application 不再 approved、策略存在 BLOCKER 时均不能激活。

这里的“签署”是平台内结构化确认和审计记录，不等同于 CA 数字证书、可靠电子签名或线下法律意见。

## 7. 激活边界

激活后：

- ContractRevision 状态为 `active`
- 记录 `activated_at` 和最终 digest
- 记录 `contract.revision.activated` AuditEvent
- 页面显示“等待数据与模型就绪”

激活不会：

- 创建 ComputeJob 或 ComputeRun
- 消耗 run count
- 创建 Artifact
- 授予原始数据、模型或结果下载能力
- 自动确认数据、模型或平台 readiness

迁移 0029 只允许 `phase5.4/structured-contract/v1` 合约把执行绑定推迟到 Phase 5.5；其他历史合同继续执行原有 fail-closed 绑定守卫。

## 8. API

统一前缀：`/api/v1`

- `POST /applications/{application_id}/contract`
- `GET /digital-contracts`
- `GET /digital-contracts/{contract_id}`
- `POST /digital-contracts/{contract_id}/confirm`
- `POST /digital-contracts/{contract_id}/activate`
- `GET /digital-contracts/{contract_id}/audit-events`

所有写操作要求 `Idempotency-Key`。权限、当前责任方、版本、摘要、状态前置条件和激活条件均由后端验证。

## 9. Migration 0029

`20260724_0029` 完成：

- 增加 `contract.draft.generated`
- 增加 `contract.policy.converged`
- 同步 AuditEvent CHECK 和 `guard_audit_event_v8()`
- 为 Phase 5.4 结构化合同延迟执行绑定要求
- 保留历史合同的原有执行绑定守卫
- downgrade 在存在 Phase 5.4 审计事实时拒绝删除事件词汇

历史 migration 未修改，业务表仍为 48 张。

破坏性迁移循环使用独立的 `MEDTRUST_MIGRATION_CYCLE_DATABASE_URL`，避免与会提交测试数据的共享回归库互相污染。完整升降级后数据库恢复到 `20260724_0029`。

## 10. 审计、幂等与权限

新增或复用的关键事件：

- `contract.draft.generated`
- `contract.policy.converged`
- `contract.revision.proposed`
- `contract.revision.signed`
- `contract.revision.activated`

重复生成、确认和激活不会重复创建业务事实或 AuditEvent。需求方、医院、模型方和平台只能查看与本组织相关的合约，并只能以本方角色确认。

证据面板展示真实持久化字段，包括 Event ID、Contract/Revision/Application ID、actor、organization、party role、digest、状态变化、前后哈希和 Outbox 状态，不展示密钥、Token、数据库密码、原始数据、模型权重、Connector 凭据或本地路径。

## 11. 验证结果

- Python compileall：通过
- 后端权威回归：147 passed，2 skipped，0 failed
- 前端测试：22 passed
- 前端 typecheck：通过
- 前端生产构建：通过，3700 modules
- OpenAPI：78 paths，81 operations，无重复 operation ID
- Alembic head/current：`20260724_0029`
- 空库完整迁移：通过
- 独立数据库完整 migration cycle：通过并恢复到 0029
- 业务表：48
- 演示数据库 ComputeJob：0
- 演示数据库 active ContractRevision：2
- 演示数据库 verified ContractSignature：8
- 演示数据库无效审计链：0
- 严格 UTF-8 和 mojibake 扫描：通过
- 敏感信息与本地路径 diff 扫描：通过
- `git diff --check`：通过

Pytest 仍有本机 `.pytest_cache` 写权限警告，不影响测试结果或产品行为。前端构建仍有既有主包体积提示，不影响构建成功。

## 12. 浏览器验收

流程 A：

- Application ID：`9f32ffbb-5951-5967-8242-b53499d4d546`
- Contract ID：`a75b7d48-e4a3-5cd5-8703-67d418cff324`
- 合约编号：`CON-9F32FFBB`
- digest：`sha256:01037c88e4020b59cc06edf59dee066113531714e70d50040042003b38b9313c`
- 平台提前确认：后端拒绝，原因 `platform confirmation must be last`
- 最终状态：`active`

流程 B：

- Application ID：`a688f28d-531c-5ca3-9513-7b609c5f1cfc`
- Contract ID：`1f63f990-5a93-5069-ab38-bdb8eb819630`
- 合约编号：`CON-A688F28D`
- digest：`sha256:559e4f846ae12af215fe52b7c654bee7fd98d31e6c33dfc093ccaea65a7080ff`
- 最终状态：`active`

两条流程均通过真实 UI 完成，没有使用手工 SQL 修改状态。四方确认均绑定 `v1` 和同一 digest，激活后下一步均为等待数据与模型就绪，ComputeJob 总数仍为 0。

390x844 窄屏复核通过：页面无文档级横向溢出，策略矩阵在内容区内部滚动。

浏览器事件显示的 2026-07-25 来源于本机时钟漂移；授权任务和交付基准日期保持为 2026-07-24。

## 13. 已知限制与回滚

- `hard_isolation=false`
- 单机工程演示，不是生产级隐私计算或消息基础设施
- 平台内确认不是真实电子签名或 CA 集成
- 合约 active 不等于数据、模型或平台执行就绪
- 不支持任意模型、代码、镜像、数据或路径输入
- Phase 5.5 及后续能力必须单独授权

代码回滚使用 Phase 5.3 标签：

```text
v0.6-phase5.3-application-lifecycle
```

对已产生 Phase 5.4 AuditEvent 的数据库执行 downgrade 会被迁移明确拒绝；数据库回滚前必须按受控流程处理现有证据。
