# Phase 5.13 EvidenceBundle 规范

EvidenceBundle 是医院审核后允许返回中央的最小可信交付物，不等同原始 Artifact。

## 必需内容

- `bundle_id`、`bundle_version`、Connector 与医院组织 ID；
- PolicyBundle、ExecutionOrder 及其摘要；
- DataProductVersion、LocalAssetVersion、ModelProductVersion、StudyProtocolVersion；
- execution environment、镜像、输入 manifest 摘要；
- 白名单 output manifest（名称、媒体类型、大小、摘要）；
- 聚合指标、质量限制、协议偏离、安全事件；
- 医院本地 Artifact 稳定引用，不含对象键或路径；
- OutputReviewDecision、审核角色身份、时间；
- hospital audit head、Connector key_id、签名、bundle digest。

## 明确禁止

原始患者数据、本地路径、数据库凭据、可逆患者标识、解密密钥、原始未审核日志、未批准文件和未审核 Artifact。

## 生成流程

```text
LocalRun completed
-> LocalArtifact quarantined
-> scanner passed
-> hospital egress review approved
-> canonical manifest
-> digest and Connector signature
-> central signature/digest verification
-> Evidence Registry
```

任一输出不在白名单、摘要不一致、审核过期、审计链断裂或策略撤销，都阻断生成或接收。中央保存接收事实但不能改变医院审核决定。bundle 版本不可变；更正使用新版本并保留 supersedes 引用。

