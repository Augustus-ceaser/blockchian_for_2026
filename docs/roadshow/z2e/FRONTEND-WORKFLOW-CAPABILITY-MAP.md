# Phase 5.12.7-Z2E 前端流程能力图

## 审计范围

- 测试基线：`097ac29393323b17c69cf3cd894814a70de0c5f6`
- Z2E 入口：`http://127.0.0.1:18080`
- 统一业务入口：Gateway 下的 `/api/v1`
- 会话：需求企业、医院数据方、模型提供方、空间运营方、目录策展方五个独立浏览器 Context
- 判定原则：人工业务决定必须有前端入口；系统自动动作必须由前序前端动作合法触发；不得以直接 API 写入补齐缺口。

## 24 步能力图

| # | 流程步骤 | 页面与路由 | 角色 | 前端入口或触发动作 | 统一 Gateway | 直接 API 旁路 | 系统自动 | UI 缺口 |
|---:|---|---|---|---|---|---|---|---|
| 1 | 新建 Application | 计算需求 `/applications`、向导 `/applications/new` | 需求企业 | `新建计算需求`、`保存草稿` | 是 | 否 | 否 | 无 |
| 2 | 选择 DataProduct | 向导 `/applications/new` 第 1 步 | 需求企业 | 数据产品单选器 | 是 | 否 | 否 | 无 |
| 3 | 选择 ModelProduct | 向导 `/applications/new` 第 2 步 | 需求企业 | 模型产品单选器 | 是 | 否 | 否 | 无 |
| 4 | 填写用途与范围 | 向导 `/applications/new` 第 3、4 步 | 需求企业 | 用途、样本数、子集、输出和最小化字段 | 是 | 否 | 否 | 无 |
| 5 | 提交 Application | 向导 `/applications/new` 第 5 步 | 需求企业 | `提交多方审核` | 是 | 否 | 否 | 无 |
| 6 | Hospital 审核 | 列表 `/applications`、详情 `/applications/:applicationId` | 医院数据方 | `审核`、结构化审核表、`批准` | 是 | 否 | 否 | 无 |
| 7 | Model Provider 审核 | 列表 `/applications`、详情 `/applications/:applicationId` | 模型提供方 | `审核`、结构化审核表、`批准` | 是 | 否 | 否 | 无 |
| 8 | Operator 预审 | 列表 `/applications`、详情 `/applications/:applicationId` | 空间运营方 | `审核`、结构化审核表、`批准` | 是 | 否 | 否 | 无 |
| 9 | 生成数字 Contract | 已批准申请 `/applications/:applicationId` | 空间运营方 | `生成数字合约草稿` | 是 | 否 | 否 | 无 |
| 10 | 四方确认并激活 | 合约 `/contracts/:contractId` | 四个业务角色 | `确认当前版本`；运营方最后执行 `激活数字合约` | 是 | 否 | 否 | 无 |
| 11 | 查看 Readiness | 执行准备 `/execution`、详情 `/execution/:contractId` | 四个业务角色 | `进入准备` | 是 | 否 | 否 | 无 |
| 12 | 数据 Ready | `/execution/:contractId` | 医院数据方 | 声明确认后点击 `确认数据执行就绪` | 是 | 否 | 否 | 无 |
| 13 | 模型 Ready | `/execution/:contractId` | 模型提供方 | 声明确认后点击 `确认模型执行就绪` | 是 | 否 | 否 | 无 |
| 14 | Executor 与策略检查 | `/execution/:contractId` | 空间运营方 | `运行资格检查` | 是 | 否 | 检查内容由服务端生成 | 无 |
| 15 | 创建 ComputeJob | `/execution/:contractId` | 需求企业 | `创建待派发 ComputeJob` | 是 | 否 | 否 | 无 |
| 16 | 提交并派发 Job | `/execution/:contractId` | 空间运营方 | `发起受控执行` | 是 | 否 | 否 | 无 |
| 17 | 查看 ComputeRun | `/execution/:contractId` | 四个业务角色 | 派发后显示真实执行时间线、回调和指标 | 是 | 否 | 是，由第 16 步创建并运行 | 无 |
| 18 | 查看 quarantined Artifact | 列表 `/results`、详情 `/results/:artifactId` | 四个业务角色 | `查看` | 是 | 否 | 是，由成功 Run 生成 | 无 |
| 19 | 三方结果 Review | `/results/:artifactId` | 医院、模型方、运营方 | 运营方 `创建审核计划`；各方 `审核`、`批准` | 是 | 否 | 审核任务由计划生成 | 无 |
| 20 | 生成 ReleasePackage | `/results/:artifactId` | 空间运营方 | `生成安全结果包` | 是 | 否 | 否 | 无 |
| 21 | 创建 DownloadGrant | `/results/:artifactId` | 需求企业 | `创建授权并下载` | 是 | 否 | 否 | 无 |
| 22 | 首次下载 | `/results/:artifactId` | 需求企业 | 第 21 步同一按钮在授权创建后立即发起受控下载 | 是 | 否 | 否 | 无 |
| 23 | 验证重复下载拒绝 | `/results/:artifactId` | 需求企业 | `验证二次使用被拒绝` | 是 | 否 | 拒绝和审计由服务端执行 | 无 |
| 24 | 查看 Audit chain | 结果详情 `/results/:artifactId`、审计中心 `/audit` | 四个业务角色 | `审计证据`、侧栏 `审计与基础设施` | 是 | 否 | 哈希链验证由服务端完成 | 无 |

## 角色与顺序约束

- Application 审核顺序是运营方预审、医院数据审核、模型使用审核；前序任务未完成时，后序任务可见但不可操作。
- Contract 需要需求企业、医院数据方、模型提供方和运营方分别确认同一 revision 与 digest，之后仅运营方可激活。
- 医院和模型方只能确认各自 Readiness；运营方不能代替提供方确认。
- 只有需求企业可创建 ComputeJob，只有运营方可派发。
- Artifact 保持 `quarantined`；三方结果审核通过后创建独立 ReleasePackage，不改变原始 Artifact 的隔离状态。
- DownloadGrant 与第一次下载由同一个明确的前端命令串行完成；同一页面保存本次 token，提供第二次使用验证入口。

## 结论

静态路由、角色守卫、按钮条件和命令调用审计未发现关键人工步骤只能通过 API 完成。随后完成的五个独立浏览器会话实测证明：24 步均由前端入口完成或由前序前端动作合法触发，直接业务 API 写入为 0，未发现阻断流程的 UI 缺口。

非阻断问题：Readiness 页的“确认意见”可见标签未与文本域建立完整的程序化关联，人工操作不受影响，但后续应补充 `htmlFor`/控件标识以改善辅助技术和自动化定位。
