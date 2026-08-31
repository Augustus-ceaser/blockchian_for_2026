# Phase 2-B.4-D Review 编排实现冻结

状态：**冻结完成；允许进入 Contract 领域设计，不允许据此宣称 Review 编排或 Contract 已实现。**

本文件冻结 Review 编排下一步实现所需的算法、摘要、事务和领域边界。它修订并收敛 [Phase2-B4-review-orchestration-model.md](Phase2-B4-review-orchestration-model.md) 中仍未确定的部分，但不修改现有 ORM、migration、API 或数据库。

## 1. 审查结论

### 1.1 本阶段通过的内容

- `ReviewDecision` 和结构不可变的 `ReviewTask` 继续作为审核事实源；
- `ReviewRequirement` 是规则派生值，不新增表；
- `ReviewPlan` 由一个 ApplicationSnapshot 的 ReviewTask 结构集合物化，不新增表；
- `ApplicationReviewSummary` 是可重建投影，不新增表，也不成为 Contract 的权威输入；
- Contract Draft 只接收完整正向审核证据，不因单个 approved Decision 获得准入；
- V1 不新增 Review 表、列或索引。

### 1.2 对原建议的两项修正

第一，不在本阶段给 Application 直接增加：

```text
under_review
approved_for_contract
contracting
completed
terminated
```

现有 `prechecking/provider_review` 已表达审核过程，`approved` 已表达“允许进入 Contract 协商”，但不表达访问授权。`contracting/completed/terminated` 属于 Contract 或治理生命周期，直接写回 Application 会重新耦合领域，并要求重做 CHECK、时间线和终态保护。

第二，不把 `administrative_termination` 映射成 Review rejected。管理性终止没有形成审核结论，不得伪造 rejected Decision。

### 1.3 条件性 Go

可以进入 Contract **领域设计**。

Contract ORM、Contract Draft 创建服务或生产级准入实现仍有一个前置条件：必须能够证明审核计划没有漏掉当时规则要求的条件审核。V1 通过不可变的 `review-orchestration-v1` / `demo-v1` 规则实现和黄金向量满足演示验证；规则一旦演进，必须先增加不可变 `review.plan_created` Audit/outbox 证据或等价计划证据，不能只依赖当前 Task 集合自证完整。

---

## 2. 权威事实与投影

### 2.1 权威事实

```text
ApplicationSnapshot
  + ReviewTask structural fields
  + ReviewDecision
  + Application lifecycle status
```

其中：

- Snapshot 固定申请内容；
- Task 固定当时创建的审核路线；
- Decision 固定最终审核决定；
- Application 状态表达申请生命周期，不表达 Contract 状态。

### 2.2 非权威投影

以下内容可缓存或重建，但不能作为准入事实：

- `applications.decision_summary`；
- `ApplicationReviewSummary`；
- 待办数量；
- 当前开放 sequence；
- 人类可读的审核结论文本；
- 前端显示的 `approved_for_contract` 标签。

即使 `decision_summary` 被直接改成“全部通过”，Contract eligibility 仍必须从 Snapshot、Task 和 Decision 重算。

### 2.3 Requirement 的正确保存边界

`ReviewRequirement` 本身不建表，但“规则结果不建表”不等于“历史计划可以随当前规则重算并改写”。

计划创建后：

- 已创建 Task 的结构字段永久不变；
- 每项 `routing_rule_digest` 永久固定；
- 旧规则不得重新解释旧 Snapshot；
- 新规则只能产生新的 orchestration version，不能修改 `review-orchestration-v1` 的含义。

---

## 3. 统一 canonical JSON 规则

Plan、routing、eligibility 三类新摘要复用现有 ApplicationSnapshot 的规范化方式：

```python
json.dumps(
    document,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
```

冻结规则：

1. 对象键按 Unicode code point 的 Python `sort_keys=True` 结果排序；
2. 数组在序列化前按各自明确的业务键排序；
3. UUID 使用小写、带连字符的标准字符串；
4. digest 使用 `sha256:<64 lowercase hex>`；
5. JSON 布尔值使用 `true/false`，不使用字符串；
6. 禁止 NaN 和 Infinity；
7. 不把运行时计算时间放入可重算摘要；
8. V1 不额外引入 Unicode NFC 转换，以免与已落库 Snapshot 形成第二套规范。

摘要函数：

```text
digest(document)
  = "sha256:" + lowercase_hex(
      SHA256(canonical_json(document))
    )
```

---

## 4. ReviewRequirement 派生冻结

### 4.1 固定版本

```text
orchestration_algorithm = review-orchestration-v1
route_config_version    = demo-v1
```

两者含义一旦发布不得原地修改。规则变化必须发布新版本，例如 `review-orchestration-v2`。

### 4.2 派生输入分层

#### 计划事实输入

这些输入参与 Requirement 和 routing digest：

- ApplicationSnapshot digest；
- Snapshot 内 Action、Requested Output、附件摘要；
- Snapshot 固定的 DataProductVersion 和产品策略摘要；
- 数据分类/敏感等级；
- `space_ruleset_version`；
- `route_config_version`；
- 触发事实代码；
- review type、sequence、required 标志；
- 责任组织解析结果。

#### 当前状态守卫

这些内容在计划创建时校验，但不进入历史计划摘要：

- Space 当前是否 active；
- 组织当前是否 active；
- SpaceParticipant 当前是否 admitted；
- 成员和上下文能力当前是否有效；
- 职责分离当前是否可执行。

它们会变化，因此必须在领取、决定和 Contract handoff 时重新校验，不能让变化后的状态改写旧计划身份。

### 4.3 V1 requirement 词表

| review type | sequence | 触发 | 责任组织 |
| --- | ---: | --- | --- |
| `application_precheck` | 10 | 始终 | Space operator。 |
| `provider_review` | 20 | 始终 | Application provider。 |
| `compliance_review` | 20 | 条件触发 | 已配置的合规责任参与组织。 |
| `ethics_review` | 20 | 条件触发 | 已配置的伦理责任参与组织。 |

V1 所有已生成 Requirement 均为 `is_required=true`。首期不生成 optional Task；否则 Summary 和 Contract eligibility 还需要另一套可选任务语义。

### 4.4 routing rule digest

沿用 v4 已冻结格式，`trigger_facts` 在序列化前按代码升序排列：

```json
{
  "schema_version": "1.0",
  "space_id": "uuid",
  "space_ruleset_version": "string",
  "application_snapshot_digest": "sha256:<64hex>",
  "review_type": "ethics_review",
  "sequence_no": 20,
  "is_required": true,
  "trigger_facts": ["high_sensitivity", "model_artifact"],
  "assignee_organization_id": "uuid",
  "route_config_version": "demo-v1"
}
```

每个 Task 保存该文档摘要，不保存规则表达式或患者数据。

---

## 5. ReviewPlan digest 冻结

### 5.1 结构来源

```text
ReviewPlan(Snapshot S)
  = all ReviewTasks where application_snapshot_id = S.id
```

Plan 只读取 Task 结构字段，不读取状态、领取用户、Decision 或时间戳。

### 5.2 canonical plan document

```json
{
  "schema_version": "1.0",
  "orchestration_algorithm": "review-orchestration-v1",
  "route_config_version": "demo-v1",
  "space_id": "uuid",
  "application_id": "uuid",
  "application_snapshot_id": "uuid",
  "target_digest": "sha256:<64hex>",
  "requirements": [
    {
      "review_type": "application_precheck",
      "sequence_no": 10,
      "is_required": true,
      "assignee_organization_id": "uuid",
      "routing_rule_digest": "sha256:<64hex>"
    }
  ]
}
```

`requirements` 排序键：

```text
(sequence_no, review_type, assignee_organization_id, routing_rule_digest)
```

摘要：

```text
review_plan_digest = digest(canonical plan document)
```

### 5.3 明确排除的字段

- ReviewTask ID；
- Task status；
- assignee user；
- due/claimed/decided/cancelled 时间；
- cancel reason；
- ReviewDecision；
- 组织名称和页面标签；
- `computed_at`。

Task ID 不参与 Plan digest，使原子创建前可以先计算预期摘要，也避免数据库随机 UUID 改变相同结构计划的身份。

### 5.4 完整性限制

Plan digest 能证明“这组 Task 的结构没有变化”，不能单独证明“规则本应要求的 Task 一项未漏”。

V1 完整性判定必须同时满足：

1. 使用不可变 `review-orchestration-v1/demo-v1` 派生 expected requirements；
2. expected requirement keys 与已落库 Task keys 完全相等；
3. 每项 routing digest 重算一致；
4. 不存在多余、缺失或冲突 Task；
5. 黄金向量测试固定 v1 规则实现。

规则版本演进前必须新增不可变 `review.plan_created` 证据，至少固定 expected requirement keys、plan digest、orchestration version 和 route config version。

---

## 6. ApplicationReviewSummary 冻结

### 6.1 计算输入

同一数据库一致性快照读取：

- Application；
- 唯一 ApplicationSnapshot；
- 该 Snapshot 全部 ReviewTask；
- Task 对应零或一条 ReviewDecision。

### 6.2 outcome

```text
not_started
in_review
approved_for_contract
blocked
withdrawn
```

### 6.3 判定优先级

1. Application `withdrawn` → `withdrawn`；
2. Snapshot 缺失或 digest 不一致 → `blocked`；
3. 计划缺失 → `not_started`；
4. 计划不完整或冲突 → `blocked`；
5. 任一 required Decision rejected → `blocked`；
6. 任一 required Task cancelled → `blocked`；
7. 存在 pending/claimed Task → `in_review`；
8. 全部 required Task decided 且 Decision approved → `approved_for_contract`；
9. 其他组合 → `blocked`并返回一致性错误码。

### 6.4 projection metadata

Summary 可返回：

```text
computed_at
pending_count
claimed_count
approved_count
rejected_count
cancelled_count
blocker_codes[]
```

这些字段用于观察和展示，不进入 Plan digest 或 eligibility digest。

---

## 7. 行政终止与领域归属

### 7.1 申请方撤回

```text
Application -> withdrawn
open ReviewTasks -> cancelled(application_withdrawn)
decided ReviewTasks -> 保留不变
Summary -> withdrawn
Contract eligibility -> false
```

### 7.2 上游拒绝

```text
authoritative ReviewDecision -> rejected
Application -> rejected
remaining open ReviewTasks -> cancelled(upstream_rejected)
already decided ReviewTasks -> 保留不变
Summary -> blocked
Contract eligibility -> false
```

### 7.3 审核过程中的管理性终止

`administrative_termination` 只允许特权治理命令用于 pending/claimed Task：

```text
open ReviewTasks -> cancelled(administrative_termination)
ReviewDecision -> 不创建
Application -> 保持当前 prechecking/provider_review
Summary -> blocked(administrative_termination)
Contract eligibility -> false
same Snapshot -> 不允许恢复或重建同类Task
```

当前 Application 状态保持原审核阶段是 V1 的保守做法：它不伪造 rejected，也不在本轮无迁移评审时发明新终态。UI 必须以 Summary 的治理阻断投影禁用继续操作。

该方案不是生产级治理状态终点。生产前应设计独立 GovernanceCase/AuditEvent 或经冻结的新 Application 治理状态。

### 7.4 审核批准后的合作终止

审核批准后：

- ReviewTask 已 decided，数据库和领域服务均禁止取消；
- Application/Review 作为历史准入证据保持不变；
- 未签约时停止协商属于未来 Contract Draft/negotiation 生命周期；
- 已生效后停止合作属于未来 Contract `suspended/terminated`；
- 不反向修改 ReviewDecision，不把 Application 改写成 Review rejected。

因此，“合作终止”与“审核拒绝”是两个不同事实。

---

## 8. Eligibility Evidence Bundle 冻结

### 8.1 稳定证据内容

参与摘要的 canonical document：

```json
{
  "schema_version": "1.0",
  "eligibility_algorithm": "contract-eligibility-v1",
  "orchestration_algorithm": "review-orchestration-v1",
  "space_id": "uuid",
  "application_id": "uuid",
  "application_snapshot_id": "uuid",
  "snapshot_digest": "sha256:<64hex>",
  "application_status": "approved",
  "review_plan_digest": "sha256:<64hex>",
  "required_decisions": [
    {
      "review_task_id": "uuid",
      "review_type": "provider_review",
      "sequence_no": 20,
      "assignee_organization_id": "uuid",
      "target_digest": "sha256:<64hex>",
      "decision": "approved",
      "decision_digest": "sha256:<64hex>"
    }
  ],
  "outcome": "approved_for_contract"
}
```

`required_decisions` 排序键：

```text
(sequence_no, review_type, review_task_id)
```

摘要：

```text
eligibility_digest = digest(stable eligibility evidence content)
```

### 8.2 运行时信封

返回给调用方时可以包一层不参与摘要的元数据：

```json
{
  "eligibility_digest": "sha256:<64hex>",
  "evidence": {"...": "stable content above"},
  "projection_metadata": {
    "computed_at": "UTC ISO-8601"
  }
}
```

### 8.3 对 Phase 32 的修正

Phase 32 把 `computed_at` 放入了整个证据包摘要输入。该设计会让同一事实每次重算产生不同 digest，本冻结文件明确废止该做法。

`decided_at` 已经包含在既有 `ReviewDecision.decision_digest` 中，Eligibility 不再重复把它作为摘要字段。它可以作为页面或审计展开信息，但不进入第二层 digest。

### 8.4 明确不包含

- 数据或对象存储路径；
- MinIO storage key；
- Connector 凭据；
- 用户会话/JWT；
- Compute 运行令牌；
- 原始医疗数据；
- Contract 签署或生效状态；
- 当前页面角色；
- `computed_at` 等运行时字段。

---

## 9. Contract Draft 准入校验

### 9.1 两层校验

#### A. 不可变审核证据校验

全部条件必须为 true：

```text
Application.status == approved
AND Snapshot exists
AND Snapshot digest matches every Task target_digest
AND expected requirements exactly equal materialized Task structure
AND review_plan_digest recomputes identically
AND every required Task.status == decided
AND every required Task has exactly one Decision
AND every required Decision.decision == approved
AND every Decision digest is valid
AND no required Task is cancelled/pending/claimed
AND eligibility algorithm version is recognized
```

#### B. 当前 handoff 守卫

创建 Contract Draft 当下还必须重新校验：

- Space 仍 active；
- applicant/provider 组织仍 active；
-双方仍是 admitted SpaceParticipant；
-目标 DataProductVersion 仍允许进入协商；
-不存在管理性阻断；
-同一 Application 尚无 Contract 系列；
-幂等键没有对应的冲突 Draft。

当前状态守卫不进入历史 eligibility digest。Contract 领域应在创建 revision 时固定其实际采用的当前状态证据。

### 9.2 正向证明原则

以下条件都不足以创建 Contract Draft：

- 找不到 rejected Decision；
- 有一个 approved Decision；
- Application `decision_summary` 显示通过；
- ReviewTask 全部不在 pending；
- 前端角色是管理员；
- 一个旧缓存 Summary 显示 eligible。

必须得到完整、无冲突、可重算的正向证据。

### 9.3 输出边界

准入校验成功只返回：

```text
eligible_to_create_contract_draft = true
```

它不产生：

- 数据访问权；
- Connector 命令；
- Compute 权限；
- Artifact 出域授权；
- 已签署或 active Contract。

---

## 10. 服务边界冻结

### 10.1 纯函数

```text
derive_review_requirements(snapshot, frozen_ruleset)
canonical_routing_rule_document(requirement)
canonical_review_plan(tasks, orchestration_version)
compute_application_review_summary(application, snapshot, tasks, decisions)
build_eligibility_evidence(application, snapshot, tasks, decisions)
```

纯函数不得读前端缓存、`decision_summary` 或当前用户会话。

### 10.2 事务命令

```text
start_application_review
claim_review_task_in_context
release_review_task_in_context
submit_review_decision_and_aggregate
withdraw_application_and_cancel_open_reviews
administratively_terminate_open_reviews
validate_contract_draft_handoff
```

### 10.3 与当前代码的差距

| 当前能力 | 仍缺能力 |
| --- | --- |
| `claim_review_task` 修改单个Task内存状态 | sequence、成员资格、职责分离、行锁和乐观并发。 |
| `submit_review_decision` 插入Decision并关闭Task | 锁Application/全部Task、汇总、推进Application、取消下游Task。 |
| Task/Decision数据库保护 | Requirement派生、原子计划生成、Plan/Eligibility digest。 |
| `decision_summary`列 | 非权威投影更新器。 |
| `routing_rule_digest`列 | 不可变版本化路由实现和plan-created存证。 |

本文件不能被解释为上述缺失能力已经实现。

---

## 11. 事务与并发冻结

### 11.1 计划创建锁顺序

```text
Application FOR UPDATE
  -> ApplicationSnapshot
  -> existing ReviewTasks ordered by sequence_no, id
  -> create all expected ReviewTasks atomically
  -> Application submitted -> prechecking
```

### 11.2 Decision与汇总锁顺序

```text
Application FOR UPDATE
  -> ApplicationSnapshot
  -> ReviewTasks FOR UPDATE ordered by sequence_no, id
  -> insert ReviewDecision
  -> Task claimed -> decided
  -> recompute Summary
  -> update Application projection/status
  -> cancel remaining open Tasks when required
```

所有步骤在一个事务提交。不得先返回 approved，再异步补写 Summary。

### 11.3 Contract handoff

Contract Draft创建事务必须重新执行 eligibility 和 current handoff guards，并以 Application 作为首锁，防止两个并发请求创建两个 Contract 系列。

---

## 12. 数据库补充结论

### 12.1 本阶段不新增

- 不新增 `review_requirements`；
- 不新增 `review_plans`；
- 不新增 `application_review_summaries`；
- 不新增 eligibility 表；
- 不新增 Review 状态；
- 不新增 migration。

### 12.2 索引判断

当前已有：

- application_id 索引；
- application_snapshot_id 索引；
- space/status/sequence 复合索引；
- assignee organization/status 复合索引；
- routing digest 索引。

V1 每个申请最多四类 Task，汇总查询基数很小。没有实测证据支持再增加专用汇总索引。

### 12.3 未来必须评审的持久化证据

规则演进或生产审计前，需要在 Audit/outbox 阶段评审不可变：

```text
review.plan_created
  snapshot_digest
  orchestration_algorithm
  route_config_version
  expected_requirement_keys[]
  review_plan_digest
```

它不是 Summary，也不是第二套审核结论；它证明计划创建时完整要求集合是什么。

---

## 13. 验收矩阵

### 13.1 摘要黄金向量

- 相同Plan结构、不同Task插入顺序 → plan digest相同；
- 相同Plan结构、不同Task ID → plan digest相同；
- Task领取/释放/决定 → plan digest不变；
- 修改责任组织、sequence或routing digest → plan digest变化；
- 相同审核事实、不同Summary计算时间 → eligibility digest相同；
- required Decision顺序变化 → eligibility digest相同；
-任一Decision digest变化 → eligibility digest变化。

### 13.2 完整性

-缺少provider review → plan incomplete；
-规则要求ethics但Task缺失 → plan incomplete；
-多出未知Task → plan conflict；
-routing digest不匹配 → plan conflict；
-部分计划生成失败 → 回滚全部Task，Application保持submitted。

### 13.3 汇总与准入

-单个approved → 不准入；
-全部required approved → approved_for_contract；
-任一required rejected → blocked；
-任一required cancelled → blocked；
-篡改decision_summary → eligibility不变；
-administrative termination → blocked且无伪造Decision；
-Application withdrawn → withdrawn且不准入；
-Space/参与资格在审核后失效 →历史eligibility evidence仍可重算，但当前Contract handoff失败。

### 13.4 并发

-并发启动审核只产生一套Task；
-部分Task集合不能被重试静默补齐；
-并发领取只允许一个用户成功；
-并发提交Decision只允许一条；
-并发创建Contract Draft只允许一个Contract系列。

---

## 14. 下一阶段入口

下一阶段应进入 **Contract 领域设计**，而不是直接写 Contract ORM。

Contract设计必须回答：

1. 如何固定 eligibility evidence及digest；
2. Contract与ContractRevision的边界；
3. Application/Review范围如何只能收窄不能扩大；
4. 双方协商、签署、active、suspended、terminated、expired如何分离；
5. 可执行Policy如何由签署revision产生；
6. 哪些current handoff guards由Contract创建事务固定；
7. 合同终止为何不反向修改Application或Review历史。

在Contract领域设计通过前，不生成Contract migration、API、Connector命令或Compute授权。

---

## 15. 最终冻结结论

```text
ApplicationSnapshot = 申请证据
ReviewTask           = 审核计划事实
ReviewDecision       = 审核决定事实
Summary              = 可重建投影
Eligibility Evidence = Contract Draft准入证明
Contract             = 后续可执行约束来源
```

Review通过不等于数据授权，Application approved不等于Contract active，管理性终止不等于审核拒绝。

Phase 2-B.4-D冻结后可以进入Contract领域设计；完整Review编排、职责分离授权服务、plan-created存证和Contract handoff仍未实现。
