# CODEX → CHATGPT 阶段回传

## 1. 阶段
- 阶段名称：Phase 5.9 数据与模型生命周期治理、四账号独立门户产品化
- 完成状态：正式完成
- 基准日期：2026-07-25

## 2. Git
- 基线 Commit：`30f1d97e9983c48caf15386640528efca39f57fe`
- 实现 Commit：`e576e08c02c1ceb91e00f9ab9ba97a020575180a`
- 封板 Commit：`4e779765e1401a2f9bb5c13bd90e506c6222fcb9`
- Tag：`v0.12-phase5.9-lifecycle-four-portals`
- 工作区：clean
- 历史标签：未移动

## 3. 数据库
- Alembic：`20260725_0032`
- Migration：`20260725_0032_phase59_lifecycle_portals.py`
- 业务表：54
- 增量迁移：真实演示库 0031 → 0032 通过
- 空库迁移：通过
- 迁移往返：0031 ↔ 0032 连续两轮通过

## 4. 技术验收
- 后端测试：156 passed / 0 failed
- skipped：4 个外部 PathMNIST 资产环境门控项
- 前端测试：43 passed
- typecheck：通过
- build：通过，3704 modules
- OpenAPI paths：109
- OpenAPI operations：112
- 无效审计链：0

## 5. 生命周期
- 数据 published_at：服务端真实 publication 时间，历史保留
- 模型 published_at：服务端真实 publication 时间，历史保留
- 下架申请：数据/模型所有方均可提交
- 平台下架审核：服务端影响分析后决定
- 重新上架：必须审核；内容变化必须新版本
- 删除申请：定义为逻辑归档
- 平台删除审核：仅已下架产品可批准
- 删除语义：写入 `deleted_at`，不物理删除
- active 合同保护：不自动修改合同和锁定版本
- 历史引用：Application、Contract、Job、Run、Artifact、Package、Grant 保留
- 影响分析：跨全部产品版本，由服务端生成并固化

## 6. 四账号
- 医院账号：`hospital.demo`
- 模型方账号：`model.demo`
- 需求方账号：`requester.demo`
- 运营方账号：`operator.demo`
- 密码哈希：scrypt
- 明文密码持久化：否
- 四 Membership：存在且角色正确
- 四 session 并发：通过，Cookie 均不同
- 角色切换默认状态：隐藏；不能获得后端权限

报告不包含真实密码、Cookie、Token、密钥或本地资产路径。

## 7. 四门户
- 医院门户：数据产品、数据审批、数据 ready、出域审核、生命周期申请
- 模型门户：模型产品、模型许可、模型 ready、技术确认、生命周期申请
- 需求门户：published-only 目录、Application、合同、Job、下载
- 运营门户：上架/生命周期审核、合约、资格、派发和合规审核
- 路由权限：API 模式直接路由有显式 403，后端继续权威拒绝
- 下一责任方提示：提示切换浏览器窗口，不自动切换账号
- `/roadshow` 兼容：保留真实只读编排

## 8. 浏览器验收
- 数据生命周期流程：创建、发布、下架、重上架、再下架、逻辑归档通过
- 模型生命周期流程：创建、发布、下架、重上架、再下架、逻辑归档通过
- 四账号完整登录/权限流程：通过
- 是否使用手工 SQL：否
- 是否使用角色切换：否
- 是否执行真实推理：Phase 5.9 未新增推理；Phase 5.7 固定资产回归基线通过
- 下载和二次拒绝：未改变既有语义；现有完整链保留
- 页面错误：0 个阻断错误
- API 错误：0 个未处理业务错误

## 9. 安全
- hard_isolation：false
- Executor：unknown
- 敏感信息扫描：通过
- `.env.local` 提交：否
- Token 暴露：否
- 密码暴露：否
- 历史审计链：有效

## 10. 文档
- 生命周期规则：`docs/PRODUCT-LIFECYCLE-GOVERNANCE.md`
- 四账号指南：`docs/ROADSHOW-FOUR-ACCOUNT-GUIDE.md`
- 四 Profile 指南：`docs/ROADSHOW-FOUR-BROWSER-PROFILE-SETUP.md`
- 本地账号设置：`docs/LOCAL-DEMO-ACCOUNT-SETUP.md`
- 交付报告：`docs/Phase5.9-lifecycle-governance-and-four-portals-delivery.md`

## 11. 已知限制
- 限制：本地单机工程演示，`hard_isolation=false`
- 人工设置：本地密码与固定公开 PathMNIST 资产路径
- 未实现：开放注册、找回密码、邮箱/短信验证、局域网、公网、云部署、任意模型或代码执行

## 12. 下一阶段建议
- 建议阶段：Phase 5.10
- 建议内容：局域网四设备路演、远程受控访问与部署准备
- 不应提前开发：开放 `0.0.0.0`、防火墙、Tunnel、Tailscale、公网、云服务器、域名和 HTTPS
