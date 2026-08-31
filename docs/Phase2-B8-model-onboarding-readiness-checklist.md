# 模型接入前就绪检查清单

## 状态

`READY_FOR_MODEL_ONBOARDING = true`

这里的 true 仅表示：可以进入下一阶段的“受控登记与小规模公开数据测试准备”，不表示可以临床使用或生产部署。

## 24 项验收

1. Callback Inbox 真实落库：通过。
2. Callback 身份与事实幂等：通过。
3. Coordinator 重试不重复提交：通过。
4. FakeExecutor 完整闭环：通过。
5. Local Executor Adapter 符合协议：通过。
6. Self-test 进入 running：通过。
7. Self-test 进入 succeeded（completed 回调）：通过。
8. 一次完成事实只生成一个自检 Artifact：通过。
9. Artifact 默认 quarantined：通过。
10. Artifact 不自动 released：通过。
11. run_count 数据库原子限制：专项并发测试通过。
12. Audit 链有效：开发库 573/573 有效；最终测试库 76/76 有效。
13. Outbox、Consumer Inbox、Callback Inbox 状态一致：通过。
14. Manifest 摘要稳定：通过。
15. Schema 和兼容性校验：通过。
16. 路径穿越拒绝：通过。
17. 符号链接逃逸拒绝：保护逻辑通过；当前 Windows 账户不能始终创建真实符号链接，测试在此情况下验证同一拒绝分支。
18. 非白名单 entrypoint 拒绝：通过。
19. 非白名单输出拒绝：通过。
20. timeout：通过。
21. 重复提交与回调幂等：通过。
22. 日志和回调 payload 不泄露敏感信息：字段白名单、递归拒绝和摘要化边界通过。
23. PostgreSQL 全量回归：133 passed，3 skipped；另行执行的 run_count 并发测试通过。
24. 空库完整迁移和全迁移循环：通过，最终 head 为 20260722_0020。

## 仍未实现的生产能力

- 生产级 Windows/容器网络隔离；
- CPU、内存和系统调用硬隔离；
- 真实对象存储发布与下载；
- 真实模型包验签、依赖供应链验证和漏洞扫描；
- 真实病理数据接入、脱敏和伦理合规流程；
- CA 电子签名、第三方可信存证和隐私计算；
- 临床验证、医疗器械合规和国家可信数据空间认证。

## 下一阶段建议

下一阶段只登记 PathMNIST 等公开、低风险、小样本数据的 manifest 和一个固定、预登记模型；先验证来源授权、哈希、Schema 和输出策略，再在受控容器能力补齐后运行。不得直接扫描现有下载目录或自动发现模型文件。
