# Phase 5.8.1 路演预检与启动流程热修复交付

日期：2026-07-25

## 交付结论

Phase 5.8.1 是脚本稳定性热修复。它不修改 Phase 5.1 至 5.8 的业务状态机、API 业务语义、数据库结构、migration、执行次数、Artifact、结果包、下载授权或推理逻辑。

基线：

```text
Commit: c9cd9a6feb333490fa748991bdf950b9790d3187
Tag: v0.11-phase5.8-roadshow-seal
Compose services: postgres, minio, backend
PowerShell: 5.1.26100.8875
Docker Compose: v5.3.1
```

## 问题与根因

当 PostgreSQL 或 MinIO 已停止时：

```powershell
docker compose ps -q postgres
docker compose ps -q minio
```

会返回空输出和退出码 0。旧脚本直接调用：

```powershell
(docker compose ps -q postgres).Trim()
```

Windows PowerShell 5.1 因此在 `scripts/preflight_phase4_demo.ps1` 原第 80 行抛出空值方法调用异常。相同风险还存在于 reset 和 start。

## 修复

- 统一读取真实 Compose 服务清单，并在服务缺失时给出可用服务名。
- 对空容器 ID、空 inspect 输出和多行输出做受控判断。
- PostgreSQL 或 MinIO 停止时完整输出两个 `[FAIL]`，并建议：

```powershell
docker compose up -d postgres minio
```

- preflight 保持只读，不启动服务、不 reset、不执行推理、不修改业务状态。
- prepare 按顺序完成 Docker 检查、正式 stop、端口检查、基础设施启动、受控就绪等待、preflight、可选 reset、正式 start、HTTP 检查和 status。
- stop 只终止 PID 文件中经命令行和项目日志目录双重验证的进程及其后代。
- PID 已不存在时按 stale 处理；PID 被其他进程复用或归属无法验证时拒绝停止并保留文件。
- start 只在确认全部记录进程已不存在后删除 stale PID 文件。
- PostgreSQL 和 MinIO 使用有超时的轮询，不再依赖固定等待。
- preflight 成功输出不打印数据资产或模型资产的绝对路径。

## Prepare 语义

默认命令：

```powershell
.\scripts\prepare_roadshow.ps1
```

会停止现有应用进程、启动 PostgreSQL/MinIO、执行预检、启动后端/前端/Worker，并检查路演状态。它不会 reset 数据库。

显式命令：

```powershell
.\scripts\prepare_roadshow.ps1 -Reset
```

会调用正式 Phase 4 reset。该 reset 只重建固定 Phase 4 演示图，会移除 Phase 5 路演业务链。不要把 `-Reset` 作为日常路演默认操作。

## 验证

- 在 PostgreSQL、MinIO 停止时复现旧空值异常。
- 修复后停止态 preflight 无空值异常，并完整报告两个基础服务未运行。
- Docker CLI 不可用时返回项目自定义受控错误。
- Compose 服务不存在时返回缺失服务名和可用服务清单。
- stale PID、PID 重用和未知端口占用均按失败关闭。
- 后端启动器的 Uvicorn 子进程被识别为同一受管进程树。
- stop 连续执行两次成功，5173/8000 均释放。
- 传统 reset/start 流程通过。
- 默认 prepare 从应用停止状态成功恢复。
- 重复 prepare 成功。
- 最终前端 `/roadshow` HTTP 200。
- 最终后端 `/api/v1/health/ready` HTTP 200。
- PostgreSQL healthy，MinIO running。
- Dispatcher、Coordinator、Callback 为 OK。
- Executor 保持 `unknown`，因为没有独立持久心跳。
- Audit chain valid，`hard_isolation=False`。
- Windows PowerShell 5.1 语法检查通过。
- PowerShell 7 当前机器不可用，未执行兼容性运行检查。
- 严格 UTF-8、热修复差异乱码标记、敏感值和 `git diff --check` 检查通过。

## 当前数据限制

本次验证按要求执行过正式 Phase 4 reset。该脚本不会恢复 Phase 5.1 至 5.8 的完整业务链，因此当前 `Business chains` 为空。没有使用手工 SQL、额外推理或伪造对象恢复链路。

## 日常操作

```powershell
cd "<repo-root>"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\stop_phase4_demo.ps1
.\scripts\prepare_roadshow.ps1
```

访问：

```text
http://127.0.0.1:5173/roadshow
```

查看状态：

```powershell
.\scripts\status_roadshow.ps1
```

结束演示：

```powershell
.\scripts\stop_phase4_demo.ps1
```

停止脚本只关闭应用和 Worker；PostgreSQL 与 MinIO 保持运行。

## 回滚

如需回到 Phase 5.8 封板代码，检出：

```text
v0.11-phase5.8-roadshow-seal
```

不要移动或覆盖该标签及更早历史标签。
