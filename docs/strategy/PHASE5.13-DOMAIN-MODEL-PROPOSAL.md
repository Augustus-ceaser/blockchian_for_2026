# Phase 5.13 领域模型提案

本文件只冻结设计，不创建表或迁移。通用规则：ID、创建主体、时间、版本、状态、摘要和签发方是不可变事实；修订通过新版本；撤销追加事件；敏感本地值不得进入中央库。

## Connector 与节点

| 对象 | 目的与关键字段 | 状态/责任/边界 |
|---|---|---|
| HospitalConnector | 医院物理节点稳定身份；org、external_id | requested/approved/active/paused/revoked；运营审批，医院控制 |
| ConnectorIdentity | 节点主体、key_id、subject | active/revoked；不可复用身份 |
| ConnectorCertificate | fingerprint、issuer、validity | pending/active/expired/revoked/rotated；不保存私钥 |
| ConnectorRegistrationRequest | nonce、CSR 摘要、能力摘要 | submitted/approved/rejected/expired；幂等 |
| ConnectorCapabilityManifest | schema/version、能力、摘要、签名 | proposed/accepted/superseded；Connector 签名 |
| ConnectorHeartbeat | connector、sequence、posture 摘要 | append-only；不含路径 |
| ConnectorRevocation | reason、effective_at、authority | scheduled/effective；不可删除 |
| ConnectorSecurityPosture | OS/runtime 摘要、控制状态 | pass/warn/fail；仅同步摘要 |

## 本地资产

| 对象 | 目的与关键字段 | 状态/责任/边界 |
|---|---|---|
| LocalAssetDescriptor | 产品版本到医院资产的稳定映射 | registered/paused/retired；中央只知稳定 ID |
| LocalAssetVersion | schema、content/manifest digest | draft/verified/changed/retired；内容不可变 |
| LocalAssetLocationRef | 加密本地引用 | local-only；永不出域 |
| LocalDataDictionary | 字段、单位、编码、语义版本 | draft/approved/superseded |
| LocalAssetAvailability | 可用性、时间窗、容量区间 | available/limited/unavailable；只同步摘要 |

## 治理、隐私与质量

GovernanceEvidence/Version 记录依据类型、签发方、适用地区/对象、有效期、文件摘要；EvidenceApplicability 连接用途与资产；EvidenceReviewDecision 和 EvidenceRevocation 追加审核与撤销。

DataGovernanceProfile 聚合控制方、来源、目的、分类、地区路径和留存；DeidentificationAssessment 与 ReidentificationRiskAssessment 分别记录方法、版本、可逆性、攻击假设和复评条件。

DataQualityProfile 按资产版本和用途绑定；FieldProfile、MissingnessProfile、CodingProfile、TemporalConsistencyProfile 提供可复核维度；RepresentativenessAssessment 和 FitnessForUseDecision 只能针对明确研究问题，不能生成脱离用途的“高质量”总分。

## 研究方案

ResearchStudy 是长期研究容器；ProtocolVersion 冻结 ResearchQuestion、TargetEstimand、CohortDefinition、纳排、暴露、对照、结局、协变量、VariableDictionary、StatisticalAnalysisPlan 和 SensitivityAnalysisPlan。ProtocolDeviation 追加偏离；SiteReadiness 记录每站点资格。

协议 approved/frozen 后不可原地修改，ComputeJob 或 ExecutionOrder 必须引用冻结版本摘要。

## 策略、任务与证据

PolicyBundle/Version、ExecutionOrder、LocalTaskDecision、LocalExecutionReceipt 和 ExecutionRejectionReason 使用前述规范。LocalArtifact、LocalArtifactScan 和 OutputReviewDecision 位于医院域；EvidenceBundle/Version 和 StudyEvidencePackage 是获批出域证据。

## 复用而非重复

- 复用 Application、Contract、Readiness、ComputeJob/Run、Artifact review、ReleasePackage、DownloadGrant 和 AuditEvent 的生命周期经验。
- 不把中央 Artifact 当作医院 LocalArtifact，不把 ReleasePackage 直接改名为 EvidenceBundle。
- 不复制合约政策到 ExecutionOrder；不把治理证据压成布尔字段；不存医院真实路径。

