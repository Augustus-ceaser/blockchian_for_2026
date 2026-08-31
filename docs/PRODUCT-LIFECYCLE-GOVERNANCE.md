# 产品生命周期治理规则

## 状态与请求

产品状态与治理请求状态必须分离。产品可处于草稿、已发布、已下架或已归档；请求可处于 pending、approved、rejected、returned 或 cancelled。

```text
创建/编辑草稿
-> 提交上架
-> 平台批准并发布
-> 所有方申请下架
-> 平台影响分析与决定
-> 已下架
-> 申请重新上架或逻辑归档
```

## 发布时间

`published_at` 绑定具体不可变版本和 publication，由服务端在正式发布时写入。普通编辑、下架和读取页面不得伪造或覆盖历史发布时间。重新上架创建新的 publication，同时保留此前时间线。

## 下架

- 只有产品所属组织可以申请。
- 提交后产品仍保持 published，直到运营方批准。
- 运营方决定前必须读取服务端影响快照。
- running Run、未处理安全结果或无效审计链构成 BLOCKER。
- 批准后撤销 active publication、写入 `unpublished_at`，并阻止新的产品选择和 Application 提交。
- 已生效合同及锁定版本不被自动修改，历史执行和结果仍可查看。

## 重新上架

- 只允许已下架且未归档产品申请。
- 必须平台审核。
- 内容、Schema、数据范围、模型 digest、许可或安全策略发生变化时，不允许原版本直接重新上架，必须创建新版本。
- 批准后为同一不可变版本创建新的 active publication，并写入新的 `published_at`。

## 逻辑归档

- 只允许已下架产品申请归档。
- 平台批准后写入 `deleted_at`，不物理删除产品、版本或历史外键。
- 已归档产品永久退出新目录和新申请。
- 历史 Application、Contract、Job、Run、Artifact、Package、Grant 和 AuditEvent 保留。
- 主演示 PathMNIST 数据产品和固定 ResNet-18 模型产品受保护，不允许归档。

## 影响分析

影响快照由服务端生成，覆盖所有产品版本及其关联的：

- Application 与审核状态
- draft/active/未过期 Contract
- waiting/ready Job 与 running Run
- quarantined Artifact 与结果审核
- available Package 与 active Grant
- 受影响组织、版本和业务编号
- 审计链有效性

前端只负责展示，不得静态填充或修改影响结论。

## 幂等、并发与审计

- 同一产品只能存在一个有效 pending 生命周期请求。
- 相同命令和相同幂等键返回原结果。
- 不同幂等键的竞争决定或取消必须冲突。
- 运营双击、两个标签页并发决定和取消/审核竞争最多成功一次。
- 每次提交、取消、退回、拒绝、批准和实际状态变化均写入 AuditEvent。
