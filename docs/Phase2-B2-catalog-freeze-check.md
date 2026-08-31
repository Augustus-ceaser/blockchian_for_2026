# MedTrust Space Phase 2-B.2.3-A Catalog ORM 前冻结检查

文档版本：v1.0  
日期：2026-07-22  
状态：通过；可进入 Catalog ORM + migration  
权威数据库基线：`docs/Phase2-database-design-v2.md` v0.2.1  
范围：`DataProduct`、`DataProductVersion`、`DataResource`、`DataProductSource`、`DataProductPublication`；本阶段不生成 ORM、migration、API 或前端代码。

## 1. 审查结论

Catalog 五表边界成立，不需要新增第六张策略模板表，也不需要回退到“产品直接对应文件/数据库”的模型。

复核发现两项 ORM 前冻结缺口，已同步修订到数据库设计 v2：

1. 增加同一产品内 `version_label` 唯一约束，避免两个版本同时显示为 `v1.0`。
2. 用复合候选键和复合外键闭合 Product、Version、Resource、Publication 以及 Application 的同空间关系，不把可由数据库低成本拒绝的跨空间错配全部留给业务代码。

父对象关系只建立一条复合 FK，不在同一父 ID 上再叠加单列 FK；否则 ORM 会看到两条重复关联路径。各表的 `space_id → spaces.id` 可以保留为直接租户引用。

同时修正一处上游表述冲突：`under_review` 版本不允许原地编辑；需要修改时必须先退回 `draft`，关闭本轮审查结论，再修改并重算摘要。

上述调整不改变五表边界、不新增表，也不改变 34 表总数。修订后没有阻塞 Catalog ORM 的领域问题。

## 2. 五个对象的冻结边界

| 对象 | 冻结职责 | 不承担的职责 |
|---|---|---|
| DataProduct | 产品的长期逻辑身份、所属空间、责任提供方和目录定位。 | 不保存当前版本，不保存真实资源，不直接授权使用。 |
| DataProductVersion | 某一时点产品内容、资源构成、质量、来源摘要和默认使用边界的可验证快照。 | 不表示目录当前发布状态；不保存患者行。 |
| DataResource | 版本内 WSI 集合、临床表、标注、随访等逻辑资源组件。 | 不是单个患者、单张切片、单个 DICOM 文件或对象存储文件。 |
| DataProductSource | DataResource 与 Connector 本地资源别名之间的来源映射。 | 不保存中央可访问路径、PACS 地址、访问凭据或密钥。 |
| DataProductPublication | 某个 approved Version 在目录中的发布事实。 | 不改变 Version 内容，不代替 Product 生命周期。 |

`DefaultPolicyTemplate` 继续作为 DataProductVersion 内的不可变 JSONB 值对象和摘要存在，不创建共享可变模板表。它是申请与合约协商的默认边界，不是最终授权，也不是可直接下发 Connector 的执行 Policy。

## 3. 删除策略

### 3.1 冻结规则

| 表 | 允许物理删除的唯一情形 | 其余情形 |
|---|---|---|
| `data_products` | 产品仍为 draft、从未创建 Version，且没有下游引用。 | 有任何 Version 后不得物理删除；使用 suspended、expired、archived。 |
| `data_product_versions` | 从未离开 draft、没有 ReviewTask/Publication/Application/ContractObject 引用；其 Resource/Source 可随草稿清理。 | under_review、approved、retired 或曾形成审查证据的版本不得物理删除。 |
| `data_resources` | 所属 Version 是可编辑 draft，且删除由版本编辑命令执行。 | under_review、approved、retired 版本内禁止删除；需要修改时先退回 draft。 |
| `product_sources` | 所属 Version 是可编辑 draft，且删除会触发 Resource/Version 摘要重算。 | under_review、approved、retired 版本内禁止删除。 |
| `data_product_publications` | 无。 | 业务 API 永不删除；active 只能转为 withdrawn 或 expired，历史保留。 |

### 3.2 数据库删除动作

- Product → Version 使用 `RESTRICT`。
- Version → Resource 的 `CASCADE` 只服务于“可删除草稿版本”的组成清理，不能被当作任意删除 Version 的授权。
- Resource → ProductSource 的 `CASCADE` 同样只服务于可编辑草稿清理。
- Publication 对 Product 和 Version 均使用 `RESTRICT`。
- “能否删除”先由领域命令判断；FK/CASCADE 只定义被允许删除后的结构行为。

不为五表统一增加 `deleted_at`。生命周期状态和不可变历史比通用软删除更准确。

## 4. 唯一约束

### 4.1 DataProduct

| 约束 | 决定 | 理由 |
|---|---|---|
| `(space_id, product_code)` | UNIQUE | 产品编码是空间内稳定业务标识。 |
| `(space_id, id)` | UNIQUE 候选键 | 支撑 Version 和 Publication 的同空间复合 FK。 |
| `(space_id, name)` | 不唯一 | 同名产品可由不同提供方或面向不同用途存在；名称不是稳定标识。 |

### 4.2 DataProductVersion

| 约束 | 决定 | 理由 |
|---|---|---|
| `(data_product_id, version_no)` | UNIQUE | 产品内单调技术版本号。 |
| `(data_product_id, version_label)` | UNIQUE | 防止两个不同版本都显示为 `v1.0`。 |
| `(data_product_id, id)` | UNIQUE 候选键 | Publication 验证 Version 确实属于 Product。 |
| `(space_id, id)` | UNIQUE 候选键 | Resource 和 Application 验证同空间引用。 |
| `(data_product_id, snapshot_digest)` | UNIQUE | 防止同一产品重复创建内容完全相同的版本。 |

`version_no` 用于排序和并发生成；`version_label` 用于人类展示。二者都唯一，但不要求从 label 反解析技术版本号。

### 4.3 DataResource

| 约束 | 决定 |
|---|---|
| `(data_product_version_id, resource_code)` | UNIQUE；资源编码在版本内稳定。 |
| `(data_product_version_id, position_no)` | UNIQUE；保证稳定展示和摘要排序。 |
| `(data_product_version_id, id)` | UNIQUE 候选键；供来源和未来同父对象约束使用。 |

### 4.4 DataProductSource

- 主键：`(data_resource_id, connector_id, local_resource_alias)`。
- 同一 Connector 可以为同一 Resource 暴露多个明确不同的本地别名；同一别名不能重复登记。
- `source_digest` 不做全局唯一，因为同一来源快照可被不同产品版本合法复用。

### 4.5 DataProductPublication

- 部分 UNIQUE：每个 `data_product_id` 最多一个 `status='active'` 的 Publication。
- 部分 UNIQUE：每个 `data_product_version_id` 最多一个 `status='active'` 的 Publication。
- 非 active 历史记录允许多条，以保留撤回、重新发布和目录可见性变更的证据。

## 5. 状态机

### 5.1 DataProduct

```text
draft → active → suspended → active
              └→ expired → archived
draft ───────────────────→ archived
```

- `active` 只说明产品逻辑身份有效。
- 产品是否当前可申请，必须同时存在 active Publication。
- suspended/expired/archived 不回写或删除历史 Version、Application、Contract 和 Compute 证据。

### 5.2 DataProductVersion

```text
draft → under_review → approved → retired
            └───────→ draft
```

- 不采用 `ACTIVE`：可发现和可申请由 Publication 的 active 表达。
- 不采用长期 `REJECTED`：拒绝事实保存在 ReviewTask/ReviewRecord；版本返回 draft 修改或放弃。
- draft → under_review 前必须形成 Source、Resource、Policy 和 Version 全部摘要。
- under_review 内容锁定；要求修改时先回到 draft，使原摘要和本轮审查结论失效。
- approved 后只允许 approved → retired，内容永久冻结。

### 5.3 DataResource 与 DataProductSource

不建立独立状态机，生命周期从属于 DataProductVersion。单独增加 status 会造成“版本 approved 但资源 draft”之类的组合爆炸。

### 5.4 DataProductPublication

```text
[*] → active → withdrawn
             └→ expired
```

- 仅 approved Version 可以创建 active Publication。
- 切换目录版本必须在同一事务中撤回旧 active Publication，并创建/激活新 Publication。
- 撤回 Publication 不撤销已签署合约；后续使用由 Contract 状态和执行 Policy 决定。

## 6. JSONB 字段边界

### 6.1 允许放入 JSONB

| 所属对象 | JSONB 字段 | 内容边界 |
|---|---|---|
| Version | `scope_metadata` | 病种、时间段、规模、人群汇总，不含个体记录。 |
| Version | `linkage_metadata` | 多模态匿名关联方法和覆盖率，不含患者标识或映射表。 |
| Version | `quality_report` | 版本级质量汇总、适用范围、偏倚说明。 |
| Version | `default_policy_template` | 默认允许、禁止、义务、环境和输出规则的规范化快照。 |
| Version | `provenance_summary` | 来源、治理、去标识化和版本形成摘要。 |
| Resource | `schema_metadata` | 字段/标签/单位/编码体系和结构描述。 |
| Resource | `scope_metadata` | 资源级规模、时间和覆盖范围。 |
| Resource | `quality_report` | 资源级完整性、缺失、图像质量和标注一致性。 |

每类 JSON 文档必须有命名的应用层 JSON Schema 和显式 `schema_version`；进入 under_review 前完成结构校验和规范化，再计算 digest。不能把任意前端对象原样落库。

### 6.2 必须关系化

以下内容不得为了省表而压入 JSONB：

- 主体、空间、产品、版本、资源、Connector 的 ID 和关系；
- 产品编码、版本号、版本标签、资源编码和展示顺序；
- 生命周期状态、产品类型、领域、资源类型、模态、格式、分类分级和默认使用模式；
- Publication 的状态、可见性和时间；
- 所有摘要、来源角色和来源快照时间；
- Application、Contract 和 Compute 的标的引用。

### 6.3 禁止保存

Catalog 中禁止保存：

- 患者级明细、患者 ID 映射或可重新识别的行；
- 真实 WSI/PACS/文件系统路径；
- 对象存储访问密钥、Connector 私钥、数据库凭据；
- 可以绕过 Connector 和 Contract Policy 直接访问资源的地址。

## 7. 不可变字段

### 7.1 永久身份字段

- 所有表的 `id` 或复合主键。
- Product 的 `space_id`、`is_demo`。
- Version 的 `space_id`、`data_product_id`、`version_no`。
- Resource 的 `space_id`、`data_product_version_id`。
- Source 的 `data_resource_id`、`connector_id`、`local_resource_alias`。
- Publication 的 `space_id`、`data_product_id`、`data_product_version_id`、发布人和发布时间。

### 7.2 条件冻结

- Product 在首个 Version 创建后，`product_code`、`provider_organization_id`、`product_type` 和 `domain` 冻结；展示名称和非版本化说明可审计修改。
- Version 进入 under_review 时锁定全部内容。退回 draft 后才允许修改，且必须重新计算全部下游摘要。
- Version approved 后，Version、Resource、Source、默认策略和全部摘要永久冻结；retired 也不解冻。
- Publication active 后不原地替换产品、版本或可见性；可见性变化使用撤回并新建发布记录，保留历史。

### 7.3 实现层防御

领域服务是主控制点，禁止开放通用 `PATCH status` 或任意 Repository update。数据库侧至少通过以下方式做纵深防御：

1. Catalog 写入使用受限数据库角色；
2. 迁移中定义 CHECK、UNIQUE、FK 和部分唯一索引；
3. 为 approved/retired Version 及其组成对象增加不可变性测试；
4. 若数据库权限不足以表达行状态保护，再增加小范围触发器，不预先建立通用触发器框架。

## 8. 与 Connector 的关系

冻结链路为：

```text
DataProductVersion
  → DataResource
    → DataProductSource
      → Connector
```

Catalog ORM 和后续领域服务必须验证：

1. Connector、Resource、Version、Product 属于同一 Space；
2. Connector owner 默认等于 Product provider organization；联合产品允许其他已准入 provider，但必须有联合授权证据；
3. Connector 处于可接受的 verification/runtime 状态；
4. Connector 具备产品封装、元数据同步或后续策略执行所需能力；
5. 平台只保存 `local_resource_alias` 和摘要，真实资源由 Connector 解析。

前三层父子空间关系已用复合 FK 约束。ProductSource 没有冗余 `space_id`，因此 Source ↔ Connector 同空间、参与资格、状态和能力由领域服务在同一事务中校验。V1 不为此新增一列和第二套空间一致性写入点。

## 9. 下游固定版本引用

| 下游对象 | 对 Catalog 的引用 | 摘要证据 | 冻结决定 |
|---|---|---|---|
| Application | 直接引用 `data_product_version_id` | `requested_product_snapshot_digest` | 提交时必须匹配 Version.snapshot_digest，并存在 active Publication。 |
| ContractObject | 直接引用 `data_product_version_id` | `product_snapshot_digest` | 合约签署后标的和摘要不可变；不得引用“最新版本”。 |
| ComputeJob | 不直接重复保存 Version ID | 通过同一 ContractRevision 的 ContractObject 固定 Version；JobInput 保存 `input_snapshot_digest` | 计算依据签署合约，而不是重新读取目录当前版本。 |

Publication 后续 withdrawn/expired 不改变 Application、ContractObject 和历史 Compute 所固定的 Version。是否允许新的任务启动由 active ContractRevision、Policy 和 Connector 状态共同决定，不能只看目录。

## 10. 数据库约束与领域服务分工

| 规则 | 数据库负责 | 领域服务负责 |
|---|---|---|
| 产品、版本、资源同空间 | 复合 FK。 | 错误语义和审计事件。 |
| Publication 的 Version 属于 Product | `(data_product_id, data_product_version_id)` 复合 FK。 | 仅 approved 可发布；原子切换 active Publication。 |
| 每个 Product/Version 最多一个 active Publication | 部分唯一索引。 | 并发冲突转为明确业务错误。 |
| Version 内容不可变 | 基础约束、权限及必要触发器。 | 状态命令、摘要重算和审查撤销。 |
| Source Connector 同空间且有资格 | FK 只保证 Connector 存在。 | 同空间、participant、provider、状态、能力和联合授权校验。 |
| Product provider 有空间 provider 资格 | FK 只保证 Organization 存在。 | SpaceParticipant admitted + provider 角色校验。 |
| Application/Contract/Compute 使用固定版本 | FK 和摘要字段。 | 提交、签约、运行前核对摘要与状态。 |

复合 FK 已经承担父对象引用时，不再为同一个父 ID 建立重复单列 FK。这样既保证同空间，又避免 ORM relationship 需要在两条等价路径中选择。

## 11. ORM 与 migration 验收门槛

进入 Phase 2-B.2.3 实现后，至少验证：

- [ ] 仅新增 Catalog 五张表，Alembic head 从 `20260722_0003_connectors` 单步前进。
- [ ] migration upgrade/downgrade 对称，且不修改 Identity、Spaces、Connectors 已有表的业务语义。
- [ ] 五表全部使用 SQLAlchemy 2.0 typed declarative mapping。
- [ ] 同产品重复 product_code、version_no 或 version_label 被数据库拒绝。
- [ ] 跨空间 Product → Version、Version → Resource、Product → Publication 和 Version → Application 被数据库拒绝。
- [ ] 一个 Product 或 Version 的第二条 active Publication 被部分唯一索引拒绝。
- [ ] Publication 不能把 Product A 与 Product B 的 Version 错配。
- [ ] under_review 版本不能原地修改；退回 draft 后重算摘要才可重新提交。
- [ ] approved/retired Version、Resource、Source、默认策略和摘要不能修改或删除。
- [ ] 非 draft Version 不能通过级联删除 Resource/Source。
- [ ] Source 绑定跨空间、未核验或无能力 Connector 时，领域服务拒绝并产生审计事件接口契约。
- [ ] JSONB 通过版本化 schema 校验；患者行、真实路径和密钥不进入 Catalog。
- [ ] SQLite 快速测试不替代 PostgreSQL 16 对 JSONB、部分索引、复合 FK、事务并发和迁移锁的集成测试。
- [ ] 不实现 Application、Contract、Compute、Audit CRUD；只为未来外键保留模型边界。

## 12. 本次同步修订

已更新：

- `docs/Phase2-database-design-v2.md`：版本提升至 v0.2.1，新增 Catalog ORM 前冻结补充、`version_label` 唯一约束及同空间复合 FK。
- `docs/Phase2-B2-catalog-model.md`：统一 under_review 的编辑规则为“先退回 draft，再修改并重算摘要”。

未改动：

- Catalog 五表数量和对象边界；
- 34 表总数；
- Application、ContractObject、ComputeJobInput 的固定版本链路；
- ORM、Alembic、API、前端和 Mock 数据。

## 13. Go / No-Go

结论：**Go**。

Catalog 五表已满足进入 ORM + migration 的前置条件。下一阶段必须继续按五表范围分批实现，先完成模型、迁移和不变量测试，再评审是否进入 Application 域；不得顺带生成通用 CRUD、Application、Contract、Compute 或 Audit 代码。
