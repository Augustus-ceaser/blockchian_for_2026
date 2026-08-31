# CODEX → CHATGPT 热修复回传

## 1. 状态

- 阶段：Phase 5.8.1 路演预检与启动流程热修复
- 完成状态：正式完成
- 日期：2026-07-25
- 根因：停止的 Compose 服务返回空输出和退出码 0，旧 PowerShell 脚本直接调用 `.Trim()`，触发空值方法调用异常。

## 2. Git

- Branch：`main`
- 基线 Commit：`c9cd9a6feb333490fa748991bdf950b9790d3187`
- 热修复实现 Commit：`8f8de564079d27832d2c9c8f7ac93550db088ecd`
- Tag：`v0.11.1-roadshow-preflight-hotfix`
- 工作区：封板后 clean
- 历史标签：`v0.11-phase5.8-roadshow-seal` 及更早标签未移动

## 3. 修改

- preflight：读取真实 Compose 服务，安全处理空容器 ID，完整汇总停止/缺失/不健康服务，保持只读。
- prepare：检查 Docker，正式 stop，检查端口，启动 PostgreSQL/MinIO，等待就绪，执行 preflight，可选 reset，正式 start，检查 HTTP 并运行 status。
- stop/start：验证 PID 的命令和项目归属，拒绝复用或未知 PID，清理已确认 stale 的 PID 文件，停止已验证进程树。
- reset/start：移除 Compose 空输出 `.Trim()`，增加原生命令退出码和基础设施就绪检查。
- 文档：新增 Phase 5.8.1 交付报告，更新 README、CHANGELOG、操作清单、故障手册和项目交接状态。

## 4. Compose

- PostgreSQL 服务名：`postgres`
- MinIO 服务名：`minio`
- 其他 Compose 服务：`backend`
- 容器停止时 preflight：无 Null 异常；分别输出 PostgreSQL、MinIO 未运行，并建议 `docker compose up -d postgres minio`
- 容器运行时 preflight：PostgreSQL healthy，MinIO running，Alembic `20260725_0031`

## 5. 验证

- stop 重复执行：通过
- stale PID：仅在全部记录进程不存在时清理
- PID 重用：归属不匹配时拒绝停止并保留 PID 文件
- 受管子进程：Uvicorn 子监听进程可识别、可随正式 stop 释放
- 端口释放：5173=0、8000=0
- Docker CLI 不可用：受控错误，无空值异常
- Compose 服务不存在：受控错误，显示可用服务名
- preflight 停止态/运行态：通过
- reset：通过；只重建固定 Phase 4 基线
- start：通过
- 前端 HTTP：`200`
- 后端 HTTP：`200`
- status_roadshow：health ok、Audit chain valid
- 完整 prepare：从应用停止状态通过
- 重复 prepare：通过
- PowerShell 5.1 语法：通过
- PowerShell 7：当前机器未安装，未执行
- UTF-8：357 个 tracked 文本文件可严格解码；16 个热修复文件无损坏标记
- 前端：typecheck 通过，production build 通过，3703 modules
- `git diff --check` 与敏感值扫描：通过

## 6. 安全

- 是否修改业务状态机：否
- 是否新增 migration：否
- 是否修改 API 业务语义：否
- 是否手工 SQL：否
- 是否重新推理：否
- 是否修改 `run_count`、Artifact、package 或 grant：否
- 是否泄露密码、Token、数据库连接串、MinIO Key、下载授权或资产绝对路径：否
- `hard_isolation=False`：保持可见
- `Executor=unknown`：保持可见，不伪造独立心跳

## 7. 用户最终命令

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

`prepare_roadshow.ps1` 已包含应用启动，完成后不要再单独运行 `start_phase4_demo.ps1`。默认 prepare 不 reset；`-Reset` 会重建固定 Phase 4 基线并移除 Phase 5 路演链。stop 只关闭应用和 Worker，PostgreSQL 与 MinIO 保持运行。

## 8. 已知限制

- 本次正式验证执行过 Phase 4 reset，当前 `Business chains` 为空。
- 未使用手工 SQL、伪造对象或额外推理恢复 Phase 5 链。
- 如需恢复完整 Phase 5 路演链，必须另行明确授权正式重建范围。
- PowerShell 7 未安装，只有 Windows PowerShell 5.1 完成运行验证。
- 历史 Phase 3 `scripts/reset_demo.ps1` 仍有既有空输出 `.Trim()` 与代码位置缺陷；它不在 Phase 5.8.1 路演主链内，本次未扩大范围修改。
- 当前应用和基础设施保持运行，路演 URL 可访问。
