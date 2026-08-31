# 路演对象地图

## 四账号与生命周期

```text
LocalDemoCredential
-> LocalDemoSession (digest only)
-> User + OrganizationMembership + Role
-> role-specific portal

DataProduct / ModelProduct
-> ProductLifecycleRequest
-> impact snapshot
-> operator decision
-> unpublish / relist / logical archive
-> AuditEvent
```

## 当前实时主链

实时主链由 `/api/v1/roadshow-experience/chains` 动态识别为未完成链，当前示例：

```text
APP-AD02DD51
-> published DataProductVersion
-> published ModelVersion
-> approved Application
-> next: operator creates Contract
```

该对象可能随正式 reset 和路演操作变化，编号应以页面实时结果为准。

## 完成态备用案例

```text
APP-BD5902BE
-> CON-BD5902BE
-> Job f96de33b...
-> Run 0d5bfed7...
-> Artifact 5833fed4...
-> Package 009844a4...
-> Grant b8d72255... exhausted
```

```text
APP-57F74162
-> CON-57F74162
-> succeeded Job/Run
-> Artifact 743a2b9d...
-> available Package
-> exhausted Grant
```

## 关联关系

```text
DataProductVersion + ModelVersion
-> Application + ApplicationSnapshot + ReviewTask
-> Contract + ContractRevision + ContractParty + Signature
-> ContractReadinessConfirmation + ExecutionEligibilitySnapshot
-> ComputeJob -> ComputeRun -> Callback -> Artifact
-> ArtifactReviewTask + ArtifactReviewDecision
-> ApprovedResultPackage
-> ResultDownloadGrant
-> AuditEvent
```

## 不记录内容

- 下载 Token 明文。
- MinIO bucket、object key 或访问凭据。
- 数据库连接串。
- 本地数据/模型绝对路径。
- 原始病理图像、模型权重和执行工作目录。
