# Phase 3 — 真实数据驱动演示交付

日期：2026-07-23

## 一键操作

首次或需要恢复干净状态：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1 -Reset
```

保留现有演示数据再次启动：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1
```

停止应用进程：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_demo.ps1
```

只恢复数据库：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\reset_demo.ps1
```

入口：前端`http://127.0.0.1:5173`，OpenAPI`http://127.0.0.1:8000/docs`。

## 运行组成

- PostgreSQL 16与MinIO由Docker提供；
- FastAPI提供真实只读查询和固定PathMNIST命令；
- Outbox Dispatcher把`compute.dispatch`可靠写入Coordinator Consumer Inbox；
- Coordinator核验当前合同、额度、Connector和能力；
- Local Built-in Executor只运行代码内白名单`pathmnist_resnet18_v1`；
- Callback Worker把started/completed事实写回数据库，并创建`quarantined` Artifact；
- React在`VITE_DATA_MODE=api`下轮询真实Run状态。

本地Executor与Coordinator同进程运行，以保留内存内签名化执行请求；这是单机工程演示，不是生产级Worker隔离。Outbox除`compute.dispatch`外的本地读模型投递在演示Publisher中确认，不代表外部消息代理。

## 演示脚本

1. 用“AI企业用户”进入工作台，确认页面顶部显示“真实后端模式”。
2. 在数据产品查看PathMNIST固定版本、20样本资源和受控计算策略。
3. 在使用申请查看固定算法、目的和20次演示额度。
4. 在数字合约查看ACTIVE Revision、合同对象、permit/deny策略和演示签署摘要。
5. 在可信计算点击“启动 PathMNIST 20张受控推理”。
6. 观察Run从`reserved`、`dispatched`、`running`到`succeeded`。
7. 查看聚合指标，并强调它来自已验证冻结基线；新任务不展示样本级预测。
8. 查看Artifact保持`quarantined`且下载按钮禁用。
9. 在审计中心查看`compute.job.created`、`compute.run.reserved/started/completed`和`artifact.created`。
10. 回到工作台确认Outbox已投递、执行/回调Inbox已完成且审计链校验为有效。

## 安全与真实性边界

- 使用公开PathMNIST与固定ResNet-18，20张固定test样本，CPU推理；
- 不运行用户上传代码，不扫描指定资产目录之外的文件；
- 不返回原图、样本级预测、特征、模型权重、本地路径、凭据、密钥或令牌；
- Artifact不进入`released`，不生成外部下载链接；
- `hard_isolation=false`，进程内白名单执行不等于生产级隐私计算；
- 不是临床验证、医疗器械性能验证、真实医院接入或国家可信数据空间测评。

## 复位与恢复

`reset_demo.ps1`只操作固定数据库`medtrust_demo`，从本机冻结备份`tmp/medtrust-v0.2-controlled-smoke.dump`恢复，并派生独立的PathMNIST演示产品、申请、审核和合同。权威冒烟运行、历史migration和本地资产均不修改。

## 最终验收结果

- Alembic head：`20260722_0020`；`medtrust`实表：38；
- 浏览器真实模式：Outbox 34/34已投递，执行Inbox 4/4完成，回调Inbox 8/8完成，审计链有效；
- 最新Run：`succeeded`；所有4个演示库Artifact均为`quarantined`，`released=0`；
- 后端全量回归：134 passed、5个显式环境门禁skip；
- PathMNIST真实数值冒烟：在独立干净数据库和D盘数值运行时通过；
- Phase 3 API查询与幂等命令集成测试：在一次性恢复库通过；
- 前端TypeScript检查、生产构建及真实浏览器视觉验收通过。

大体积前端chunk仅产生构建警告，不影响本次演示；后续可按路由拆包优化，不应在本阶段为消除警告扩大改动范围。
