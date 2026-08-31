# Phase 2-B.8 Stage 3B：Execution Callback Inbox ORM

## 结论

Stage 3B 已完成。外部 Executor 回调使用独立的 `execution_callback_inbox_entries`，没有放宽内部 `consumer_inbox_entries` 的来源约束。

当前 Alembic head 为 `20260722_0020`；0019 新增 Callback Inbox 表，0020 只负责把 Run 的临时审计阻断替换为回调证据守卫。实表共 38 张。

## 实现位置

- ORM：`backend/app/modules/callback_inbox/models.py`
- 接收、租约、重试和终态服务：`backend/app/modules/callback_inbox/services.py`
- 回调信封：`backend/app/execution/callback.py`
- 迁移：`backend/alembic/versions/20260722_0019_execution_callback_inbox.py`
- 单元测试：`backend/tests/test_callback_inbox_models.py`
- PostgreSQL 测试：`backend/tests/integration/test_callback_inbox_postgresql.py`

## 已冻结的数据库保护

- `UNIQUE(executor_namespace, callback_id)` 防止同一回调身份漂移。
- 语义事实唯一约束防止更换 callback_id 重复推进同一事实。
- Run 与 Space 在数据库触发器中校验。
- payload、事实、执行证据和认证证据摘要均校验为 SHA-256。
- source 字段创建后不可修改。
- `completed`、`dead_letter` 不可恢复、不可更新、不可删除。
- Worker 使用租约和 `FOR UPDATE SKIP LOCKED` 领取。
- 接收端只在 Inbox 事务提交后 ACK。

## 隐私边界

Callback payload 使用顶层字段白名单，并递归拒绝 token、secret、credential、患者标识和 WSI 路径类字段。错误摘要会清理令牌、URL 查询参数和本地路径。

## 验证

- 空库升级到 0019/0020：通过。
- 0019 Callback Inbox 专项测试：通过。
- 0020→0018→0020 所在完整迁移循环：通过。
- 审计链：开发库 573 个 Space，0 个无效链。

