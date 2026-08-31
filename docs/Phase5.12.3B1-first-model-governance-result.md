# Phase 5.12.3B1 首批公共模型真实证据治理结果

## 1. 结论

`Phase 5.12.3B1 accepted = true`。

本阶段从 16 条既有候选中审核 8 条主候选，保留 4 条备选。通过
`operator.demo` 正式 API 写入 96 条 append-only Review，并重算 16 条治理画像。
最终有 3 条进入 `eligible_for_model_draft`。该状态只允许后续创建受许可证和用途
限制的 metadata-only ModelProduct 草稿，不表示权重已取得、可执行、兼容、可商用
或可用于临床。

## 2. 候选构成

| 方向 | 主候选 |
| --- | --- |
| 病理基础模型 | CONCH、UNI、Prov-GigaPath |
| 病理与组学 | ST-Net、DeepPT |
| 细胞与组织结构 | CellViT、HoVer-Net |
| 医学影像分割 | PraNet |

备选但未审核：Phikon、PLIP、iStar、MedSAM。未入选的 8 条模型均保持 0 条正式
Review。没有启用备选，没有新增、合并或删除 ExternalModelRecord。

## 3. 来源、论文和仓库

- official source confirmed：8
- official paper confirmed：7；preprint only：1（PraNet）
- official repository confirmed：8
- official model card confirmed：3（CONCH、UNI、Prov-GigaPath）
- incomplete card：4；missing card：1（DeepPT）
- archived/missing/disputed repository：0

证据只来自期刊、PMC/arXiv、作者或项目官方 GitHub、官方 Hugging Face 匿名元数据
和 Zenodo。未使用博客、搜索摘要、个人上传或第三方镜像作为结论依据。

## 4. 许可证

| 结论 | 数量 | 说明 |
| --- | ---: | --- |
| noncommercial | 3 | CONCH、UNI、HoVer-Net 权重或模型用途受非商业条款约束 |
| research_only | 2 | Prov-GigaPath、PraNet 仅支持研究/教育等受限用途 |
| custom_terms | 1 | CellViT 为 Apache-2.0 加 Commons Clause 及组件条款 |
| unverified | 2 | ST-Net、DeepPT 未找到足够明确的模型/权重许可 |

代码许可证与模型/权重条款分别记录。GitHub 仓库显示 MIT 或 Apache-2.0，不会被
扩张解释为模型权重可商用或可再分发。CONCH 和 UNI 的官方条款为
CC BY-NC-ND 4.0 加项目使用条款；Prov-GigaPath 的代码为 Apache-2.0，但模型和
checkpoint 明确限研究及复现，并排除部署和临床用途。

## 5. 权重与 Revision

| 状态 | 数量 |
| --- | ---: |
| gated / public_available / not_released | 3 / 3 / 2 |
| model_revision_pinned / commit_pinned | 3 / 2 |
| conflicting_versions / unpinned | 1 / 2 |

三个 gated 模型的官方元数据列出：

- CONCH：802,235,437 bytes
- UNI：1,213,527,781 bytes
- Prov-GigaPath：4,885,429,372 bytes（两个模型权重文件）
- 已核验文件元数据合计：6,901,192,590 bytes

这些只是上游文件名、大小、revision 和 LFS OID 存在性记录。实际下载为 0。
CellViT、HoVer-Net 和 PraNet 虽有公开获取说明，但缺少本阶段可接受的固定权重
摘要或存在版本冲突，因此继续要求安全复核。

## 6. 技术、临床和安全

- technical contract accepted：6；incomplete：2
- input known：8；output known：8
- clinical boundary：8 条均为 `research_only`
- security cleared：3；review_required：5
- family resolution：0；8 条 Review 明确未建立批内重复关系

`security=cleared` 仅表示当前静态元数据中的版本和完整性缺口已由官方元数据覆盖。
没有进行沙箱、序列化加载、依赖安装、代码执行、GPU/CPU 验证或联网运行测试。
其余模型主要保留 `weight_integrity_unknown`、`dependency_unpinned` 和
`preprocessing_missing` 风险。

## 7. Eligibility

```text
eligible_for_model_draft = 3
security_review_required = 11
blocked = 2
```

可进入仅元数据草稿的模型为 CONCH、UNI 和 Prov-GigaPath。ST-Net 和 DeepPT
因权重未发布而 blocked。其余模型继续处于安全复核队列。没有为了达到目标数量
放宽许可证、revision、权重完整性或临床边界。

## 8. 证据预算

```text
official requests = 43
evidence files = 49
evidence size = 2,991,931 bytes
largest response = 847,248 bytes
```

访问域名仅包括 `api.github.com`、`arxiv.org`、`huggingface.co`、
`pmc.ncbi.nlm.nih.gov`、`www.nature.com` 和 `zenodo.org`。证据目录为
`D:\MedTrustData\model-governance-evidence\phase5.12.3B1`。采集器拒绝权重/
归档扩展名、附件响应、大体积响应和非白名单域名。没有使用 GitHub/Hugging Face
token。

## 9. 数据与安全边界

```text
formal Reviews = 96, exactly 8 records
backup Reviews = 0
raw record digests changed = 0
ExternalModelVersion changed = 0
ModelProduct added = 0
ComputeRun added = 0
weights downloaded = 0
git clone / Git LFS = 0 / 0
inference calls = 0
invalid audit chains = 0
```

非 operator 写审核返回 HTTP 403。历史 `ModelProduct=1` 和 `ComputeRun=2`
保持不变。审计链包含 96 条 review-created 事件并验证有效。

## 10. 验收

- backend：210 collected，144 passed，66 environment-gated skipped
- frontend：64 passed
- TypeScript typecheck / production build / Python compile：passed
- OpenAPI：143 paths，147 operations，0 duplicate operation IDs
- Alembic current/head：`20260727_0043`
- Compose：base、LAN、remote-preview、production template 均解析通过
- PowerShell：25 files，0 parse errors
- Chrome：四账号独立会话、operator 页面、会话隔离通过
- 390x844、768x1024、1366x768、1920x1080：页面级横向溢出 0
- 外部浏览器请求：0

后端跳过项需要独立测试数据库、破坏性并发开关或 PathMNIST 受控 smoke 环境；
本阶段未伪造这些条件。

## 11. 下一步

Phase 5.12.3B2 只能对上述 3 条 eligible 记录创建 metadata-only ModelProduct
草稿，并冻结当前治理快照。不得下载权重、创建可执行版本、推断兼容性或进入
Application/Contract/ComputeJob。

首个权重物化阶段仍未就绪。至少需要单独完成：条款接受和授权主体确认、固定文件
摘要核验、pickle/动态代码静态审计、依赖锁定、资源预算、隔离执行设计和许可证
允许范围确认。
