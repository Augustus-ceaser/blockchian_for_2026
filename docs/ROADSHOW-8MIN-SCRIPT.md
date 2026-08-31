# MedTrust Space 8 分钟主路演脚本

适用入口：四个独立浏览器 Profile 登录 `/demo-login`，运营端主讲 `/roadshow`。

边界必须全程可见：`hard_isolation=false`、公开数据工程演示、非临床验证、非生产级隐私计算。

| 时间 | 页面/角色 | 点击动作 | 建议话术 | 预期后台事实 / AuditEvent | 失败备用 |
|---|---|---|---|---|---|
| 0:00-0:40 | 四窗口 / 运营方 | 展示四个真实账号，再选择 8 分钟模式 | 四方会话相互隔离，权限由服务端账号、组织和 Membership 决定。 | 4 个独立 session | 使用单机逐次登录备用 |
| 0:40-1:20 | 实时主链 / 需求企业 | 打开 Application 节点 | 企业申请受约束计算，不是申请下载数据。 | Application、兼容性快照、三方 ReviewTask | 展示完成态 Application |
| 1:20-2:05 | 运营/医院/模型方 | 使用“下一责任方”说明三方审核 | 平台预审、医院数据使用审核、模型使用审核相互独立。 | `application.review.decided` | 切到备用链的 3/3 审核事实 |
| 2:05-2:55 | 合约 / 运营方 | 打开 Contract 节点 | 多方约束按最严格规则收敛，四方确认同一版本；不是 CA 签名。 | 4/4 confirmation，revision active | 展示完成态合约矩阵 |
| 2:55-3:35 | 执行准备 / 三方 | 打开 Readiness 节点 | 合约 active 后仍需数据、模型和平台环境分别 ready。 | 3/3 readiness，Eligibility Snapshot | 展示完成态 3/3 |
| 3:35-4:35 | 受控执行 / 运营方 | 打开 Run 节点 | Dispatcher、Coordinator、固定 Executor、Callback 形成真实执行链。 | Job/Run succeeded，20 张公开图像 | 立即切换完成态备用链 |
| 4:35-5:20 | 隔离结果 / 运营方 | 展示 Artifact 对比 | 运行成功不等于允许出域，源 Artifact 始终 quarantined。 | `artifact.created`，无原始下载 | 展示完成态 Artifact |
| 5:20-6:15 | 结果审核 / 医院、模型、运营 | 打开 Result Review 节点 | 医院看出域边界，模型方看技术质量，平台最后做合规审核。 | 3/3 approved | 展示备用链 3/3 |
| 6:15-7:05 | Package / 运营方 | 展示三文件列表 | 只生成独立安全结果包，精确包含三个白名单文件。 | `result.package.created` | 展示完成态文件清单 |
| 7:05-7:40 | Download / 需求企业 | 展示 exhausted grant | 授权绑定机构、用户和包，只能消费一次；重用会被拒绝。 | completed + rejected 各 1 | 展示完成态 1/1 |
| 7:40-8:00 | Audit / 运营方 | 总结健康与审计链 | 整条链可验证，但当前仍是单机工程演示，不宣称生产隔离。 | audit chain valid | 使用录屏或截图 |

## 现场原则

- 不在 8 分钟内执行完整归档；可展示生命周期中心和已归档历史。
- 不使用顶部角色切换；按“下一责任方”提示切换到对应 Profile。
- 不等待不确定的 Worker 时间超过 30 秒；超时立即切换完成态备用链。
- 不打开开发者控制台完成业务动作。
- 不复制 Token、对象键、路径、凭据或原始结果。
- 所有写操作只通过 Phase 5.1 至 5.7 正式 API 页面执行。
