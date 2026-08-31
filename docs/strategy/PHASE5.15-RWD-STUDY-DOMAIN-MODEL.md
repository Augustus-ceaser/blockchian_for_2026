# Phase 5.15 RWD 研究领域模型

## 正式链

```text
ResearchStudy
-> ProtocolVersion
-> SiteFeasibility
-> Application
-> Contract
-> PolicyBundle
-> SiteExecutionOrder
-> LocalRun
-> SiteEvidenceBundle
-> AggregateAnalysis
-> StudyEvidencePackage
```

StudyProtocol 与 AnalysisPlan 必须在 ComputeJob 或 ExecutionOrder 前冻结。

## 协议最低内容

| 领域 | 必需定义 |
|---|---|
| 问题与估计 | 研究问题、目标人群、时间零点、目标估计量、随访、删失 |
| 变量 | 暴露、对照、结局、协变量、变量字典、验证证据 |
| 设计 | 队列、纳排、样本量、中心差异、亚组、多重比较 |
| 偏倚 | 混杂、未测量混杂、缺失机制、阴性对照、敏感性分析 |
| 治理 | 伦理、合法依据、用途、保存期限、地区规则和撤销 |
| 可复现 | 数据/代码/环境摘要、统计方法、输出 schema |
| 解释 | 限制、质量适用性、方法学审核、协议偏离 |

## 状态与不变量

ResearchStudy：draft/active/suspended/closed。ProtocolVersion：draft/under_review/approved/frozen/superseded/withdrawn。approved 后须显式 freeze；任何实质变更创建新版本。SiteFeasibility 不能替代医院审批；站点执行只能引用同一冻结协议和站点适用的质量/治理证据。

AggregateAnalysis 只消费获批 SiteEvidenceBundle，不消费医院原始数据。StudyEvidencePackage 保留站点范围、版本、偏离、质量限制、分析摘要和审计证明，不得把观察性关联写成疗效证明。

