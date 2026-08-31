from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


ASSISTANT_SCHEMA = "phase5.14/research-demand-assistant/v1"
PAIR_CANDIDATE_SCHEMA = "medtrust.data-model-match/v1"
PAIR_RULESET_VERSION = "orthopedic-match-v1"

IMAGE_TASK_FAMILIES = {
    "image_classification",
    "object_detection",
    "image_segmentation",
    "ordinal_classification",
    "image_regression",
}

PAIR_SCORE_WEIGHTS = (
    ("DISEASE_ANATOMY", 20),
    ("MODALITY_VIEW", 15),
    ("TASK_TARGET", 20),
    ("INPUT_OUTPUT_SCHEMA", 15),
    ("POPULATION_TEMPORAL", 10),
    ("LICENSE_PURPOSE", 10),
    ("EVIDENCE", 5),
    ("LOCAL_OPERABILITY", 5),
)

BOUNDARY = {
    "research_only": True,
    "recommendation_only": True,
    "clinical_use": False,
    "auto_approval": False,
    "auto_training": False,
    "creates_application": False,
    "creates_compute_job": False,
    "raw_data_access": False,
    "requires_pre_index_features": True,
    "temporal_leakage_check_enforced": False,
    "hard_isolation": False,
    "catalog_scope": "active_published_internal_versions",
}

CONDITION_RULES = (
    {
        "code": "colorectal_pathology",
        "label": "结直肠组织病理",
        "keywords": ("结直肠", "colorectal", "pathmnist"),
        "catalog_terms": ("结直肠", "colorectal", "pathmnist"),
    },
    {
        "code": "fracture",
        "label": "骨折",
        "keywords": ("骨折", "fracture"),
        "catalog_terms": (
            "骨折",
            "fracture",
            "骨科",
            "orthopedic",
            "orthopaedic",
            "创伤",
            "trauma",
            "musculoskeletal",
        ),
    },
    {
        "code": "knee_osteoarthritis",
        "label": "膝骨关节炎",
        "keywords": ("膝骨关节炎", "骨关节炎", "knee osteoarthritis"),
        "catalog_terms": (
            "膝骨关节炎",
            "骨关节炎",
            "knee osteoarthritis",
            "osteoarthritis",
            "oai",
        ),
    },
    {
        "code": "bone_age",
        "label": "儿童骨龄",
        "keywords": ("骨龄", "bone age"),
        "catalog_terms": ("骨龄", "bone age", "pediatric hand"),
    },
    {
        "code": "bone_tumor",
        "label": "骨肿瘤",
        "keywords": ("骨肿瘤", "osteosarcoma"),
        "catalog_terms": ("骨肿瘤", "osteosarcoma", "bone tumor", "bone tumour"),
    },
    {
        "code": "cancer",
        "label": "肿瘤",
        "keywords": ("肿瘤", "癌", "cancer", "tumor", "tumour"),
        "catalog_terms": ("肿瘤", "癌", "cancer", "tumor", "tumour", "oncology"),
    },
    {
        "code": "diabetes",
        "label": "糖尿病",
        "keywords": ("糖尿病", "diabetes", "diabetic"),
        "catalog_terms": ("糖尿病", "diabetes", "diabetic", "endocrine"),
    },
    {
        "code": "cardiovascular",
        "label": "心血管疾病",
        "keywords": ("心血管", "心衰", "冠心病", "cardiovascular", "heart failure"),
        "catalog_terms": (
            "心血管",
            "心衰",
            "冠心病",
            "cardiovascular",
            "cardiology",
            "heart failure",
        ),
    },
    {
        "code": "pneumonia",
        "label": "肺炎",
        "keywords": ("肺炎", "pneumonia"),
        "catalog_terms": ("肺炎", "pneumonia", "respiratory", "呼吸"),
    },
)

STRUCTURED_MODALITY_TERMS = (
    "structured_ehr",
    "structured ehr",
    "electronic health record",
    "ehr",
    "clinical_tabular",
    "clinical tabular",
    "tabular",
    "longitudinal",
    "电子病历",
    "结构化",
    "纵向",
    "住院记录",
    "hospital information system",
    "医院信息系统",
    "fhir",
    "omop",
)
IMAGE_MODALITY_TERMS = (
    "digital_pathology",
    "digital pathology",
    "pathmnist",
    "histopathology",
    "histology",
    "image_classification",
    "image classification",
    "图像分类",
    "数字病理",
    "病理图像",
    "组织病理",
    "x_ray",
    "x-ray",
    "x ray",
    "radiograph",
    "radiography",
    "plain_radiograph",
    "x线",
    "x 线",
    "x光",
    "x 光",
)

PATHOLOGY_MODALITY_TERMS = (
    "digital_pathology",
    "digital pathology",
    "pathmnist",
    "histopathology",
    "histology",
    "数字病理",
    "病理图像",
    "组织病理",
)

XRAY_MODALITY_TERMS = (
    "x_ray",
    "x-ray",
    "x ray",
    "radiograph",
    "radiography",
    "plain_radiograph",
    "x线",
    "x 线",
    "x光",
    "x 光",
)


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _has_structured_modality(text: str) -> bool:
    return _contains_any(text, STRUCTURED_MODALITY_TERMS) or bool(
        re.search(r"(?<![a-z0-9])his(?![a-z0-9])", text, flags=re.IGNORECASE)
    )


def _flatten_public_metadata(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_flatten_public_metadata(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_public_metadata(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _detect_blocking_reasons(text: str) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    personal_patterns = (
        r"(?:预测|评估|计算).{0,10}(?:我的|我本人)",
        r"我(?:最近|已经|刚刚|可能)?(?:骨折|患病|受伤)",
        r"我会不会",
        r"我的.{0,20}(?:风险|病情|诊断|治疗)",
        r"(?:给|帮)我.{0,8}(?:诊断|治疗|用药|判断是否需要住院)",
    )
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in personal_patterns):
        reasons.append(
            {
                "code": "PERSONAL_CLINICAL_REQUEST_NOT_SUPPORTED",
                "message": "该入口不处理个人患者风险、诊断、治疗或用药请求。",
            }
        )
    clinical_use_text = re.sub(
        r"(?:不|不得|不会|禁止).{0,6}(?:用于|作为|支持)?(?:临床诊断|临床决策|治疗决策|治疗方案|用药方案)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if re.search(
        r"(?:用于|支持|辅助|替代|提供|给出).{0,10}(?:临床诊断|临床决策|治疗决策|治疗方案|用药方案)|(?:直接|实际)用于临床",
        clinical_use_text,
        flags=re.IGNORECASE,
    ):
        reasons.append(
            {
                "code": "CLINICAL_USE_NOT_SUPPORTED",
                "message": "当前平台仅支持非临床研究需求准备。",
            }
        )
    if _contains_any(
        text,
        (
            "导出原始病历",
            "下载原始病历",
            "原始病历明细",
            "下载患者明细",
            "导出患者明细",
            "下载原始数据",
            "patient-level export",
            "raw medical record",
        ),
    ):
        reasons.append(
            {
                "code": "RAW_RECORD_EXPORT_NOT_SUPPORTED",
                "message": "该入口不提供原始病历或患者级明细导出。",
            }
        )
    if re.search(r"忽略.{0,12}(?:规则|指令)|系统提示|prompt\s*injection", text, re.I):
        reasons.append(
            {
                "code": "INSTRUCTION_OVERRIDE_NOT_SUPPORTED",
                "message": "需求文本不能改变平台权限、策略或执行边界。",
            }
        )
    return reasons


def _detect_condition(text: str) -> dict[str, Any]:
    for rule in CONDITION_RULES:
        if _contains_any(text, rule["keywords"]):
            return dict(rule)
    return {
        "code": "unspecified",
        "label": "待确认疾病人群",
        "keywords": (),
        "catalog_terms": (),
    }


def _detect_outcome(text: str) -> tuple[str, str, str]:
    if _contains_any(text, ("再入院", "再住院", "readmission")):
        return "readmission", "再入院", "binary_classification"
    if _contains_any(text, ("死亡", "病死", "mortality")):
        return "mortality", "死亡", "binary_classification"
    if _contains_any(text, ("并发症", "complication")):
        return "complication", "并发症", "binary_classification"
    if _contains_any(text, ("住院时长", "住院时间", "length of stay")):
        return "length_of_stay", "住院时长", "regression"
    if _contains_any(text, ("复发", "recurrence")):
        return "recurrence", "复发", "binary_classification"
    if _contains_any(text, ("住院风险", "入院风险")):
        return "inpatient_risk_unspecified", "住院相关风险（待澄清）", "risk_prediction"
    if _contains_any(text, ("风险预测", "预测模型", "risk prediction")):
        return "unspecified_outcome", "预测结局（待澄清）", "risk_prediction"
    if _contains_any(text, ("分割", "segmentation", "pixel mask", "像素掩膜")):
        return "image_mask", "医学影像像素掩膜", "image_segmentation"
    if _contains_any(text, ("检测", "定位", "bounding box", "object detection", "框标注")):
        return "object_location", "医学影像目标位置", "object_detection"
    if _contains_any(text, ("kl分级", "kl 分级", "严重度分级", "severity grading")) or re.search(
        r"(?:kl|kellgren(?:\s*[-–]\s*lawrence)?)[\s_-]*(?:0\s*(?:到|至|-|—)\s*4\s*)?(?:级|分级)|"
        r"骨关节炎.{0,20}(?:分级|评级)",
        text,
        re.IGNORECASE,
    ):
        return "severity_grade", "影像严重度等级", "ordinal_classification"
    if _contains_any(text, ("骨龄", "bone age")):
        return "bone_age_months", "骨龄（月）", "image_regression"
    if _contains_any(text, ("image classification", "image_classification")) or re.search(
        r"(?:图像|影像|病理|组织|骨骼|骨折|x\s*线|x\s*光).{0,28}(?:分类|识别|判断|判定)|"
        r"(?:分类|识别|判断|判定).{0,16}(?:是否)?(?:存在)?(?:骨折|病变|异常)",
        text,
        re.IGNORECASE,
    ):
        if _contains_any(text, ("骨折", "fracture")):
            return "fracture_presence", "骨折存在性", "image_classification"
        return "pathology_image_class", "组织病理图像类别", "image_classification"
    return "unspecified_outcome", "预测结局（待澄清）", "risk_prediction"


def _detect_anatomical_site(text: str) -> tuple[str | None, str | None]:
    rules = (
        ("multi_site", "多部位骨骼", ("多部位", "multiple sites", "multi-site", "multisite")),
        ("wrist", "腕部", ("腕", "wrist")),
        ("knee", "膝关节", ("膝", "knee")),
        ("hip", "髋部", ("髋", "hip")),
        ("pelvis", "骨盆", ("骨盆", "pelvis", "pelvic")),
        ("ankle", "踝部", ("踝", "ankle")),
        ("foot", "足部", ("足部", "foot")),
        ("hand", "手部", ("手部", "hand")),
        ("shoulder", "肩部", ("肩", "shoulder")),
        ("elbow", "肘部", ("肘", "elbow")),
        ("chest", "胸部", ("胸", "chest")),
    )
    for code, label, terms in rules:
        if _contains_any(text, terms):
            return code, label
    return None, None


def _detect_index_time(text: str) -> tuple[str | None, str | None]:
    rules = (
        ("discharge", "出院时", (r"出院时", r"出院后")),
        ("emergency_visit", "急诊首次评估时", (r"急诊.{0,6}(?:时|首次评估)",)),
        ("preoperative", "手术前", (r"术前", r"手术前")),
        ("postoperative", "手术后", (r"术后", r"手术后")),
        ("admission", "入院时", (r"入院时", r"入院当天", r"首次入院", r"入院后")),
        ("first_assessment", "首次评估时", (r"首次评估", r"初次评估")),
    )
    for code, label, patterns in rules:
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            return code, label
    return None, None


def _detect_horizon(text: str) -> tuple[str | None, str | None]:
    matches = list(
        re.finditer(
            r"(?P<count>\d{1,4})\s*(?P<unit>小时|天|日|周|月|年)(?:之?内|以内)?",
            text,
        )
    )
    if matches:
        def horizon_score(candidate: re.Match[str]) -> int:
            before = text[max(0, candidate.start() - 14):candidate.start()]
            after = text[candidate.end():candidate.end() + 10]
            score = 0
            if re.search(r"预测|未来|结局|风险|再入院|死亡|复发|发生", before + after):
                score += 4
            if re.search(r"过去|历史|回顾|基线|观察窗|特征窗", before):
                score -= 6
            if re.search(r"内|以内|之后|以后", after):
                score += 1
            return score

        match = max(matches, key=lambda item: (horizon_score(item), item.start()))
        count = int(match.group("count"))
        unit = match.group("unit")
        codes = {
            "小时": "hours",
            "天": "days",
            "日": "days",
            "周": "weeks",
            "月": "months",
            "年": "years",
        }
        return f"{count}_{codes[unit]}", f"{count}{unit}"
    if "住院期间" in text:
        return "during_hospitalization", "住院期间"
    return None, None


def _detect_population(text: str, condition_label: str) -> tuple[str, str]:
    if _contains_any(text, ("老年", "高龄", "elderly", "older adult")):
        return "older_adults", f"老年{condition_label}患者"
    if _contains_any(text, ("儿童", "小儿", "儿科", "pediatric", "paediatric")):
        return "children", f"儿童{condition_label}患者"
    if _contains_any(text, ("成人", "adult")):
        return "adults", f"成人{condition_label}患者"
    return "unspecified", f"{condition_label}患者"


def _detect_study_mode(text: str) -> tuple[str, str]:
    if _contains_any(text, ("外部验证", "独立验证", "验证模型", "模型验证", "评估模型", "validation")) or re.search(
        r"(?:验证|评估).{0,12}(?:模型|分类器|算法)", text, re.IGNORECASE
    ):
        return "validation", "模型验证"
    if _contains_any(text, ("训练模型", "模型训练", "构建模型", "建立模型", "开发模型", "training")) or re.search(
        r"(?:构建|建立|开发|训练).{0,12}(?:预测|分类|风险)?模型", text, re.IGNORECASE
    ):
        return "training", "模型训练"
    if _contains_any(text, ("模型推理", "运行推理", "批量推理", "调用模型", "inference")):
        return "inference", "模型推理"
    return "unspecified", "待确认研究方式"


def _detect_care_setting(text: str) -> tuple[str, str]:
    if _contains_any(text, ("急诊", "emergency")):
        return "emergency", "急诊"
    if _contains_any(text, ("重症监护", "重症", "ICU", "intensive care")):
        return "intensive_care", "重症监护"
    if _contains_any(text, ("门诊", "outpatient")):
        return "outpatient", "门诊"
    if _contains_any(text, ("住院", "入院", "出院", "inpatient", "admission")):
        return "inpatient", "住院"
    return "unspecified", "待确认就诊场景"


def _detect_data_modality(text: str) -> tuple[str, str]:
    if _contains_any(text, PATHOLOGY_MODALITY_TERMS):
        return "digital_pathology", "数字病理图像"
    if _contains_any(text, XRAY_MODALITY_TERMS):
        return "x_ray", "X 线影像"
    if _has_structured_modality(text):
        return "structured_ehr", "结构化电子病历"
    if re.search(r"(?<![a-z0-9])mri?(?![a-z0-9])|磁共振|核磁", text, re.IGNORECASE):
        return "mr", "磁共振影像"
    if re.search(r"(?<![a-z0-9])ct(?![a-z0-9])|计算机断层", text, re.IGNORECASE):
        return "ct", "CT 影像"
    if _contains_any(text, ("image", "图像", "影像")):
        return "medical_image", "医学影像"
    return "unspecified", "待确认数据模态"


def _bounded_clause(text: str, prefix: str) -> str | None:
    match = re.search(rf"(?:{prefix})(?P<value>[^，。；;\n]{{2,80}})", text, re.IGNORECASE)
    return _normalise_text(match.group("value")) if match else None


def _build_cohort_criteria(
    text: str,
    *,
    condition_code: str,
    condition_label: str,
    population_code: str,
    care_setting_code: str,
    care_setting_label: str,
) -> tuple[list[str], list[str]]:
    inclusion: list[str] = []
    if condition_code != "unspecified":
        inclusion.append(f"疾病或研究对象：{condition_label}")
    population_labels = {
        "adults": "年龄范围：成人（具体阈值由研究方案确认）",
        "older_adults": "年龄范围：老年人群（具体阈值由研究方案确认）",
        "children": "年龄范围：儿童人群（具体阈值由研究方案确认）",
        "pathology_images": "样本类型：组织病理图像",
    }
    if population_code in population_labels:
        inclusion.append(population_labels[population_code])
    if care_setting_code != "unspecified":
        inclusion.append(f"就诊场景：{care_setting_label}")
    explicit_inclusion = _bounded_clause(text, "纳入|包含")
    if explicit_inclusion:
        inclusion.append(f"用户描述：{explicit_inclusion}")
    explicit_exclusion = _bounded_clause(text, "排除|不纳入")
    exclusion = [f"用户描述：{explicit_exclusion}"] if explicit_exclusion else []
    return list(dict.fromkeys(inclusion)), exclusion


def _detect_evaluation_outputs(text: str) -> list[str]:
    rules = (
        ("准确率", ("准确率", "accuracy")),
        ("AUROC", ("auroc", "auc", "受试者工作特征")),
        ("F1 分数", ("f1", "f-1")),
        ("混淆矩阵", ("混淆矩阵", "confusion matrix")),
        ("校准度", ("校准曲线", "校准度", "calibration")),
        ("生存分析指标", ("c-index", "concordance", "生存分析")),
    )
    return [label for label, keywords in rules if _contains_any(text, keywords)]


def _concept_mappings(intent: Mapping[str, Any]) -> list[dict[str, Any]]:
    concepts: list[dict[str, Any]] = []
    for semantic_role, code_key, label_key in (
        ("condition", "condition_code", "condition_label"),
        ("outcome", "outcome_code", "outcome_label"),
    ):
        local_code = str(intent.get(code_key) or "")
        label = str(intent.get(label_key) or "")
        if not label or local_code in {"unspecified", "unspecified_outcome", "inpatient_risk_unspecified"}:
            continue
        concepts.append(
            {
                "semantic_role": semantic_role,
                "text": label,
                "coding_system": None,
                "code": None,
                "mapping_status": "not_mapped",
            }
        )
    return concepts


def _build_clarifications(intent: Mapping[str, Any]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    if intent["condition_code"] == "unspecified":
        questions.append(
            {
                "code": "disease_domain",
                "required": True,
                "question": "研究针对哪一种疾病、损伤或临床人群？",
                "options": [],
            }
        )
    if intent["task_family"] in IMAGE_TASK_FAMILIES:
        return questions
    if intent["population_code"] == "unspecified":
        questions.append(
            {
                "code": "population_definition",
                "required": True,
                "question": "目标人群的年龄范围、就诊场景和关键纳排条件是什么？",
                "options": ["成人", "老年人", "儿童", "其他明确人群"],
            }
        )
    if intent["outcome_code"] in {"inpatient_risk_unspecified", "unspecified_outcome"}:
        questions.append(
            {
                "code": "outcome_definition",
                "required": True,
                "question": "“住院风险”具体指哪一个可验证结局？",
                "options": ["是否入院", "住院期间并发症", "住院时长", "出院后再入院", "院内死亡"],
            }
        )
    if intent["index_time_code"] is None:
        questions.append(
            {
                "code": "index_time",
                "required": True,
                "question": "模型在哪一个时点做预测，且只能使用该时点之前可获得的信息？",
                "options": ["急诊首次评估时", "入院时", "手术前", "出院时"],
            }
        )
    if intent["prediction_horizon"] is None:
        questions.append(
            {
                "code": "prediction_horizon",
                "required": True,
                "question": "预测窗口是多长？",
                "options": ["住院期间", "24小时", "7天", "30天", "90天"],
            }
        )
    return questions


def _build_draft_patch(intent: Mapping[str, Any]) -> dict[str, Any]:
    population = str(intent["population_label"])
    outcome = str(intent["outcome_label"])
    if intent["task_family"] in IMAGE_TASK_FAMILIES:
        return {
            "profile": {
                "demand_name": f"{population}分类研究草稿"[:160],
                "project_type": "model_external_validation",
                "project_summary": (
                    f"拟使用已获批准并锁定版本的{population}，评估{outcome}分类的研究可行性；"
                    "数据与模型候选均来自当前已发布目录。"
                ),
                "purpose_code": "model_validation",
                "research_purpose": (
                    f"在非临床研究场景下建立并比较{outcome}分类方案，"
                    "输出仅限聚合性能指标、混淆矩阵和执行摘要。"
                ),
                "use_background": "由研究需求方根据自然语言生成的待确认申请草稿，仅用于研究设计和目录资产筛选。",
                "expected_value": "评估组织病理图像分类的研究可行性并形成可审查的方法比较证据。",
                "clinical_diagnosis": False,
                "research_publication": False,
                "commercial_validation": False,
                "data_minimization": "仅申请完成图像分类研究所必需的最小样本、标签与质量字段。",
            },
            "data_scope": {
                "scope_type": "described_subset",
                "subset_description": f"仅纳入满足最终确认标准的{population}研究样本。",
                "selection_criteria": f"研究对象：{population}；目标任务：{outcome}分类。",
            },
        }
    index_label = intent["index_time_label"] or "待确认预测时点"
    horizon_label = intent["prediction_horizon_label"] or "待确认预测窗口"
    return {
        "profile": {
            "demand_name": f"{population}{outcome}预测研究草稿"[:160],
            "project_type": "research_analysis",
            "project_summary": (
                f"拟使用已获批准并锁定版本的研究数据，评估{population}{outcome}预测的可行性；"
                f"预测时点为{index_label}，预测窗口为{horizon_label}，未确认项需由研究人员补充。"
            ),
            "purpose_code": "research_analysis",
            "research_purpose": (
                f"在非临床研究场景下建立并比较{population}{outcome}的可复核预测方案，"
                "输出仅限聚合性能指标、混淆矩阵和执行摘要。"
            ),
            "use_background": "由研究需求方根据自然语言生成的待确认申请草稿，仅用于研究设计和目录资产筛选。",
            "expected_value": "评估研究可行性并形成可审查的方法比较证据，不直接支持患者级或临床决策。",
            "clinical_diagnosis": False,
            "research_publication": False,
            "commercial_validation": False,
            "data_minimization": (
                f"仅申请完成{outcome}研究所必需的最小字段、队列和时间范围；"
                f"特征必须在{index_label}之前可获得，排除未来信息泄漏。"
            ),
        },
        "data_scope": {
            "scope_type": "described_subset",
            "subset_description": f"仅纳入满足最终确认标准的{population}研究子集。",
            "selection_criteria": (
                f"疾病人群：{population}；预测时点：{index_label}；"
                f"预测窗口：{horizon_label}；结局：{outcome}。"
            ),
        },
    }


def _method_suggestions(task_family: str) -> list[dict[str, Any]]:
    common = {
        "registered": False,
        "executable": False,
        "boundary": "方法族建议，不代表平台已登记、可执行或已验证的模型产品。",
    }
    if task_family in IMAGE_TASK_FAMILIES:
        methods = (
            ("linear_probe_baseline", "冻结特征线性分类基线", "用于建立可复核的图像分类基准。"),
            ("resnet_classifier", "ResNet 图像分类器", "适合与当前固定目录模型进行受控比较。"),
        )
    elif task_family == "regression":
        methods = (
            ("regularized_regression", "正则化回归基线", "用于连续住院时长的透明基线。"),
            ("gradient_boosting_regression", "梯度提升树回归", "适合结构化表格特征的非线性比较。"),
        )
    elif task_family == "survival_analysis":
        methods = (
            ("cox_baseline", "Cox 生存模型基线", "适合时间到事件结局并保留可解释性。"),
            ("discrete_time_survival", "离散时间生存模型", "适合明确时间窗口和删失信息的研究。"),
        )
    else:
        methods = (
            ("logistic_regression_baseline", "正则化 Logistic 回归基线", "适合作为二分类风险预测的可解释基线。"),
            ("gradient_boosting", "梯度提升树", "适合结构化表格特征的非线性候选比较。"),
        )
    return [
        {"code": code, "name": name, "reason": reason, **common}
        for code, name, reason in methods
    ]


def _safe_candidate(item: Mapping[str, Any], *, score: int, reasons: list[str], limitations: list[str]) -> dict[str, Any]:
    return {
        "product_id": str(item.get("product_id") or ""),
        "version_id": str(item.get("version_id") or ""),
        "product_code": str(item.get("product_code") or ""),
        "name": str(item.get("name") or ""),
        "provider": str(item.get("provider") or ""),
        "disease_domain": str(item.get("disease_domain") or ""),
        "modality": str(item.get("modality") or ""),
        "task_type": str(item.get("task_type") or ""),
        "version": str(item.get("version") or ""),
        "non_clinical": bool(item.get("non_clinical", True)),
        "score": score,
        "match_level": "strong" if score >= 80 else "partial",
        "recommendation_eligible": True,
        "reasons": reasons,
        "limitations": limitations,
    }


def _rank_data_products(
    products: Sequence[Mapping[str, Any]], intent: Mapping[str, Any]
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    terms = tuple(intent["catalog_terms"])
    for item in products:
        public_metadata = {
            key: item.get(key)
            for key in (
                "name",
                "product_code",
                "disease_domain",
                "modality",
                "scale",
                "quality",
                "policy",
            )
        }
        haystack = _flatten_public_metadata(public_metadata).casefold()
        reasons: list[str] = []
        blockers: list[str] = []
        limitations = ["仍需服务端兼容性检查、用途审查和多方授权。"]
        score = 0

        if terms and _contains_any(haystack, terms):
            score += 50
            reasons.append("疾病领域或产品说明与目标人群匹配。")
        else:
            blockers.append("疾病领域未显示与目标人群匹配。")

        modality_text = str(item.get("modality") or "").casefold()
        has_structured_modality = _has_structured_modality(modality_text)
        has_image_modality = _contains_any(modality_text, IMAGE_MODALITY_TERMS)
        if intent["task_family"] in IMAGE_TASK_FAMILIES:
            if has_image_modality:
                score += 40
                reasons.append("目录元数据显示数字病理或图像分类数据模态。")
            else:
                blockers.append("目录未登记图像分类所需的数字病理数据模态。")
        elif has_image_modality and not has_structured_modality:
            blockers.append("图像/数字病理模态不能替代住院风险所需的结构化临床时间线。")
        elif has_structured_modality:
            score += 30
            reasons.append("目录元数据显示结构化或纵向临床数据模态。")
        else:
            blockers.append("目录未登记可验证的结构化或纵向临床数据模态。")

        if intent["task_family"] in IMAGE_TASK_FAMILIES:
            if _contains_any(haystack, ("pathmnist", "histopathology", "digital_pathology", "数字病理", "病理")):
                score += 10
                reasons.append("产品说明与组织病理分类场景匹配。")
        elif _contains_any(haystack, ("longitudinal", "纵向", "time", "时间", "admission", "住院")):
            score += 10
            reasons.append("目录元数据包含纵向或住院场景线索。")
        else:
            limitations.append("尚未登记完整的时间覆盖与索引时点语义。")

        if _contains_any(haystack, ("controlled_compute", "controlled compute", "受控计算")):
            score += 5
            reasons.append("用途策略包含受控计算。")

        if not blockers and score >= 65:
            ranked.append(
                _safe_candidate(
                    item,
                    score=min(score, 100),
                    reasons=reasons,
                    limitations=limitations,
                )
            )

    return sorted(
        ranked,
        key=lambda item: (-item["score"], item["name"].casefold(), item["version_id"]),
    )[:5]


def _rank_model_products(
    products: Sequence[Mapping[str, Any]], intent: Mapping[str, Any]
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    terms = tuple(intent["catalog_terms"])
    task_family = str(intent["task_family"])
    task_terms = {
        "image_classification": (
            "image_classification",
            "image classification",
            "图像分类",
            "病理分类",
            "pathmnist",
        ),
        "object_detection": ("object_detection", "object detection", "detection", "定位", "检测"),
        "image_segmentation": ("image_segmentation", "segmentation", "分割", "mask"),
        "ordinal_classification": ("ordinal_classification", "severity grading", "分级"),
        "image_regression": ("bone_age", "bone age", "骨龄", "regression"),
        "binary_classification": (
            "risk_prediction",
            "risk prediction",
            "binary_classification",
            "binary classification",
            "risk model",
            "风险预测",
            "二分类",
        ),
        "regression": ("regression", "回归", "length_of_stay", "住院时长"),
        "survival_analysis": ("survival", "time_to_event", "生存", "时间到事件"),
    }.get(task_family, ("risk_prediction", "risk prediction", "风险预测"))
    outcome_terms = {
        "readmission": ("readmission", "再入院", "再住院"),
        "mortality": ("mortality", "death", "死亡", "病死"),
        "complication": ("complication", "并发症"),
        "length_of_stay": ("length_of_stay", "length of stay", "住院时长", "住院时间"),
        "recurrence": ("recurrence", "relapse", "复发"),
    }.get(str(intent["outcome_code"]), ())

    for item in products:
        public_metadata = {
            key: item.get(key)
            for key in (
                "name",
                "product_code",
                "disease_domain",
                "task_type",
                "modality",
                "input_schema",
                "output_schema",
                "policy",
                "license",
                "non_clinical",
            )
        }
        haystack = _flatten_public_metadata(public_metadata).casefold()
        reasons: list[str] = []
        blockers: list[str] = []
        limitations = ["目录匹配不等于性能、临床有效性或使用授权。"]
        score = 0

        if terms and _contains_any(haystack, terms):
            score += 45
            reasons.append("模型疾病领域与目标人群匹配。")
        else:
            blockers.append("模型疾病领域未显示与目标人群匹配。")

        if _contains_any(haystack, task_terms):
            score += 20
            reasons.append("登记任务类型与预测结局类型匹配。")
        else:
            blockers.append("登记任务类型与目标预测任务不匹配。")

        if outcome_terms and _contains_any(haystack, outcome_terms):
            score += 20
            reasons.append("模型登记的具体预测结局与研究结局匹配。")
        elif outcome_terms:
            blockers.append("模型未登记与目标研究一致的具体预测结局。")

        modality_text = str(item.get("modality") or "").casefold()
        has_structured_modality = _has_structured_modality(modality_text)
        has_image_modality = _contains_any(modality_text, IMAGE_MODALITY_TERMS)
        if task_family in IMAGE_TASK_FAMILIES:
            if has_image_modality:
                score += 15
                reasons.append("模型输入模态支持数字病理图像。")
            else:
                blockers.append("模型未登记数字病理图像输入模态。")
        elif has_image_modality and not has_structured_modality:
            blockers.append("图像分类输入不能替代结构化住院风险特征。")
        elif has_structured_modality:
            score += 15
            reasons.append("模型输入模态支持结构化临床数据。")
        else:
            blockers.append("模型未登记可验证的结构化临床输入模态。")

        if bool(item.get("non_clinical", True)):
            score += 5
            reasons.append("模型登记为非临床用途，符合当前研究边界。")
        else:
            limitations.append("即使产品存在其他用途，当前平台仍只允许非临床研究使用。")

        if _contains_any(haystack, ("controlled_compute", "controlled compute", "受控计算")):
            score += 5
            reasons.append("用途策略包含受控计算。")

        if not blockers and score >= 65:
            ranked.append(
                _safe_candidate(
                    item,
                    score=min(score, 100),
                    reasons=reasons,
                    limitations=limitations,
                )
            )

    return sorted(
        ranked,
        key=lambda item: (-item["score"], item["name"].casefold(), item["version_id"]),
    )[:5]


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _candidate_profile(item: Mapping[str, Any]) -> Mapping[str, Any]:
    for value in (
        item.get("profile"),
        item.get("medtrust_profile"),
        _as_mapping(item.get("normalized_payload")).get("medtrust_profile"),
        _as_mapping(item.get("scope_metadata")).get("medtrust_profile"),
        _as_mapping(item.get("compatibility_metadata")).get("medtrust_profile"),
    ):
        if isinstance(value, Mapping) and value:
            return value
    return {}


def _candidate_blob(item: Mapping[str, Any]) -> str:
    profile = _candidate_profile(item)
    public_metadata = {
        key: item.get(key)
        for key in (
            "name",
            "product_code",
            "disease_domain",
            "modality",
            "task_type",
            "input_schema",
            "output_schema",
            "scope_metadata",
            "linkage_metadata",
            "compatibility_metadata",
        )
    }
    public_metadata["profile"] = profile
    return _flatten_public_metadata(public_metadata).casefold()


def _canonical_modalities(value: Any) -> set[str]:
    text = _flatten_public_metadata(value).casefold()
    result: set[str] = set()
    if _contains_any(text, STRUCTURED_MODALITY_TERMS) or _has_structured_modality(text):
        result.add("structured_ehr")
    if _contains_any(text, PATHOLOGY_MODALITY_TERMS):
        result.add("digital_pathology")
    if _contains_any(text, XRAY_MODALITY_TERMS):
        result.add("x_ray")
    if re.search(r"(?<![a-z0-9])mri?(?![a-z0-9])|磁共振|核磁", text, re.IGNORECASE):
        result.add("mr")
    if re.search(r"(?<![a-z0-9])ct(?![a-z0-9])|计算机断层", text, re.IGNORECASE):
        result.add("ct")
    if not result and _contains_any(text, ("image", "图像", "影像")):
        result.add("medical_image")
    return result


def _candidate_modalities(item: Mapping[str, Any]) -> set[str]:
    profile = _candidate_profile(item)
    compatibility = _as_mapping(item.get("compatibility_metadata"))
    return _canonical_modalities(
        (
            item.get("modality"),
            profile.get("modalities"),
            profile.get("modality"),
            profile.get("input_schema"),
            compatibility.get("modality"),
            compatibility.get("input_schema"),
        )
    )


def _canonical_tasks(value: Any) -> set[str]:
    text = _flatten_public_metadata(value).casefold()
    result: set[str] = set()
    task_terms = (
        ("image_segmentation", ("image_segmentation", "segmentation", "分割", "mask")),
        (
            "object_detection",
            (
                "object_detection",
                "object detection",
                "image_localization",
                "localization",
                "detection",
                "定位",
                "检测",
                "bounding box",
            ),
        ),
        ("ordinal_classification", ("ordinal_classification", "severity grading", "kl grading", "分级")),
        ("image_regression", ("bone_age", "bone age", "骨龄")),
        ("image_classification", ("image_classification", "image classification", "图像分类", "影像分类", "病理分类")),
        ("survival_analysis", ("survival", "time_to_event", "生存", "时间到事件")),
        ("regression", ("regression", "length_of_stay", "住院时长", "回归")),
        ("risk_prediction", ("risk_prediction", "risk prediction", "risk model", "风险预测")),
        ("binary_classification", ("binary_classification", "binary classification", "二分类", "readmission", "mortality", "complication")),
    )
    for code, terms in task_terms:
        if _contains_any(text, terms):
            result.add(code)
    return result


def _candidate_tasks(item: Mapping[str, Any]) -> set[str]:
    profile = _candidate_profile(item)
    compatibility = _as_mapping(item.get("compatibility_metadata"))
    return _canonical_tasks(
        (
            item.get("task_type"),
            profile.get("task_type"),
            profile.get("supported_tasks"),
            profile.get("output_schema"),
            compatibility.get("task_type"),
            compatibility.get("output_schema"),
        )
    )


def _candidate_anatomies(item: Mapping[str, Any]) -> set[str]:
    profile = _candidate_profile(item)
    text = _flatten_public_metadata(
        (
            profile.get("anatomical_sites"),
            profile.get("anatomy"),
            item.get("organs"),
            item.get("name"),
        )
    ).casefold()
    rules = (
        ("musculoskeletal_system", ("musculoskeletal_system", "musculoskeletal system")),
        ("upper_limb", ("upper_limb", "upper limb")),
        ("lower_limb", ("lower_limb", "lower limb")),
        ("multi_site", ("multi_site", "multi-site", "multiple sites", "多部位")),
        ("wrist", ("wrist", "腕")),
        ("knee", ("knee", "膝")),
        ("hip", ("hip", "髋")),
        ("pelvis", ("pelvis", "pelvic", "骨盆")),
        ("ankle", ("ankle", "踝")),
        ("foot", ("foot", "足部")),
        ("hand", ("hand", "手部")),
        ("shoulder", ("shoulder", "肩")),
        ("elbow", ("elbow", "肘")),
        ("chest", ("chest", "胸")),
    )
    return {code for code, terms in rules if _contains_any(text, terms)}


def _condition_code_matches(requested_code: str, candidate_code: str) -> bool:
    requested = requested_code.casefold().replace("-", "_").strip()
    candidate = candidate_code.casefold().replace("-", "_").strip()
    aliases = {
        "fracture": {"fracture", "hip_fracture", "pelvic_fracture"},
        "knee_osteoarthritis": {"knee_osteoarthritis", "osteoarthritis"},
        "bone_age": {"bone_age", "pediatric_bone_age"},
        "bone_tumor": {"bone_tumor", "osteosarcoma"},
    }
    return candidate == requested or candidate in aliases.get(requested, set())


def _condition_match(
    item: Mapping[str, Any], requested_code: str, *, allow_general_template: bool = False
) -> str:
    if requested_code == "unspecified":
        return "hold"
    profile = _candidate_profile(item)
    profile_codes = profile.get("condition_codes")
    if isinstance(profile_codes, str):
        declared_codes = [profile_codes]
    elif isinstance(profile_codes, Sequence):
        declared_codes = [str(code) for code in profile_codes]
    else:
        declared_codes = []
    if declared_codes:
        if any(
            _condition_code_matches(requested_code, candidate_code)
            for candidate_code in declared_codes
        ):
            return "pass"
        return "fail"
    if allow_general_template:
        return "hold"
    blob = _candidate_blob(item)
    requested_rule = next(
        (rule for rule in CONDITION_RULES if rule["code"] == requested_code), None
    )
    if requested_rule and _contains_any(blob, requested_rule["keywords"]):
        return "pass"
    declared = bool(
        item.get("disease_domain")
        or item.get("disease_areas")
    )
    return "fail" if declared else "hold"


def _candidate_flag(
    item: Mapping[str, Any], key: str, *, default: bool
) -> bool:
    profile = _candidate_profile(item)
    value = profile.get(key)
    if value is None:
        value = item.get(key)
    return value if isinstance(value, bool) else default


def _is_external_candidate(item: Mapping[str, Any]) -> bool:
    return str(item.get("candidate_source") or "internal_catalog") == "external_catalog"


def _is_materialized(item: Mapping[str, Any]) -> bool:
    profile = _candidate_profile(item)
    status = str(
        profile.get("materialization_status")
        or item.get("materialization_status")
        or ("not_materialized" if _is_external_candidate(item) else "materialized")
    )
    return status in {"materialized", "full_local", "fixed_validation_only"}


def _is_application_eligible(item: Mapping[str, Any]) -> bool:
    return _candidate_flag(
        item,
        "application_eligible",
        default=not _is_external_candidate(item),
    )


def _is_algorithm_template(item: Mapping[str, Any]) -> bool:
    profile = _candidate_profile(item)
    return (
        str(profile.get("asset_kind") or item.get("asset_kind") or "")
        == "algorithm_template"
        or profile.get("target_task_weights") is False
        or item.get("target_task_weights") is False
    )


def _has_target_task_weights(item: Mapping[str, Any]) -> bool:
    if _is_algorithm_template(item):
        return False
    return _candidate_flag(item, "target_task_weights", default=True)


def _executor_registered(item: Mapping[str, Any]) -> bool:
    return _candidate_flag(
        item,
        "executor_registered",
        default=bool(item.get("entrypoint_id")),
    )


def _allowed_purposes(item: Mapping[str, Any]) -> set[str]:
    profile = _candidate_profile(item)
    license_profile = _as_mapping(profile.get("license"))
    values = (
        _as_mapping(item.get("policy")).get("allowed_purposes"),
        _as_mapping(item.get("policy")).get("allowed_actions"),
        _as_mapping(item.get("license")).get("allowed_purposes"),
        _as_mapping(item.get("license_metadata")).get("allowed_purposes"),
        license_profile.get("allowed_purposes"),
    )
    purposes: set[str] = set()
    for value in values:
        if isinstance(value, str):
            purposes.add(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            purposes.update(str(item) for item in value)
    canonical: set[str] = set()
    aliases = {
        "research": "research_analysis",
        "research_only": "research_analysis",
        "scientific_research": "research_analysis",
        "model_validation": "model_validation",
        "validation": "model_validation",
        "model_external_validation": "external_performance_validation",
        "external_validation": "external_performance_validation",
        "external_performance_validation": "external_performance_validation",
        "algorithm_performance_evaluation": "model_validation",
        "teaching": "teaching_demo",
        "teaching_demo": "teaching_demo",
        "commercial_validation": "commercial_validation",
    }
    for purpose in purposes:
        normalized = "_".join(
            part
            for part in purpose.strip().casefold().replace("-", " ").split()
            if part
        )
        if normalized:
            canonical.add(normalized)
        alias = aliases.get(normalized)
        if alias:
            canonical.add(alias)
        if "科研" in purpose:
            canonical.add("research_analysis")
        if "模型验证" in purpose:
            canonical.add("model_validation")
        if "外部" in purpose and "验证" in purpose:
            canonical.update({"model_validation", "external_performance_validation"})
        if "教学" in purpose:
            canonical.add("teaching_demo")
        if "商业" in purpose:
            canonical.add("commercial_validation")
    return canonical


def _check(code: str, result: str, reason: str, evidence: str) -> dict[str, str]:
    return {"code": code, "result": result, "reason": reason, "evidence": evidence}


def _worst_result(*results: str) -> str:
    return max(results, key={"pass": 0, "hold": 1, "fail": 2}.get)


def _relation_for_pair(
    relations: Sequence[Mapping[str, Any]], data_version_id: str, model_version_id: str
) -> Mapping[str, Any] | None:
    for relation in relations:
        relation_data_id = str(
            relation.get("data_version_id")
            or relation.get("data_product_version_id")
            or ""
        )
        relation_model_id = str(
            relation.get("model_version_id")
            or relation.get("model_product_version_id")
            or ""
        )
        if relation_data_id == data_version_id and relation_model_id == model_version_id:
            return relation
    return None


def _pair_stage(
    data: Mapping[str, Any],
    model: Mapping[str, Any],
    relation: Mapping[str, Any] | None,
) -> str:
    if relation and (
        str(relation.get("current_status")) == "verified"
        and str(relation.get("strongest_evidence_level")) == "platform_verification"
        and relation.get("public_visible") is not False
    ):
        return "verified_pair"
    if (
        _is_application_eligible(data)
        and _is_application_eligible(model)
        and _is_materialized(data)
        and _is_materialized(model)
        and _executor_registered(model)
    ):
        return "execution_ready"
    if _is_application_eligible(data) and _is_application_eligible(model):
        return "application_candidate"
    data_stage = str(_candidate_profile(data).get("catalog_stage") or "catalog_only")
    model_stage = str(_candidate_profile(model).get("catalog_stage") or "catalog_only")
    if data_stage in {"static_candidate", "application_candidate"} and model_stage in {
        "static_candidate",
        "application_candidate",
    }:
        return "static_candidate"
    return "catalog_only"


def _pair_evidence(
    relation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if relation is None:
        return {
            "relation_id": None,
            "status": "not_assessed",
            "level": "none",
            "public_visible": False,
        }
    return {
        "relation_id": str(relation.get("id") or "") or None,
        "status": str(relation.get("current_status") or "not_assessed"),
        "level": str(relation.get("strongest_evidence_level") or "none"),
        "public_visible": bool(relation.get("public_visible")),
    }


def _build_pair_checks(
    data: Mapping[str, Any],
    model: Mapping[str, Any],
    intent: Mapping[str, Any],
    relation: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    condition_code = str(intent["condition_code"])
    data_condition = _condition_match(data, condition_code)
    model_condition = _condition_match(
        model,
        condition_code,
        allow_general_template=_is_algorithm_template(model),
    )
    condition_result = _worst_result(data_condition, model_condition)
    requested_anatomy = intent.get("anatomical_site_code")
    data_anatomies = _candidate_anatomies(data)
    model_anatomies = _candidate_anatomies(model)
    if requested_anatomy:
        def anatomy_matches(values: set[str]) -> bool:
            if requested_anatomy == "multi_site":
                return (
                    "multi_site" in values
                    or "musculoskeletal_system" in values
                    or len(values - {"upper_limb", "lower_limb"}) >= 3
                )
            return str(requested_anatomy) in values

        anatomy_results = [
            "pass" if anatomy_matches(values) else "fail" if values else "hold"
            for values in (data_anatomies, model_anatomies)
        ]
        condition_result = _worst_result(condition_result, *anatomy_results)
    elif data_anatomies and model_anatomies and data_anatomies.isdisjoint(model_anatomies):
        condition_result = "fail"
    condition_reason = {
        "pass": "疾病主题与解剖部位在数据和模型版本元数据中一致。",
        "hold": "疾病主题可比较，但解剖部位或目标疾病元数据仍不完整。",
        "fail": "数据、模型或需求的疾病主题/解剖部位存在明确冲突。",
    }[condition_result]
    checks.append(_check("CONDITION_ANATOMY", condition_result, condition_reason, "locked version metadata"))

    data_modalities = _candidate_modalities(data)
    model_modalities = _candidate_modalities(model)
    task_family = str(intent["task_family"])
    expected_modality = str(intent.get("data_modality_code") or "unspecified")
    if task_family not in IMAGE_TASK_FAMILIES:
        data_image_only = bool(data_modalities & {"digital_pathology", "x_ray", "mr", "ct", "medical_image"}) and "structured_ehr" not in data_modalities
        model_image_only = bool(model_modalities & {"digital_pathology", "x_ray", "mr", "ct", "medical_image"}) and "structured_ehr" not in model_modalities
        if data_image_only or model_image_only:
            modality_result = "fail"
            modality_reason = "住院风险预测需要结构化临床时间线，影像分类输入不能替代该数据语义。"
        elif "structured_ehr" in data_modalities and "structured_ehr" in model_modalities:
            modality_result = "pass"
            modality_reason = "数据与模型均登记结构化临床时间线输入。"
        else:
            modality_result = "hold"
            modality_reason = "结构化临床时间线模态尚未在数据和模型两侧完整登记。"
    else:
        if expected_modality in {"x_ray", "digital_pathology", "mr", "ct"}:
            data_match = expected_modality in data_modalities
            model_match = expected_modality in model_modalities
        else:
            image_modalities = {"digital_pathology", "x_ray", "mr", "ct", "medical_image"}
            data_match = bool(data_modalities & image_modalities)
            model_match = bool(model_modalities & image_modalities)
        if data_match and model_match and not (
            data_modalities and model_modalities and data_modalities.isdisjoint(model_modalities)
        ):
            modality_result = "pass"
            modality_reason = "数据与模型的医学影像模态一致。"
        elif data_modalities and model_modalities:
            modality_result = "fail"
            modality_reason = "数据、模型与需求的影像模态存在明确冲突。"
        else:
            modality_result = "hold"
            modality_reason = "影像模态或摄影视图元数据仍不完整。"
    checks.append(_check("MODALITY_VIEW", modality_result, modality_reason, "modality and view profile"))

    model_tasks = _candidate_tasks(model)
    data_tasks = _candidate_tasks(data)
    if task_family not in IMAGE_TASK_FAMILIES and model_tasks & IMAGE_TASK_FAMILIES:
        task_result = "fail"
        task_reason = "需求是结构化住院风险预测，候选模型仅登记为影像分类/影像任务模型。"
    elif task_family in IMAGE_TASK_FAMILIES:
        model_match = task_family in model_tasks
        data_match = not data_tasks or task_family in data_tasks
        if model_match and data_match:
            task_result = "hold" if _is_algorithm_template(model) else "pass"
            task_reason = (
                "候选仅是通用算法模板，必须先用目标数据训练并冻结目标任务权重。"
                if task_result == "hold"
                else "任务类型与目标标签/结局一致。"
            )
        elif model_tasks:
            task_result = "fail"
            task_reason = "模型登记任务与研究目标任务不一致。"
        else:
            task_result = "hold"
            task_reason = "模型目标任务或目标标签元数据不完整。"
    else:
        compatible_risk_tasks = {task_family, "risk_prediction"}
        if task_family == "binary_classification":
            compatible_risk_tasks.add("binary_classification")
        if model_tasks & compatible_risk_tasks:
            task_result = "pass"
            task_reason = "模型登记的风险任务与目标结局类型一致。"
        elif model_tasks:
            task_result = "fail"
            task_reason = "模型登记任务与目标风险结局不一致。"
        else:
            task_result = "hold"
            task_reason = "模型未登记可验证的目标风险任务。"
    checks.append(_check("TASK_TARGET", task_result, task_reason, "task and target profile"))

    data_profile = _candidate_profile(data)
    model_profile = _candidate_profile(model)
    data_schema = (
        data_profile.get("label_schemas")
        or data_profile.get("schema")
        or item_value(data, "scale", "scope_metadata")
    )
    model_input = model_profile.get("input_schema") or item_value(
        model, "input_schema", "compatibility_metadata"
    )
    model_output = model_profile.get("output_schema") or model.get("output_schema")
    if model_input and model_output and (data_schema or "structured_ehr" in data_modalities):
        schema_result = "pass"
        schema_reason = "数据标签/字段与模型输入输出 Schema 均有版本级元数据。"
    else:
        schema_result = "hold"
        schema_reason = "输入、输出、标签或字段 Schema 仍需补齐后才能做正式兼容性检查。"
    checks.append(_check("INPUT_OUTPUT_SCHEMA", schema_result, schema_reason, "version schema metadata"))

    if task_family not in IMAGE_TASK_FAMILIES:
        temporal_blob = _flatten_public_metadata((model_input, model_output, model_profile)).casefold()
        temporal_terms = (
            str(intent.get("index_time_code") or ""),
            str(intent.get("outcome_code") or ""),
            str(intent.get("prediction_horizon") or ""),
        )
        temporal_match = all(
            not term or term in temporal_blob or term.replace("_days", "") in temporal_blob
            for term in temporal_terms
        )
        if temporal_match:
            population_result = "pass"
            population_reason = "模型元数据覆盖目标人群、预测时点和结局窗口。"
        else:
            population_result = "hold"
            population_reason = "目标人群、预测时点或结局窗口尚未完整绑定到模型版本。"
    else:
        population_result = "pass"
        population_reason = "未发现数据与模型适用人群的明确冲突；仍需正式申请复核。"
    checks.append(_check("POPULATION_TEMPORAL", population_result, population_reason, "population and temporal metadata"))

    requested_purpose = str(intent.get("purpose_code") or "research_analysis")
    purpose_sets = [_allowed_purposes(data), _allowed_purposes(model)]
    incompatible_purpose = any(values and requested_purpose not in values for values in purpose_sets)
    license_blob = _flatten_public_metadata(
        (data.get("license"), model.get("license"), data_profile.get("license"), model_profile.get("license"))
    ).casefold()
    if _contains_any(license_blob, ("prohibited", "禁止使用", "unavailable")) or incompatible_purpose:
        license_result = "fail"
        license_reason = "许可或用途范围明确不允许当前研究目的。"
    else:
        license_result = "pass"
        license_reason = "未发现许可与当前非临床研究用途的明确冲突。"
    checks.append(_check("LICENSE_PURPOSE", license_result, license_reason, "policy and license metadata"))

    if _is_application_eligible(data) and _is_application_eligible(model):
        eligibility_result = "pass"
        eligibility_reason = "数据与模型均来自可申请的已发布内部版本。"
    else:
        eligibility_result = "hold"
        eligibility_reason = "至少一个版本仍是目录/静态候选，只能比较，不能直接申请或执行。"
    checks.append(_check("CATALOG_ELIGIBILITY", eligibility_result, eligibility_reason, "governed catalog state"))

    evidence = _pair_evidence(relation)
    if evidence["status"] == "verified" and evidence["level"] == "platform_verification":
        evidence_result = "pass"
        evidence_reason = "已有公开的平台验证版本对证据。"
    elif evidence["level"] in {"runtime_execution", "platform_static_review", "external_declaration"}:
        evidence_result = "hold"
        evidence_reason = "已有组合证据，但尚未达到平台验证级。"
    else:
        evidence_result = "hold"
        evidence_reason = "尚无该锁定数据版本与模型版本的正式组合证据。"
    checks.append(_check("PAIR_EVIDENCE", evidence_result, evidence_reason, "dataset-model relation"))
    return checks


def item_value(item: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return None


def _hard_gate_status(checks: Sequence[Mapping[str, str]]) -> str:
    gate_checks = [check for check in checks if check["code"] != "PAIR_EVIDENCE"]
    if any(check["result"] == "fail" for check in gate_checks):
        return "fail"
    if any(check["result"] == "hold" for check in gate_checks):
        return "hold"
    return "pass"


def _pair_score(
    checks: Sequence[Mapping[str, str]], stage: str, evidence: Mapping[str, Any], gate_status: str
) -> dict[str, Any]:
    check_by_code = {check["code"]: check for check in checks}
    component_check_codes = {"DISEASE_ANATOMY": "CONDITION_ANATOMY"}
    components: list[dict[str, Any]] = []
    for code, weight in PAIR_SCORE_WEIGHTS:
        if code == "EVIDENCE":
            level_points = {
                "none": 0,
                "external_declaration": 2,
                "platform_static_review": 3,
                "runtime_execution": 4,
                "platform_verification": 5,
            }
            earned = level_points.get(str(evidence.get("level") or "none"), 0)
            reason = check_by_code["PAIR_EVIDENCE"]["reason"]
        elif code == "LOCAL_OPERABILITY":
            earned = {
                "catalog_only": 0,
                "static_candidate": 1,
                "application_candidate": 3,
                "execution_ready": 5,
                "verified_pair": 5,
            }[stage]
            reason = {
                "catalog_only": "仅目录比较，未形成本地运行条件。",
                "static_candidate": "静态候选尚未形成申请或执行条件。",
                "application_candidate": "可进入申请，但尚未登记完整执行条件。",
                "execution_ready": "版本已物化且固定执行入口已登记。",
                "verified_pair": "版本对已有平台验证证据。",
            }[stage]
        else:
            check = check_by_code[component_check_codes.get(code, code)]
            earned = {
                "pass": weight,
                "hold": weight // 2,
                "fail": 0,
            }[check["result"]]
            reason = check["reason"]
        components.append(
            {"code": code, "earned": earned, "weight": weight, "reason": reason}
        )
    return {
        "total": sum(item["earned"] for item in components),
        "max_total": 100,
        "ruleset_version": PAIR_RULESET_VERSION,
        "ranking_eligible": gate_status != "fail",
        "components": components,
    }


def _build_pair_candidate(
    data: Mapping[str, Any],
    model: Mapping[str, Any],
    intent: Mapping[str, Any],
    relations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    data_version_id = str(data.get("version_id") or "")
    model_version_id = str(model.get("version_id") or "")
    relation = _relation_for_pair(relations, data_version_id, model_version_id)
    stage = _pair_stage(data, model, relation)
    checks = _build_pair_checks(data, model, intent, relation)
    gate_status = _hard_gate_status(checks)
    evidence = _pair_evidence(relation)
    workflow_role = (
        "incompatible"
        if gate_status == "fail"
        else "training_required"
        if _is_algorithm_template(model)
        else "validation_ready"
        if _has_target_task_weights(model)
        else "metadata_review_required"
    )
    can_select = gate_status == "pass" and stage in {
        "application_candidate",
        "execution_ready",
        "verified_pair",
    }
    can_execute = gate_status == "pass" and stage in {"execution_ready", "verified_pair"}
    reasons = [check["reason"] for check in checks if check["result"] == "pass"]
    limitations = [check["reason"] for check in checks if check["result"] != "pass"]
    return {
        "pair_key": f"{data_version_id}:{model_version_id}",
        "data_product_id": str(data.get("product_id") or ""),
        "data_product_code": str(data.get("product_code") or ""),
        "data_version_id": data_version_id,
        "data_name": str(data.get("name") or ""),
        "model_product_id": str(model.get("product_id") or ""),
        "model_product_code": str(model.get("product_code") or ""),
        "model_version_id": model_version_id,
        "model_name": str(model.get("name") or ""),
        "stage": stage,
        "workflow_role": workflow_role,
        "hard_gate": {
            "status": gate_status,
            "checks": checks,
            "overrides_score": True,
        },
        "score": _pair_score(checks, stage, evidence, gate_status),
        "reasons": reasons,
        "limitations": limitations,
        "evidence": evidence,
        "actions": {
            "can_compare": True,
            "can_select": can_select,
            "can_apply": can_select,
            "can_execute": can_execute,
        },
    }


def _candidate_preselection_score(
    item: Mapping[str, Any], intent: Mapping[str, Any], *, model: bool
) -> int:
    condition_result = _condition_match(
        item,
        str(intent["condition_code"]),
        allow_general_template=model and _is_algorithm_template(item),
    )
    score = {"pass": 60, "hold": 20, "fail": 0}[condition_result]
    modalities = _candidate_modalities(item)
    expected_modality = str(intent.get("data_modality_code") or "unspecified")
    if expected_modality == "structured_ehr" and "structured_ehr" in modalities:
        score += 20
    elif expected_modality in modalities:
        score += 20
    elif expected_modality == "medical_image" and modalities & {
        "digital_pathology",
        "x_ray",
        "mr",
        "ct",
        "medical_image",
    }:
        score += 15
    tasks = _candidate_tasks(item)
    if str(intent["task_family"]) in tasks:
        score += 20
    elif not model and not tasks:
        score += 5
    return score


def _rank_pair_candidates(
    data_products: Sequence[Mapping[str, Any]],
    model_products: Sequence[Mapping[str, Any]],
    intent: Mapping[str, Any],
    relations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    selected_data = sorted(
        data_products,
        key=lambda item: (
            -_candidate_preselection_score(item, intent, model=False),
            str(item.get("name") or "").casefold(),
            str(item.get("version_id") or ""),
        ),
    )[:25]
    selected_models = sorted(
        model_products,
        key=lambda item: (
            -_candidate_preselection_score(item, intent, model=True),
            str(item.get("name") or "").casefold(),
            str(item.get("version_id") or ""),
        ),
    )[:25]
    candidates = [
        _build_pair_candidate(data, model, intent, relations)
        for data in selected_data
        for model in selected_models
        if data.get("version_id") and model.get("version_id")
    ]
    gate_rank = {"pass": 0, "hold": 1, "fail": 2}
    check_rank = {"pass": 0, "hold": 1, "fail": 2}

    def ranked_check(item: Mapping[str, Any], code: str) -> int:
        checks = _as_mapping(item.get("hard_gate")).get("checks")
        if isinstance(checks, Sequence) and not isinstance(checks, (str, bytes)):
            for check in checks:
                check_mapping = _as_mapping(check)
                if check_mapping.get("code") == code:
                    return check_rank.get(str(check_mapping.get("result")), 1)
        return 1

    def curated_catalog_rank(item: Mapping[str, Any], side: str) -> int:
        product_code = str(item.get(f"{side}_product_code") or "")
        if "medtrust-orthopedic-curated-" in product_code:
            return 0
        return 1

    return sorted(
        candidates,
        key=lambda item: (
            gate_rank[item["hard_gate"]["status"]],
            ranked_check(item, "CONDITION_ANATOMY"),
            ranked_check(item, "MODALITY_VIEW"),
            ranked_check(item, "TASK_TARGET"),
            -item["score"]["total"],
            curated_catalog_rank(item, "data"),
            curated_catalog_rank(item, "model"),
            item["pair_key"],
        ),
    )[:20]


def _pair_matching_status(candidates: Sequence[Mapping[str, Any]]) -> str:
    if any(_as_mapping(item.get("actions")).get("can_select") for item in candidates):
        return "ready"
    if any(_as_mapping(item.get("hard_gate")).get("status") == "hold" for item in candidates):
        return "on_hold"
    if candidates:
        return "incompatible"
    return "catalog_gap"


def recommend_research_demand(
    demand_text: str,
    data_products: Sequence[Mapping[str, Any]],
    model_products: Sequence[Mapping[str, Any]],
    *,
    pair_data_products: Sequence[Mapping[str, Any]] | None = None,
    pair_model_products: Sequence[Mapping[str, Any]] | None = None,
    pair_relations: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Compile a research demand and rank only pre-filtered governed catalog items.

    This function is deliberately deterministic and side-effect free. Callers are
    responsible for supplying the same eligible catalog projection used by the
    application wizard.
    """

    text = _normalise_text(demand_text)
    if len(text) < 10:
        raise ValueError("Research demand must contain at least 10 characters")
    if len(text) > 2000:
        raise ValueError("Research demand must contain at most 2000 characters")

    blocking_reasons = _detect_blocking_reasons(text)
    if blocking_reasons:
        return {
            "schema_version": ASSISTANT_SCHEMA,
            "assistant_version": "deterministic-rules-v2",
            "pair_candidates_schema_version": PAIR_CANDIDATE_SCHEMA,
            "status": "blocked",
            "pair_matching_status": "blocked",
            "normalized_intent": {},
            "pair_requirement_snapshot": {},
            "clarifications": [],
            "blocking_reasons": blocking_reasons,
            "draft_patch": {},
            "data_recommendations": [],
            "model_recommendations": [],
            "pair_candidates": [],
            "pair_summary": {"total": 0, "pass": 0, "hold": 0, "fail": 0},
            "catalog_gaps": [],
            "method_suggestions": [],
            "can_apply_draft": False,
            "can_apply_catalog_selection": False,
            "can_apply_pair_selection": False,
            "boundary": dict(BOUNDARY),
            "disclaimer": "该请求超出非临床研究需求助手的安全边界，未查询或返回目录候选。",
        }

    condition = _detect_condition(text)
    outcome_code, outcome_label, task_family = _detect_outcome(text)
    anatomical_site_code, anatomical_site_label = _detect_anatomical_site(text)
    index_time_code, index_time_label = _detect_index_time(text)
    prediction_horizon, prediction_horizon_label = _detect_horizon(text)
    study_mode_code, study_mode_label = _detect_study_mode(text)
    care_setting_code, care_setting_label = _detect_care_setting(text)
    data_modality_code, data_modality_label = _detect_data_modality(text)
    if task_family in IMAGE_TASK_FAMILIES:
        population_code = "pathology_images"
        population_label = f"{condition['label']}影像"
    else:
        population_code, population_label = _detect_population(text, condition["label"])
    inclusion_criteria, exclusion_criteria = _build_cohort_criteria(
        text,
        condition_code=condition["code"],
        condition_label=condition["label"],
        population_code=population_code,
        care_setting_code=care_setting_code,
        care_setting_label=care_setting_label,
    )
    intent = {
        "condition_code": condition["code"],
        "condition_label": condition["label"],
        "population_code": population_code,
        "population_label": population_label,
        "outcome_code": outcome_code,
        "outcome_label": outcome_label,
        "task_family": task_family,
        "anatomical_site_code": anatomical_site_code,
        "anatomical_site_label": anatomical_site_label,
        "index_time_code": index_time_code,
        "index_time_label": index_time_label,
        "prediction_horizon": prediction_horizon,
        "prediction_horizon_label": prediction_horizon_label,
        "study_mode_code": study_mode_code,
        "study_mode_label": study_mode_label,
        "care_setting_code": care_setting_code,
        "care_setting_label": care_setting_label,
        "data_modality_code": data_modality_code,
        "data_modality_label": data_modality_label,
        "inclusion_criteria": inclusion_criteria,
        "exclusion_criteria": exclusion_criteria,
        "evaluation_outputs": _detect_evaluation_outputs(text),
        "purpose_code": (
            "model_validation" if task_family in IMAGE_TASK_FAMILIES else "research_analysis"
        ),
        "catalog_terms": list(condition["catalog_terms"]),
    }
    intent["concept_mappings"] = _concept_mappings(intent)
    clarifications = _build_clarifications(intent)
    intent["research_definition_status"] = (
        "needs_clarification" if clarifications else "defined"
    )
    concept_by_role = {
        item["semantic_role"]: {
            "display": item["text"],
            "local_rule_code": intent.get(f"{item['semantic_role']}_code"),
            "mapping_status": item["mapping_status"],
            "standard_system": item["coding_system"],
            "standard_code": item["code"],
        }
        for item in intent["concept_mappings"]
    }
    intent["study_definition"] = {
        "target_population": {
            "label": population_label,
            "care_setting": {
                "code": care_setting_code,
                "label": care_setting_label,
                "source": "explicit" if care_setting_code != "unspecified" else "unspecified",
            },
            "inclusion_criteria": inclusion_criteria,
            "exclusion_criteria": exclusion_criteria,
        },
        "index_time": {
            "code": index_time_code,
            "label": index_time_label,
        },
        "outcome": {
            "code": outcome_code,
            "label": outcome_label,
            "task_family": task_family,
        },
        "prediction_window": {
            "code": prediction_horizon,
            "label": prediction_horizon_label,
        },
        "operation_mode": {
            "code": study_mode_code,
            "label": study_mode_label,
            "source": "explicit" if study_mode_code != "unspecified" else "unspecified",
        },
        "modalities": []
        if data_modality_code == "unspecified"
        else [
            {
                "code": data_modality_code,
                "label": data_modality_label,
                "source": "explicit",
            }
        ],
        "terminology": concept_by_role,
        "evaluation_outputs": intent["evaluation_outputs"],
    }
    draft_patch = _build_draft_patch(intent)
    method_suggestions = _method_suggestions(task_family)

    data_recommendations: list[dict[str, Any]] = []
    model_recommendations: list[dict[str, Any]] = []
    pair_candidates: list[dict[str, Any]] = []
    catalog_gaps: list[dict[str, Any]] = []
    if not clarifications:
        data_recommendations = _rank_data_products(data_products, intent)
        model_recommendations = _rank_model_products(model_products, intent)
        pair_candidates = _rank_pair_candidates(
            pair_data_products if pair_data_products is not None else data_products,
            pair_model_products if pair_model_products is not None else model_products,
            intent,
            pair_relations,
        )
        has_broader_pair_catalog = (
            pair_data_products is not None or pair_model_products is not None
        )
        has_non_failed_pair = has_broader_pair_catalog and any(
            item["hard_gate"]["status"] in {"pass", "hold"}
            for item in pair_candidates
        )
        if has_non_failed_pair and (not data_recommendations or not model_recommendations):
            catalog_gaps.append(
                {
                    "code": "NO_APPLICATION_ELIGIBLE_PAIR",
                    "message": (
                        "已找到可比较的数据—模型目录候选，但尚无同时满足申请资格与固定执行条件的组合。"
                    ),
                    "assessed_count": len(pair_candidates),
                }
            )
        elif not data_recommendations:
            modality_gap_label = (
                data_modality_label
                if data_modality_code != "unspecified"
                else "医学影像"
                if task_family in IMAGE_TASK_FAMILIES
                else "结构化/纵向临床数据"
            )
            catalog_gaps.append(
                {
                    "code": "NO_ELIGIBLE_DATA_PRODUCT",
                    "message": f"当前已发布目录中没有同时匹配疾病领域与{modality_gap_label}模态的数据版本。",
                    "assessed_count": len(data_products),
                }
            )
        if not has_non_failed_pair and not model_recommendations:
            catalog_gaps.append(
                {
                    "code": "NO_ELIGIBLE_MODEL_PRODUCT",
                    "message": "当前已发布目录中没有同时匹配疾病领域、任务类型与输入模态的固定模型版本。",
                    "assessed_count": len(model_products),
                }
            )

    status = (
        "needs_clarification"
        if clarifications
        else "ready"
        if data_recommendations and model_recommendations
        else "catalog_gap"
    )
    public_intent = {key: value for key, value in intent.items() if key != "catalog_terms"}
    pair_status = "needs_clarification" if clarifications else _pair_matching_status(pair_candidates)
    pair_counts = {
        state: sum(
            item["hard_gate"]["status"] == state for item in pair_candidates
        )
        for state in ("pass", "hold", "fail")
    }
    return {
        "schema_version": ASSISTANT_SCHEMA,
        "assistant_version": "deterministic-rules-v2",
        "pair_candidates_schema_version": PAIR_CANDIDATE_SCHEMA,
        "status": status,
        "pair_matching_status": pair_status,
        "normalized_intent": public_intent,
        "pair_requirement_snapshot": {
            "condition": intent["condition_code"],
            "anatomical_site": intent["anatomical_site_code"],
            "modality": intent["data_modality_code"],
            "task_type": intent["task_family"],
            "target_definition": intent["outcome_code"],
            "operation_mode": intent["study_mode_code"],
            "purpose_code": intent["purpose_code"],
        },
        "clarifications": clarifications,
        "blocking_reasons": [],
        "draft_patch": draft_patch,
        "data_recommendations": data_recommendations,
        "model_recommendations": model_recommendations,
        "pair_candidates": pair_candidates,
        "pair_summary": {"total": len(pair_candidates), **pair_counts},
        "catalog_gaps": catalog_gaps,
        "method_suggestions": method_suggestions,
        "can_apply_draft": True,
        "can_apply_catalog_selection": bool(
            status == "ready" and data_recommendations and model_recommendations
        ),
        "can_apply_pair_selection": any(
            item["actions"]["can_select"] for item in pair_candidates
        ),
        "boundary": dict(BOUNDARY),
        "disclaimer": (
            "推荐仅基于已发布目录元数据，不代表数据授权、模型性能、临床有效性或执行许可；"
            "应用草稿后仍须完成兼容性检查、多方审核和数字合约。"
        ),
    }
