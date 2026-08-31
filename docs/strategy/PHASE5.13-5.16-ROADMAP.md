# Phase 5.13C accepted update

Metadata-only local asset registration, immutable versions, minimum local
quality summaries, reviewed bundle sync, and central metadata mirrors are now
implemented. Raw data/model transfer, products, execution, Artifacts, and
materialization remain disabled. Phase 5.13D has not started and
`hard_isolation=false`.

# Phase 5.13-5.16 路线图

## Phase 5.13B accepted update (2026-07-29)

Identity, registration review, Local Test CA mTLS, heartbeat, disabled
Capability Manifest, pause/resume, certificate rotation and revocation are now
implemented as a loopback Alpha. Phase 5.13C remains metadata-only Local Asset
Registry work. ExecutionOrder and local execution remain deferred to 5.13D
and 5.13E. `hard_isolation=false`.

## Phase 5.13 医院侧可信节点最小闭环

- **5.13A**：战略、三域架构、信任边界、威胁模型和协议规范冻结。
- **5.13B**：Connector identity、注册审批、证书/mTLS 骨架、心跳、Capability Manifest、暂停、撤销和中央管理页。
- **5.13C**：Local Asset Registry 与最小 DataQualityProfile；不暴露本地路径。
- **5.13D**：签名 PolicyBundle、ExecutionOrder、本地接受/拒绝和重放防护。
- **5.13E**：跨主机固定任务、本地 Artifact 隔离、医院出域审核和 EvidenceBundle。

## Phase 5.14 治理与质量证据层

实现 GovernanceEvidence、DataGovernanceProfile、DeidentificationAssessment、ReidentificationRiskAssessment、DataQualityProfile 和 FitnessForUse。治理、质量和有效期成为执行硬门，不输出自动“合规”或“高质量”结论。

## Phase 5.15 RWD 与持续证据生产

实现 StudyProtocol、AnalysisPlan、SiteFeasibility、多中心画像、AggregateAnalysis 和 StudyEvidencePackage。用公开或合成数据验证方法、版本和偏离证据，不使用真实患者数据作为早期工程门槛。

## Phase 5.16 外部登记、交易与运营适配

适配数据知识产权登记、公共数据登记、数交所上架、成果转化、技术服务、正式合同、受控交付、验收、结算和持续履约。各阶段分别记录，外部登记状态不反向覆盖平台治理事实。

## 跨阶段门

- 未通过前一阶段负面测试不得扩大能力。
- 所有阶段保持真实数据、临床、监管和认证表述边界。
- `hard_isolation=true` 只在 L4 独立验收后讨论。
- Phase 5.12 canonical 状态在 Connector 工程验证中使用隔离项目保护。

## Phase 5.13D accepted (2026-07-29)

- Signed control policy and independent Connector decision are complete.
- Final terminal set includes accepted, manual-rejected, automatic-rejected,
  and revoked-after-acceptance control orders.
- No execution, raw-data transfer, model transfer, or new Job/Run/Artifact.
- Phase 5.13E remains the next gated stage and has not started.
