# Phase 2-B.8 Stage 4：Local Built-in Executor 接入前就绪

## 定位

本阶段只建立“真实数据和真实模型注册前”的安全骨架。它没有读取或运行用户数据、病理图像、用户模型或任意用户代码。

## 组件

- `LocalBuiltInExecutorAdapter`：`backend/app/execution/local_adapter.py`
- `ModelRegistry` / `DatasetRegistry`：`backend/app/execution/registry.py`
- 工作区：`backend/app/execution/workspace.py`
- 输入/输出 Manifest 校验：`backend/app/execution/manifests.py`
- 资源策略与内置函数 Runner：`backend/app/execution/builtins.py`
- Preflight：`backend/app/tools/preflight_model_onboarding.py`
- 合成全链自检：`backend/app/tools/run_local_executor_self_test.py`
- Manifest 模板：`templates/`

## 安全边界

- 只允许 `builtin.synthetic_statistics.v1`，不接受文件路径、Shell、任意 Python 或任意镜像。
- Model/Dataset 必须预注册、启用且摘要匹配。
- 默认 `network_access=false`。
- 工作区按 Run 隔离为 input/work/output/logs/manifests。
- 拒绝绝对路径、`..`、符号链接和工作区外访问。
- 输出只允许 manifest 白名单字段和合同批准的 output type。
- 自检输入是内存生成的 1—10 数值，不读取任何用户文件。
- 输出只有 `metrics.json` 和 `execution_summary.json`，Artifact 仍为 `quarantined`。

## 诚实的能力边界

当前 Windows 原型使用进程内白名单函数和 `asyncio` timeout。它没有提供生产级 CPU/内存硬配额、系统调用沙箱或可靠的操作系统级断网。正因如此，它只允许内置合成自检，尚不允许真实模型接入后直接运行。

未来生产执行应替换为受控容器或专用隔离执行节点，并保持现有 ExecutorAdapter、Callback Inbox、Audit/Outbox 协议不变。

## 命令

在 `backend` 目录执行：

```powershell
$env:PYTHONPATH='.'
$env:MEDTRUST_TEST_DATABASE_URL='postgresql+asyncpg://medtrust:medtrust_dev_only@127.0.0.1:5432/<一次性测试库>'
python -m app.tools.run_local_executor_self_test
```

Preflight（本阶段仅允许仓库测试夹具）：

```powershell
python -m app.tools.preflight_model_onboarding `
  --model-manifest tests/fixtures/onboarding/model_manifest.yaml `
  --dataset-manifest tests/fixtures/onboarding/dataset_manifest.json
```

## 自检结果

已真实经过 Active Contract、Job、Run reservation、Outbox、Consumer Inbox、Coordinator、Local Accepted、dispatched、started Callback、running、completed Callback、succeeded、quarantined Artifact 和审计链验证。未产生下载链接，工作区在结束后清理。

