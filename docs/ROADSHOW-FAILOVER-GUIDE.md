# 路演故障兜底指南

| 问题 | 现场信号 | 30 秒内处理 | 备用对象 | 录屏 |
|---|---|---|---|---|
| 前端打不开 | 5173 无响应 | 运行 `status_roadshow.ps1`，必要时 stop/prepare | 是 | 是 |
| 后端打不开 | 页面显示 API 读取失败 | 检查 Backend 状态，stop/prepare | 是 | 是 |
| Docker 未启动 | PostgreSQL/MinIO 异常 | 启动 Docker Desktop，再运行 prepare | 否 | 是 |
| MinIO 异常 | health 为 not_ready | 不执行 package/download，切完成态讲证据 | 是 | 是 |
| Worker 未启动 | Dispatcher/Coordinator/Callback unknown | stop/prepare；不手工改状态 | 是 | 是 |
| 页面持续刷新 | loading 不结束 | 刷新一次；终态链应停止轮询 | 是 | 是 |
| 浏览器旧缓存 | 文案或布局仍旧 | 强制刷新页面，不清除业务数据库 | 是 | 否 |
| 推理未开始 | Run 长时间未出现 | 检查 Worker；30 秒后切备用链 | 是 | 是 |
| 推理失败 | Run failed/interrupted | 展示失败审计，不伪造成功 | 是 | 是 |
| Artifact 未生成 | Run succeeded 但无 Artifact | 检查 Callback；切备用链 | 是 | 是 |
| 审核按钮不可见 | 当前角色不匹配 | 使用下一责任方切换，核对组织 | 是 | 否 |
| 下载授权已使用 | Grant exhausted | 不创建假 Token；展示拒绝审计 | 是 | 是 |
| Token 重用 | HTTP 409 | 说明一次性约束已生效 | 否 | 否 |
| 端口占用 | preflight 报 5173/8000 | 停止已记录进程；不杀未知进程 | 否 | 是 |
| PowerShell 策略 | 脚本被阻止 | 使用当前会话允许的签名/策略，不修改业务数据 | 否 | 是 |
| reset 失败 | 专用库未重建 | 保留现场数据，切完成态或录屏 | 是 | 是 |
| PID 文件残留 | 进程不存在但提示运行 | 使用正式 stop；脚本只清理已确认不存在的 stale PID | 否 | 是 |
| PID 被复用 | stop 报归属不匹配 | 不删除 PID 文件、不停止该进程；检查文件和占用进程 | 否 | 是 |
| 登录失败 | 账号或密码错误 | 重新运行本地密码设置和 prepare；不打印密码 | 否 | 是 |
| 会话失效 | 页面跳回登录 | 在当前 Profile 重新登录；不要借用其他角色 Cookie | 是 | 否 |
| 菜单缺失 | 登录角色与窗口不匹配 | 核对页头账号和组织，退出后登录正确账号 | 是 | 否 |
| 生命周期审核不可见 | 非运营账号访问 `/lifecycle` | 切换到运营 Profile；403 属于正确权限行为 | 是 | 否 |

## 恢复顺序

```powershell
.\scripts\stop_phase4_demo.ps1
.\scripts\prepare_roadshow.ps1
```

不要在 prepare 完成后再单独运行 `start_phase4_demo.ps1`。Docker 未启动时先启动 Docker Desktop；未知进程占用 5173/8000 时先确认并由进程所有者停止，脚本不会强制终止未知进程。

## 禁止动作

- 不用手工 SQL 推进状态。
- 不修改 Callback 或 Artifact payload。
- 不复用或打印下载 Token。
- 不把备用对象说成当前实时对象。
- 不把 health 的 `unknown` 说成“已验证健康”。
