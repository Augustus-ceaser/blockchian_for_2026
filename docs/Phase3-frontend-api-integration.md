# Phase 3 — 前端真实 API 模式

日期：2026-07-23

## 双模式

前端保留 Phase 1 Mock 演示，同时增加真实后端模式：

```env
VITE_DATA_MODE=api
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

未设置或设置为`mock`时，原有全局阶段演示保持不变。设置为`api`时，工作台、数据产品、申请、合约、可信计算、节点和审计页面直接查询FastAPI/PostgreSQL。

## 页面映射

| 页面 | 真实接口 |
|---|---|
| 工作台 | `/overview` |
| 数据产品 | `/data-products`、`/data-products/{id}` |
| 使用申请 | `/applications` |
| 数字合约 | `/contracts`、`/contracts/{id}` |
| 可信计算 | `/compute-jobs`、`/compute-runs/{id}`、固定演示POST |
| 节点中心 | `/connectors` |
| 审计中心 | `/audit-events` |

所有真实页面包含加载、错误和空状态。页面显式显示`真实后端模式`，同时持续提示：公开演示数据、非临床、`hard_isolation=false`、Artifact不开放下载。

## 可信计算交互

“启动 PathMNIST 20张受控推理”只提交固定白名单命令，并生成新的浏览器幂等键。页面轮询Run状态，但不在前端伪造状态推进。已验证的0.95 Accuracy等指标标记为“冻结权威基线”，不会冒充新Run结果。

Artifact始终以`quarantined`语义呈现，下载按钮禁用。前端不接收或渲染对象存储引用、宿主机路径、凭据、密钥、令牌、原图或样本级预测。
