# Phase 5.8 全链路路演编排与稳定性封板交付

日期：2026-07-25

## 交付结论

Phase 5.8 不新增业务表、不新增 migration、不修改 Phase 5.1 至 5.7 状态机。新增的是受组织权限约束的只读聚合和前端编排层：

```text
现有 Phase 5 事实与正式 API
-> roadshow-experience 只读聚合
-> /roadshow 会话、12 节点导航、角色责任、讲解和证据视图
```

## 后端

新增只读接口：

```text
GET /api/v1/roadshow-experience/chains
GET /api/v1/roadshow-experience/chains/{application_id}
GET /api/v1/roadshow-experience/chains/{application_id}/events
GET /api/v1/roadshow-experience/health
```

接口不返回 Token、对象存储键、桶名、凭据、本地路径或原始证据 payload。下一责任方由 Application、ReviewTask、Contract、readiness、Job/Run、Artifact review、Package 和 Grant 的真实状态推导。

## 前端

- `/roadshow` 明确入口。
- 8 分钟和 15 分钟模式。
- sessionStorage 会话，不持久化业务状态。
- 12 节点全局业务链。
- 实时主链与完成态备用案例选择。
- 下一责任方切换与详情页导航。
- 当前讲解面板。
- Artifact/Package/Grant 安全反差。
- 关键事件和全部技术事件视图。
- 系统健康与预检。
- 详情页间保持路演上下文。

## 脚本

- `scripts/prepare_roadshow.ps1`
- `scripts/status_roadshow.ps1`
- `scripts/prepare_phase57_test_baseline.ps1`

准备脚本只调用正式 preflight/start/reset 和只读 API。Phase 5.7 测试基线通过两次正式固定 PathMNIST 执行生成，不使用手工 SQL。

## 已验证结果

- 两条完成态链为 12/12。
- 一条实时主链为 3/12，可继续正式操作；下一责任为运营方生成数字合约。
- 完成态 Artifact 仍为 `quarantined`。
- Package 精确包含三个白名单文件。
- Grant 为 `exhausted`、1/1。
- 真实演示库为 51 张业务表、81 条审计事件，审计链有效。
- 390×844、1366×768、1920×1080 无页面级横向溢出。
- 等效 125% 浏览器缩放无页面级横向溢出。
- 后端严格回归：155 passed / 2 skipped。
- 前端：39 tests、typecheck 和 build 通过，Vite 转换 3703 modules。
- OpenAPI：99 paths / 102 operations，0 个重复 operation ID。
- 下一责任按钮在尚无 Contract 时正确回退到 Application 详情。
- 15 分钟模式、关键/全部事件、讲解隐藏/恢复和备用链切换通过浏览器验收。
- `prepare_roadshow.ps1` 连续三轮预演均返回 health=ok、同一实时主链和两条 12/12 备用链。
- 15 分钟完整模式完成一轮事件、组件、讲解和备用链验收。

## 稳定性修复

严格回归发现 Phase 5.7 验收依赖两个预建 Artifact，但原有通用 smoke 生成的合同参与方并不满足 Phase 5.7 四方结果审核。新增正式测试基线工具：

```text
Phase 5.5 API lifecycle test
-> 2 active four-party Contracts
-> 2 created ComputeJobs
-> controlled dispatch
-> Outbox / Inbox / Coordinator
-> fixed PathMNIST executor
-> Callback Worker / MinIO quarantine
-> 2 succeeded Runs / 2 quarantined Artifacts
```

同时修正 PathMNIST onboarding preflight 的旧四文件常量，使其与冻结的三文件白名单一致。整个准备过程不使用手工 SQL 写业务状态。

## 边界

- `hard_isolation=false`。
- 固定白名单本地执行器，不是任意模型或代码执行平台。
- 非临床验证、非真实医院生产接入。
- 平台确认不是 CA 电子签名。
- 哈希链是篡改检测线索，不是第三方存证证明。
