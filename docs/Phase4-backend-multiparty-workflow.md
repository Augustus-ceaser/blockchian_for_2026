# Phase 4 后端多主体工作流实施报告

## 实施结果

后端已形成由 PostgreSQL 权威状态驱动的四方可信协作链。Alembic head 为 `20260723_0025`，`medtrust` schema 共有 48 张业务表。历史迁移未被修改；0024、0025 是针对合同角色和 Compute 请求方边界的纠正迁移。

主要实现位置：

- `backend/app/demo/phase4.py`：Phase 4 固定演示图、命令服务、合同/就绪/执行/审核/结果包编排；
- `backend/app/api/routes/roadshow.py`：四角色查询和显式命令端点；
- `backend/app/modules/marketplace/models.py`：模型目录、就绪、结果审核、结果包和下载授权；
- `backend/app/modules/marketplace/services.py`：结果审核、白名单打包和一次性下载；
- `backend/app/execution/*`：Outbox、Inbox、Coordinator、固定本地执行器和回调闭环；
- `backend/alembic/versions/20260723_0021_*` 至 `20260723_0025_*`：增量数据库变更。

## 安全边界

- 所有写入均通过具体业务命令；没有通用 CRUD、状态 PATCH 或触发器绕行接口。
- 四种 `X-Demo-Identity` 只是演示身份选择，后端仍校验组织、用户、空间参与关系和角色。
- 数据需求固定数据版本与模型版本；模型版本绑定固定 `ModelRegistry` entrypoint 和摘要。
- `run_count` 由数据库原子占用；真实执行状态通过 Outbox、Consumer Inbox、Coordinator 和 Callback Inbox 推进。
- Artifact 创建后为 `quarantined`；结果审核决定是不可变证据。
- 打包前重新核验运行输出 manifest 中的 SHA-256；混淆矩阵 CSV 由聚合指标派生。
- 压缩包本身只含显式白名单文件，manifest 保存在数据库权威元数据中，不作为额外下载文件混入压缩包。
- PostgreSQL 与对象存储无法形成分布式原子事务。对象存储写入成功而数据库回滚时，可能遗留不可发现对象，由运维垃圾回收；实现没有虚假宣称跨系统原子性。

## 实际端到端证据

在专用数据库 `medtrust_phase4_demo` 上，浏览器完成了发布、审批、合同、三方就绪、任务执行、三方结果审核、结果包和一次性下载。真实固定执行器对 20 张 PathMNIST test 图像进行 CPU 推理：

- ComputeRun 进入 `succeeded`；
- Artifact 保持 `quarantined`；
- 审计流连续记录到 `result.download.completed`；
- 哈希链函数返回 `is_valid = true`；
- 下载包大小 1106 bytes，仅有 3 个条目；
- 同一下载令牌第二次使用被拒绝。

最终验证：142 个后端测试中 137 passed、5 个环境门禁测试 skipped；完整套件包含真实 PostgreSQL 迁移、触发器、并发和迁移往返。Python compileall、OpenAPI 生成均通过。未授权的需求企业直接调用合同激活命令返回 403。

结果包精确条目：

```text
aggregate_metrics.json
confusion_matrix.csv
execution_summary.json
```

路演库最终基础设施计数：1 个 succeeded Run、1 个 quarantined Artifact、35/35 Outbox published、1/1 Consumer Inbox completed、2/2 Callback Inbox completed。两次独立下载授权均已按各自的一次上限消耗。

## 明确未实现

- 任意模型上传、任意代码执行、训练任务或完整 WSI；
- 真实医院系统和患者数据接入；
- 操作系统/容器级硬隔离；
- 第三方可信存证、CA 电子签名或法律效力声明；
- 患者级输出、原始特征、模型权重或原始图像发布。
