# Phase 5.11 v0 公共数据集网站源码审计

审计日期：2026-07-27

历史源码位置：独立本地工作区（未包含在本仓库）

审计范围：只读检查网站源码及其中的公共数据集目录；未访问外部链接，未下载数据集，未修改该网站源码，未执行 MedTrust 业务链。

## 1. 结论

这份目录确实是用户所述网站的前端源码。它是一个 Next.js 16 / React 19 项目，首页直接加载数据集浏览器、开源项目、课程和复现内容。

公共数据集部分当前不是数据库、REST API、GraphQL API 或独立 JSON 数据源，而是直接嵌入在：

```text
<historical-source-root>\lib\datasets.ts
```

`components/dataset-explorer.tsx` 在浏览器中导入这个静态数组，并在前端完成搜索、筛选和分页。`localStorage` 只保存筛选条件，不保存或同步数据集。

因此，按照 Phase 5.11 的数据源分类，它属于：

```text
E. frontend embedded data
```

这份源码可以作为“候选公共医学影像数据集目录”的初始输入，但不能直接作为 MedTrust 的可信公共数据同步源，也不能据此认定其中的数据集都允许下载、再分发或发布。

## 2. 源码证据

关键文件：

| 文件 | 作用 |
| --- | --- |
| `app/page.tsx` | 首页直接渲染 `DatasetExplorer` |
| `components/dataset-explorer.tsx` | 客户端搜索、筛选、分页、详情和外链跳转 |
| `lib/datasets.ts` | 982 条数据集静态 TypeScript 记录 |
| `data/opensource-projects.json` | 独立的开源项目目录，不是公共数据集目录 |

未发现：

- 数据集 API 路由；
- 数据集 JSON 文件；
- `fetch`、Axios 或 GraphQL 数据请求；
- Supabase、Firebase 等远端数据客户端；
- 数据集许可、访问等级或注册要求字段。

源码快照标识：

```text
lib/datasets.ts
size: 267265 bytes
last_write_time: 2026-07-14 10:39:34
sha256: 4FEA26AB64393E40E07228F6F0BDAF082A9097DABC3563BEB074D2718EEAD6D3
```

该目录没有 `.git` 元数据，也没有已安装的 `node_modules`，所以当前只能把它识别为一个源码快照，无法从本目录确认原始仓库、版本标签或部署版本。

## 3. 目录规模和结构

`Dataset` 只有以下 10 个字符串字段：

```ts
interface Dataset {
  name: string
  year: string
  dim: string
  modality: string
  structure: string
  images: string
  label: string
  task: string
  diseases: string
  url: string
}
```

统计结果：

| 指标 | 结果 |
| --- | ---: |
| 总记录数 | 982 |
| 唯一名称 | 973 |
| 重复名称组 | 8 |
| 唯一 URL | 889 |
| 重复 URL 组 | 46 |
| 使用 HTTPS | 920 |
| 使用 HTTP | 60 |
| 空 URL | 2 |
| 含反斜杠的可疑 URL | 21 |
| 明显异常 `==` 结尾 URL | 1 |
| DOI 类 URL | 26 |

字段缺失或占位：

| 字段 | 空值 | `NA` |
| --- | ---: | ---: |
| `year` | 0 | 30 |
| `modality` | 0 | 1 |
| `structure` | 0 | 76 |
| `images` | 71 | 0 |
| `task` | 0 | 34 |
| `diseases` | 0 | 116 |
| `url` | 2 | 0 |

`images` 是自由文本，例如 `2`、`5.0k`、`100k`，不能直接作为可靠数值。其他字段也存在大小写、分隔符和缩写不统一的问题。

## 4. 数据集覆盖

这个目录的主体是医学影像数据，不是通用公共数据目录。

主要模态：

| 模态 | 记录数 |
| --- | ---: |
| MR | 203 |
| CT | 163 |
| Endoscopy | 91 |
| Histopathology (Patch) | 63 |
| X-Ray | 52 |
| Histopathology (WSI) | 40 |
| Microscopy | 38 |
| Fundus Photo / Fundus | 66 |
| OCT | 28 |

与当前研究方向相关的候选记录：

| 范围 | 数量 | 说明 |
| --- | ---: | --- |
| 病理学 Patch/WSI | 107 | 可作为病理数据候选目录 |
| 结直肠相关 | 41 | 名称、器官或疾病字段命中 |
| 胃相关 | 10 | 名称、器官或疾病字段命中 |
| 单细胞/转录组 | 0 | 当前目录不覆盖 |
| MedMNIST | 1 | 目录只列出 MedMNIST v1 聚合入口 |
| Colorectal Histology MNIST | 1 | Zenodo 入口 |
| BreastMNIST | 1 | MedMNIST 入口 |

目录里没有单独名为 `PathMNIST` 的记录。不能因为存在一条 `MedMNIST` 聚合记录，就推断 PathMNIST 的版本、许可、样本构成或下载地址已经被正确登记。

可重点人工复核的结直肠病理候选包括：

- GlaS；
- Colorectal Histology MNIST；
- CRC100K；
- CoNSeP；
- CRAG；
- DigestPath19；
- CPTAC-COAD；
- PAIP2021；
- CoNIC2022；
- OCELOT2023。

这些名称仅表示目录中存在候选条目，不表示本次审计已确认其许可、访问条件、链接有效性或适合进入 MedTrust。

## 5. 上游来源分布

出现较多的来源域名：

| 来源 | 记录数 |
| --- | ---: |
| Cancer Imaging Archive 新旧站点 | 214 |
| Kaggle | 48 |
| GitHub | 42 |
| OpenNeuro | 39 |
| 天池 | 26 |
| Synapse | 26 |
| DOI 解析入口 | 26 |
| Zenodo | 15 |

同一 URL 可能对应多个挑战任务、子任务或条目。因此 URL 不能作为稳定唯一标识，也不能据此自动合并记录。

部分链接存在明显质量问题：

- 60 条仍使用 HTTP；
- 2 条 URL 为空；
- 21 条包含类似 `\%20` 或 `\#!` 的反斜杠；
- `VinDr-SpineXR` URL 以 `==` 结尾；
- 1 条链接直接指向压缩包；
- 部分链接指向竞赛首页、GitHub 仓库或聚合页面，而不是规范的数据集版本页。

同步程序不得自动跟随这些链接下载文件。

## 6. 不能从当前源码证明的内容

当前 10 个字段不足以支撑 MedTrust 的公共数据治理。至少缺少：

- 稳定的上游数据集 ID；
- 数据集版本和修订时间；
- 来源机构和来源数据库的规范名称；
- 官方落地页与下载地址的区分；
- DOI、引用和论文信息的规范字段；
- 许可证名称、许可证 URL 和许可原文；
- 是否允许再分发、商用和衍生使用；
- 公开、注册后可用、申请后可用或受限等访问等级；
- 是否需要账号、数据使用协议或伦理审批；
- 样本数、患者数、文件数和字节大小；
- 文件格式、输入形状、通道、标签定义和数据划分；
- 校验和、清单和上游版本摘要；
- 元数据采集时间、最后验证时间和验证人；
- 撤回、失效、归档或许可变更状态。

最重要的边界是：

```text
页面存在链接 != 数据公开
可以访问页面 != 可以自动下载
可以下载 != 可以再分发
标注为公共数据集 != 许可已经核验
```

## 7. 对 MedTrust Phase 5.11 的建议映射

当前字段只能作为原始候选元数据保存：

| 网站字段 | 建议目标字段 | 处理 |
| --- | --- | --- |
| `name` | `upstream_name` | 原样保留，不能作为唯一键 |
| `year` | `reported_year` | 字符串清洗后再解析 |
| `dim` | `reported_dimension` | 保留原文并建立规范枚举 |
| `modality` | `reported_modality` | 保留原文并建立规范映射 |
| `structure` | `reported_anatomy` | 保留原文并建立规范映射 |
| `images` | `reported_image_count_text` | 不直接转为可靠数值 |
| `label` | `reported_has_label` | 仅作为上游声明 |
| `task` | `reported_task` | 保留原文并建立规范映射 |
| `diseases` | `reported_disease` | 保留原文并建立规范映射 |
| `url` | `source_landing_url` | 仅作为候选落地页，不自动下载 |

同步后默认状态应是：

```text
metadata_status = imported_unverified
license_status = unknown
access_status = unknown
publication_status = draft
auto_download = false
auto_publish = false
```

任何正式发布都必须在独立的许可与访问条件核验后，由人工选择并走现有审核流程。

## 8. 网站侧最小改造建议

不要为 Phase 5.11 编写 DOM 抓取器。当前静态 TypeScript 数组已经是结构化源数据，应直接从网站项目导出机器接口。

建议优先级：

1. 新增版本化只读 JSON，例如 `/data/datasets.v1.json`。
2. 增加 `schema_version`、`catalog_version`、`generated_at` 和目录摘要。
3. 为每条记录增加稳定 `dataset_id`，且不要用名称或 URL 单独生成身份。
4. 增加 `source_name`、`source_dataset_id`、`source_version`、`official_url`。
5. 增加许可、访问等级、注册要求、引用和最后核验信息。
6. 可选增加只读 API：`GET /api/datasets` 与 `GET /api/datasets/{id}`。
7. 对导出结果增加 Schema 校验、重复检测和 URL 格式检查。

建议的同步边界：

```text
网站版本化 JSON/API
  -> MedTrust 外部目录暂存区
  -> Schema 与重复检查
  -> URL/许可/访问条件人工核验
  -> 人工选择
  -> 转成正式数据产品草稿
  -> 沿用现有审核和发布流程
```

## 9. 当前阶段判定

可判定为：

```text
源码已找到 = true
数据集目录已定位 = true
机器接口已存在 = false
目录质量已初审 = true
许可已核验 = false
可直接同步发布 = false
可自动下载 = false
```

Phase 5.11 下一步不应直接导入全部 982 条记录。应先完成网站侧机器接口和元数据治理字段，再选取少量许可清晰、来源稳定、与路演相关的数据集做只同步元数据的试点。

## 10. 给 Pro 的解读要点

请 Pro 重点判断：

1. 是否同意将该源码定性为“前端嵌入式候选目录”，而不是权威开放数据 API。
2. MedTrust 应保存原始字段，还是在导入时立即规范化；建议同时保存原文和规范字段。
3. 哪些许可、访问和版本字段应成为人工发布前的强制门槛。
4. 网站侧采用版本化 JSON 还是 Next.js 只读 API；建议先 JSON，API 作为后续能力。
5. 首批试点是否只选 5 至 10 条结直肠/胃/病理候选，并且只同步元数据。
6. 如何处理同名、同 URL 多条记录，避免错误去重。
7. 是否需要把单细胞/转录组建设为独立来源目录，因为当前源码完全不覆盖该方向。
