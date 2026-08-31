# 路演操作清单

## 演示前 30 分钟

- 确认 `config/phase4-demo.env` 指向公开 PathMNIST 数据和固定模型资产。
- 运行 `scripts/prepare_roadshow.ps1`；它已包含 stop、基础设施启动、preflight、start、HTTP 和 status。
- 日常路演不要使用 `-Reset`；该选项只重建固定 Phase 4 基线并移除 Phase 5 路演链。
- 确认 PostgreSQL、MinIO、Backend、Frontend、Dispatcher、Coordinator、Callback 正常。
- 确认 Audit chain valid，`hard_isolation=false` 可见。
- 确认至少一条实时主链和一条完成态备用案例。
- 确认四个浏览器 Profile 分别登录 `hospital.demo`、`model.demo`、`requester.demo`、`operator.demo`。
- 确认调试角色切换未显示，运营端 `/lifecycle` 可用，其他三端直接访问显示无权访问。

## 演示前 10 分钟

- 打开 `/roadshow`。
- 选择正确的 8 分钟或 15 分钟模式。
- 检查 1366×768 或 1920×1080 投屏。
- 关闭浏览器更新提示、系统通知和无关窗口。
- 不把下载 Token、日志或本地路径放入剪贴板。

## 演示前 2 分钟

- 刷新一次真实事实。
- 确认当前角色、当前链路、下一责任方和主链进度。
- 确认完成态备用链可切换。
- 确认讲解面板按投屏需要显示或隐藏。

## 演示开始

- 先声明公开数据工程演示和 `hard_isolation=false`。
- 所有业务操作从现有页面进入，不使用数据库工具。
- 使用“切换到下一责任方”，不要固定轮转角色。
- “下一责任方”只提示切换窗口，不应自动改变当前账号。
- 每次动作后核对编号、状态、下一步和审计证据。

## 演示结束

- 停在 Audit chain valid 和安全边界页面。
- 不删除业务事实。
- 如无需继续使用，运行 `scripts/stop_phase4_demo.ps1`。
- PostgreSQL 和 MinIO 保持运行，除非明确需要整体停机。

## 推荐命令

```powershell
cd "<repo-root>"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\stop_phase4_demo.ps1
.\scripts\prepare_roadshow.ps1
```

状态检查：

```powershell
.\scripts\status_roadshow.ps1
```

## 现场失败

- 30 秒内不能恢复时切换完成态备用案例。
- 完成态对象不可再次演示首次下载；展示 1/1 exhausted 与拒绝事件。
- 页面异常时优先刷新一次；仍异常则按故障手册切换录屏或截图。
