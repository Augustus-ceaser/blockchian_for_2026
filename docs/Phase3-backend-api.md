# Phase 3 — 真实后端查询与受控演示命令

日期：2026-07-23

## 结论

Phase 3 的前端不再需要从 Mock 推导真实状态。后端提供只读业务查询，以及一个固定、显式开启、具备幂等保护的 PathMNIST 演示命令。该接口层是工程原型能力，不代表生产级身份认证、临床系统或数据下载服务。

## 查询接口

统一前缀：`/api/v1`

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/overview` | 工作台聚合状态、已验证基线指标、Outbox/Inbox汇总与审计链校验结果 |
| GET | `/data-products` | 数据产品目录 |
| GET | `/data-products/{id}` | 数据产品版本、资源和策略摘要 |
| GET | `/applications` | 使用申请 |
| GET | `/contracts` | 合同系列与当前Revision |
| GET | `/contracts/{id}` | 合同对象、参与方、Policy和签署摘要 |
| GET | `/compute-jobs` | 可信计算任务 |
| GET | `/compute-runs/{id}` | 单次执行状态 |
| GET | `/artifacts/{id}` | 隔离制品与终态审核证据 |
| GET | `/audit-events` | 追加式审计事件 |
| GET | `/connectors` | 连接器及能力状态 |

所有响应均包含能力边界：`demo=true`、`hard_isolation=false`、`clinical_use=false`、`artifact_download_enabled=false`。Artifact接口不返回对象存储引用、宿主机路径、凭据、令牌或下载地址。

## 受控演示命令

`POST /api/v1/demo/pathmnist/runs`

该命令只允许固定场景 `pathmnist_resnet18_20`，不能接受任意算法路径、Shell命令、模型代码、数据路径或输出范围。启用条件：

- `MEDTRUST_DEMO_API_ENABLED=true`；
- 请求头 `X-Demo-Role: ai_company`；
- 请求头 `Idempotency-Key`，长度 8—128；
- 已准备 `CTR-PATHMNIST-DEMO-V1` Active Contract；
- Connector在线且所需能力保持 `verified`。

一次调用在同一事务中形成 ComputeJob、ComputeRun原子次数预留、AuditEvent和OutboxMessage。Job与Run使用不同但可复算的command ID；相同幂等键重试返回同一业务结果，不重复消耗次数。

本接口只预留执行，不直接运行模型。真实执行仍由Dispatcher、Coordinator、固定白名单Local Executor和Callback链路完成。

## 演示基线准备

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.tools.prepare_pathmnist_demo_baseline `
  --database-url "postgresql+asyncpg://medtrust:***@127.0.0.1:5432/medtrust_demo"
```

准备过程不会修改冻结的权威冒烟运行；它从已验证基线派生独立的可重复演示产品、申请、审核和合同。暂态Connector心跳会在演示基线准备时刷新，但不会把离线、未验证Connector或能力强行提升为可用。

## 验证

- OpenAPI包含全部查询与演示命令；
- PostgreSQL演示库完成GET查询；
- POST首次返回`202`及原子预留序号；
- 相同幂等键重试返回相同Job/Run且`replayed=true`；
- 演示API默认关闭；
- 非AI企业演示角色被拒绝；
- API不返回患者级内容、样本级推理、本地资产路径、凭据或下载链接。
