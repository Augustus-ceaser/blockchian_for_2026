# Phase 5.13 目标架构

## 三个控制域

```mermaid
flowchart LR
  subgraph C["中央协作与治理平面"]
    CI["身份与组织"]
    CC["目录与产品版本"]
    CG["治理和质量证据注册"]
    CS["研究方案与申请"]
    CP["合约与 Policy Compiler"]
    CO["任务编排和 Connector Registry"]
    CE["Evidence Registry 与审计验证"]
    CM["运营监控与外部登记适配"]
  end
  subgraph H["医院控制域"]
    HI["医院 IAM 与审批"]
    HC["Hospital Connector、身份、证书和密钥"]
    HA["本地资产注册与质量画像"]
    HV["PolicyBundle Validator 与 Local Task Manager"]
    HX["隔离 Executor"]
    HQ["本地 Artifact 隔离与 Output Scanner"]
    HE["医院出域审核与 EvidenceBundle"]
    HL["本地审计账本"]
  end
  subgraph R["需求方/模型方控制域"]
    RQ["Research Question"]
    RP["StudyProtocol 与 AnalysisPlan"]
    RM["模型或算法提交"]
    RW["获批结果工作区与 EvidencePackage Viewer"]
  end
  RQ --> RP --> CS
  RM --> CS
  CC --> CS
  CG --> CP
  CS --> CP --> CO
  CO -->|"签名 PolicyBundle / ExecutionOrder"| HC
  HI --> HC
  HA --> HV
  HC --> HV --> HX --> HQ --> HE
  HE -->|"获批 EvidenceBundle"| CE --> RW
  HC -. "拒绝/过期/撤销/能力不足" .-> CO
  CE --> CM
  HL -. "审计头和摘要" .-> CE
```

## 流的定义

- **控制流**：申请与协议冻结后，中央编译 PolicyBundle，并引用它签发 ExecutionOrder；Connector 独立验签、查撤销和本地审批。
- **数据流**：原始数据只在医院存储到本地 Executor；中央不接收原始数据、患者级中间结果或本地路径。
- **证据流**：治理、质量、协议、环境、执行回执、输出审核和医院审计头形成 EvidenceBundle，获批后进入中央 Evidence Registry。
- **撤销流**：组织、合约、证书、Connector、PolicyBundle 或本地审批撤销均传播为拒绝；已签发未执行订单失效。
- **错误与拒绝流**：签名、摘要、版本、资源、质量、审批、安全姿态或能力不满足时，Connector 返回稳定错误码，不回传敏感内部细节。

## 架构不变量

1. 中央不是医院 Connector 的超级管理员，不能绕过本地审批和出域门。
2. PolicyBundle 是政策权威；ExecutionOrder 只能引用，不能放宽。
3. 输入默认只读，运行默认禁网，输出默认拒绝，例外必须显式列入策略。
4. 本地 Artifact 在医院批准前不出域；中央只能看到状态和非敏感摘要。
5. 双侧审计独立存在，任一侧链断裂都阻断正式证据交付。
6. Phase 5.13B 仅实现节点身份、注册、证书骨架、心跳、能力、暂停和撤销，不传数据、不传模型、不执行任务。

