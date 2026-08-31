# Phase 5.9 生命周期治理与四账号独立门户交付

日期：2026-07-25

## 交付结论

Phase 5.9 已完成数据产品与模型产品的对称生命周期治理，并把客户端演示身份切换替换为四个真实本地账号、服务端会话和独立门户权限。实施提交：

```text
e576e08c02c1ceb91e00f9ab9ba97a020575180a
```

本阶段没有开放局域网、公网或云部署，没有修改合同策略、`run_count`、受控执行、Artifact、结果包或下载授权语义。

## 生命周期治理

- 数据和模型产品均支持所有方提交 `unpublish`、`relist`、`archive` 请求。
- 请求状态与产品状态分离；pending 下架不会提前改变 publication。
- 下架、重新上架和归档均由空间运营方审核。
- 影响分析由服务端查询 Application、Contract、Job、Run、Artifact、Package、Grant、组织和审计链生成并固化摘要。
- 归档只能针对已下架产品，使用 `deleted_at` 逻辑归档，不物理删除历史引用。
- PathMNIST 主数据产品和固定 ResNet-18 主模型产品禁止归档。
- 内容变化的重新上架被拒绝，必须创建新版本。
- 历史 `published_at`、Application、Contract、执行和结果证据在下架/归档后仍可追溯。

## 四账号与会话

| 用户名 | 角色 | 门户职责 |
|---|---|---|
| `hospital.demo` | `data_provider` | 数据产品、数据审批、数据就绪和出域审核 |
| `model.demo` | `model_provider` | 模型产品、模型许可、模型就绪和技术确认 |
| `requester.demo` | `data_requester` | 产品选择、计算需求、合同确认、Job 和结果下载 |
| `operator.demo` | `space_operator` | 上架审核、生命周期审核、合约编排、执行和合规审核 |

- 密码使用 scrypt 哈希，数据库不保存明文。
- 会话 Cookie 为 HttpOnly、SameSite=Lax；数据库只保存随机会话值的 SHA-256 digest。
- 四个浏览器上下文的 Cookie 均不同，退出一个账号不影响其他账号。
- API 身份由服务端会话解析；正常模式不再接受客户端自报身份。
- 调试角色切换默认隐藏，不能作为后端权限来源。

## 数据库与 API

- Alembic：`20260725_0032`
- 业务表：54
- 新增表：本地凭据、服务端会话、通用产品生命周期请求
- OpenAPI：109 paths / 112 operations
- 重复 operation ID：0

核心接口：

```text
POST /api/v1/auth/login
GET  /api/v1/auth/me
POST /api/v1/auth/logout
GET  /api/v1/auth/status
POST /api/v1/data-products/{product_id}/lifecycle-requests
POST /api/v1/model-products/{product_id}/lifecycle-requests
GET  /api/v1/product-lifecycle-requests
GET  /api/v1/product-lifecycle-requests/{request_id}
POST /api/v1/product-lifecycle-requests/{request_id}/decision
POST /api/v1/product-lifecycle-requests/{request_id}/cancel
```

## 验收

- 后端：156 passed / 4 skipped / 0 failed。
- 4 个 skipped 为未设置外部 PathMNIST 资产的环境门控项；固定资产受控执行基线另行通过。
- 前端：43 tests passed。
- TypeScript、生产构建、Python 编译通过。
- 生产构建转换 3704 modules。
- 空库迁移、真实演示库增量迁移和 `0031 ↔ 0032` 往返通过。
- 四账号独立 HTTP Cookie、权限拒绝和退出隔离通过。
- 四账号真实浏览器登录、菜单、直接路由 403 和三个视口无页面级横向溢出通过。
- 额外数据产品和模型产品均完成发布、下架、重新上架、再次下架和逻辑归档。
- 无效审计链为 0。

## 真实演示库终态

```text
Alembic = 20260725_0032
Business tables = 54
Pending lifecycle requests = 0
Archived data products = 1
Archived model products = 1
Invalid audit chains = 0
```

当前演示库在此前正式 reset 后保留一条完整业务链，因此 Job、Run、Artifact、Package、Grant 各为 1；Phase 5.9 未修改这些对象的语义。

## 安全与边界

- `hard_isolation=false`
- `Executor=unknown` 仍表示没有独立持久心跳
- 不提交本地密码、Cookie、Token、密钥、`.env.local`、资产路径、浏览器 Profile 或运行日志
- 非临床验证、非生产级隐私计算、非真实医院接入
- Phase 5.10、局域网、公网和云部署未开始
