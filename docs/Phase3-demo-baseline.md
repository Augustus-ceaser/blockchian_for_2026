# Phase 3 Demo Baseline

## 1. 基线目标

Phase 3 将既有纯 Mock 前端升级为可切换真实 FastAPI 后端的演示系统。当前 v0.2 结果作为不可覆盖的验证证据；后续演示任务使用独立演示空间或新的演示代次，不删除、重写或复用已经消耗完 `run_count=1/1` 的权威 Run。

## 2. 权威结果

- 结果文件：`tmp/pathmnist-controlled-smoke-authoritative-20260723.json`
- Run：`9e615302-a2d1-49c3-8be3-f9685cead351`
- Artifact：`07d98c50-c55d-4027-9a84-b9251ee95ea7`
- Artifact 状态：`quarantined`
- Alembic head：`20260722_0020`
- 表数：38
- 回归：134 passed，4 skipped

API 展示时可以引用以上聚合结果，但不得把权威结果文件当作数据库事实源。数据库可用时，业务状态、时间线和实体关系必须从 PostgreSQL 查询；静态文件只用于发布说明和基线核对。

## 3. 演示数据策略

演示数据全部使用虚构机构名称并标记 `is_demo=true`：

- 空间运营方：MedTrust 空间运营中心（演示）
- 数据提供方：数字病理数据协作机构（演示）
- 数据使用方：医学 AI 研究企业（演示）
- 数据产品：PathMNIST 结直肠组织图像分类数据产品（演示）
- 模型：固定白名单 `pathmnist_resnet18_v1`

每次需要“重置”时，采用以下安全顺序：

1. 验证目标数据库名称属于明确的演示前缀；
2. 新建独立演示数据库或新演示代次；
3. 执行 migration 到 `head`；
4. 运行幂等演示种子；
5. 不删除权威演示库，不修改历史 Run、AuditEvent、Outbox 或 Artifact；
6. 前端切换到新代次的 `space_id`。

“重置”不等于删除真实业务记录，也不通过直接 SQL 篡改 Contract、Run 或 Artifact 状态。

## 4. 备份与恢复

本机权威备份：

```powershell
docker exec medtrust-space-postgres-1 pg_dump `
  -U medtrust `
  -d medtrust_pathmnist_smoke_authoritative_20260723 `
  --format=custom --no-owner --no-privileges `
  -f /tmp/medtrust-v0.2-controlled-smoke.dump
```

只允许恢复到新的演示数据库：

```powershell
docker exec medtrust-space-postgres-1 createdb `
  -U medtrust medtrust_demo_restore_20260723
docker cp tmp\medtrust-v0.2-controlled-smoke.dump `
  medtrust-space-postgres-1:/tmp/medtrust-v0.2-controlled-smoke.dump
docker exec medtrust-space-postgres-1 pg_restore `
  -U medtrust `
  -d medtrust_demo_restore_20260723 `
  --no-owner --no-privileges `
  /tmp/medtrust-v0.2-controlled-smoke.dump
```

不得覆盖共享库或生产库。恢复后的权威 Run 仅用于只读展示，不能通过修改额度再次执行。

## 5. Git 冻结说明

工作区最初存在空的 `.git` 目录但不是有效仓库。Phase 3 在不删除文件的前提下重新初始化 Git，建立首个源码基线提交并打 `v0.2-controlled-smoke` 标签。数据集、权重、虚拟环境、数据库 dump、构建产物和本地凭据均不进入提交。

## 6. 能力声明

- `demo=true`
- `simulated=false` 仅用于已真实执行的 PathMNIST 受控推理结果；其他纯展示数据仍为模拟。
- `hard_isolation=false`
- 不宣称临床、生产级沙箱、隐私计算、医院接入、医疗器械或国家测评能力。

