from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.routes import applications as application_routes
from app.modules.applications.demand_assistant import recommend_research_demand
from app.modules.external_catalog.orthopedic_seed import load_orthopedic_catalog_seed


FRACTURE_REQUEST = "我想构建一个骨折患者住院风险预测模型"


def _fracture_data(version_id: str = "data-fracture-v1") -> dict[str, object]:
    return {
        "product_id": "data-fracture",
        "version_id": version_id,
        "product_code": "DP-FRACTURE-EHR",
        "name": "骨折住院结构化研究队列",
        "provider": "示例医院",
        "provider_organization_id": "hospital-1",
        "disease_domain": "orthopedics fracture trauma",
        "modality": "structured_ehr longitudinal",
        "version": "v1.0",
        "is_demo": True,
        "scale": {"patient_count": 12000, "longitudinal": True},
        "quality": {"completeness": 0.95},
        "policy": {"use_mode": "controlled_compute"},
    }


def _fracture_model(version_id: str = "model-fracture-v1") -> dict[str, object]:
    return {
        "product_id": "model-fracture",
        "version_id": version_id,
        "product_code": "MP-FRACTURE-RISK",
        "name": "骨折患者再入院风险研究模型",
        "provider": "示例模型方",
        "provider_organization_id": "model-provider-1",
        "disease_domain": "orthopedics fracture",
        "task_type": "binary_classification risk_prediction readmission",
        "modality": "structured_ehr",
        "version": "v1.0",
        "is_demo": True,
        "non_clinical": True,
        "input_schema": {"available_at": "admission"},
        "output_schema": {"label": "30_day_readmission"},
        "policy": {"use_mode": "controlled_compute"},
        "license": {"research_only": True},
    }


def _pathmnist_data() -> dict[str, object]:
    return {
        "product_id": "data-pathmnist",
        "version_id": "data-pathmnist-v1",
        "product_code": "DP-PATHMNIST",
        "name": "PathMNIST 数字病理图像",
        "provider": "演示医院",
        "provider_organization_id": "hospital-demo",
        "disease_domain": "digital_pathology",
        "modality": "digital_pathology image",
        "version": "v1.0",
        "is_demo": True,
        "scale": {"image_count": 20},
        "quality": {},
        "policy": {"use_mode": "controlled_compute"},
    }


def _pathmnist_model() -> dict[str, object]:
    return {
        "product_id": "model-pathmnist",
        "version_id": "model-pathmnist-v1",
        "product_code": "MP-PATHMNIST-RESNET18",
        "name": "PathMNIST ResNet-18 图像分类模型",
        "provider": "演示模型方",
        "provider_organization_id": "model-demo",
        "disease_domain": "digital_pathology",
        "task_type": "image_classification",
        "modality": "digital_pathology image",
        "version": "v1.0",
        "is_demo": True,
        "non_clinical": True,
        "input_schema": {"shape": [28, 28, 3]},
        "output_schema": {"classes": 9},
        "policy": {"use_mode": "controlled_compute"},
        "license": {"research_only": True},
    }


def _fracture_xray_data(
    version_id: str = "data-fracture-xray-v1",
    *,
    source: str = "internal_catalog",
) -> dict[str, object]:
    external = source == "external_catalog"
    return {
        "candidate_source": source,
        "product_id": "data-fracture-xray",
        "version_id": version_id,
        "product_code": "DP-FRACTURE-XRAY",
        "name": "腕部骨折 X 线影像数据",
        "provider": "可信公开来源",
        "disease_domain": "orthopedics fracture",
        "modality": ["x_ray"],
        "task_type": ["image_classification"],
        "version": "v1.0",
        "non_clinical": True,
        "application_eligible": not external,
        "materialization_status": "not_materialized" if external else "materialized",
        "snapshot_digest": None if external else "sha256:" + "1" * 64,
        "policy": {"allowed_purposes": ["model_validation"]},
        "license": {"license_scope": "open_with_attribution"},
        "profile": {
            "catalog_stage": "catalog_only" if external else "application_candidate",
            "condition_codes": ["fracture"],
            "anatomical_sites": ["wrist"],
            "modalities": ["x_ray"],
            "views": ["ap", "lateral"],
            "supported_tasks": ["image_classification"],
            "label_schemas": [{"target_definition": "fracture_presence"}],
            "population": {"age_group": "all_ages"},
            "split_protocol": {"patient_split_status": "unverifiable"},
            "application_eligible": not external,
            "materialization_status": "not_materialized" if external else "materialized",
        },
    }


def _fracture_xray_model(
    version_id: str = "model-fracture-xray-v1",
    *,
    source: str = "internal_catalog",
    algorithm_template: bool = False,
) -> dict[str, object]:
    external = source == "external_catalog"
    return {
        "candidate_source": source,
        "product_id": "model-fracture-xray",
        "version_id": version_id,
        "product_code": "MP-FRACTURE-XRAY",
        "name": (
            "MobileNetV3 通用分类模板"
            if algorithm_template
            else "腕部骨折 X 线分类模型"
        ),
        "provider": "可信模型来源",
        "disease_domain": "general imaging" if algorithm_template else "orthopedics fracture",
        "task_type": "image_classification",
        "modality": ["x_ray"],
        "version": "v1.0",
        "non_clinical": True,
        "application_eligible": not external,
        "materialization_status": "not_materialized" if external else "materialized",
        "entrypoint_id": None if external else "fracture_xray_v1",
        "snapshot_digest": None if external else "sha256:" + "2" * 64,
        "policy": {"allowed_purposes": ["model_validation"]},
        "license": {"license_scope": "open_with_attribution"},
        "profile": {
            "catalog_stage": (
                "catalog_only"
                if algorithm_template
                else "static_candidate"
                if external
                else "application_candidate"
            ),
            "asset_kind": "algorithm_template" if algorithm_template else "target_task_weights",
            "target_task_weights": not algorithm_template,
            "condition_codes": [] if algorithm_template else ["fracture"],
            "anatomical_sites": [] if algorithm_template else ["wrist"],
            "modalities": ["x_ray"],
            "task_type": "image_classification",
            "target_definition": None if algorithm_template else "fracture_presence",
            "operation_modes": ["training"] if algorithm_template else ["validation", "inference"],
            "input_schema": {"channels": 1, "sample_unit": "image"},
            "output_schema": {"target_definition": "fracture_presence"},
            "application_eligible": not external,
            "materialization_status": "not_materialized" if external else "materialized",
            "executor_registered": not external,
            "platform_validation": "not_validated",
        },
    }


def test_ambiguous_fracture_request_returns_questions_not_final_catalog_choices() -> None:
    result = recommend_research_demand(
        FRACTURE_REQUEST,
        data_products=[_fracture_data()],
        model_products=[_fracture_model()],
    )

    assert result["status"] == "needs_clarification"
    assert result["normalized_intent"]["condition_code"] == "fracture"
    assert result["normalized_intent"]["outcome_code"] == "inpatient_risk_unspecified"
    assert {
        "outcome_definition",
        "index_time",
        "prediction_horizon",
    } <= {item["code"] for item in result["clarifications"]}
    assert result["data_recommendations"] == []
    assert result["model_recommendations"] == []
    assert result["pair_candidates"] == []
    assert result["pair_matching_status"] == "needs_clarification"
    assert result["draft_patch"]["profile"]["clinical_diagnosis"] is False
    assert "project_lead" not in result["draft_patch"]["profile"]
    assert "contact" not in result["draft_patch"]["profile"]


def test_complete_research_request_ranks_governed_fracture_products_stably() -> None:
    text = "使用入院时可获得的成人骨折结构化电子病历，预测入院后30天内再入院风险，用于科研分析"
    data_products = [_pathmnist_data(), _fracture_data("data-z"), _fracture_data("data-a")]
    model_products = [_pathmnist_model(), _fracture_model("model-z"), _fracture_model("model-a")]

    first = recommend_research_demand(text, data_products, model_products)
    second = recommend_research_demand(text, list(reversed(data_products)), list(reversed(model_products)))

    assert first["status"] == "ready"
    assert first["normalized_intent"]["task_family"] == "binary_classification"
    assert first["normalized_intent"]["index_time_code"] == "admission"
    assert first["normalized_intent"]["prediction_horizon"] == "30_days"
    assert [item["version_id"] for item in first["data_recommendations"]] == [
        "data-a",
        "data-z",
    ]
    assert [item["version_id"] for item in first["model_recommendations"]] == [
        "model-a",
        "model-z",
    ]
    assert first["data_recommendations"] == second["data_recommendations"]
    assert first["model_recommendations"] == second["model_recommendations"]
    assert all(item["reasons"] for item in first["data_recommendations"])
    assert all(item["reasons"] for item in first["model_recommendations"])
    assert first["can_apply_catalog_selection"] is True
    assert first["pair_candidates"]
    assert first["pair_candidates"] == second["pair_candidates"]


def test_pair_contract_is_additive_and_uses_a_100_point_breakdown() -> None:
    result = recommend_research_demand(
        "我想使用腕部 X 线影像验证骨折图像分类模型，用于科研分析",
        data_products=[_fracture_xray_data()],
        model_products=[_fracture_xray_model()],
    )

    assert result["schema_version"] == "phase5.14/research-demand-assistant/v1"
    assert result["pair_candidates_schema_version"] == "medtrust.data-model-match/v1"
    assert result["normalized_intent"]["purpose_code"] == "model_validation"
    assert result["pair_requirement_snapshot"]["purpose_code"] == "model_validation"
    assert result["data_recommendations"]
    assert result["model_recommendations"]
    assert result["pair_matching_status"] == "ready"
    assert result["can_apply_pair_selection"] is True

    pair = result["pair_candidates"][0]
    assert pair["pair_key"] == "data-fracture-xray-v1:model-fracture-xray-v1"
    assert pair["stage"] == "execution_ready"
    assert pair["workflow_role"] == "validation_ready"
    assert pair["hard_gate"]["status"] == "pass"
    assert pair["actions"] == {
        "can_compare": True,
        "can_select": True,
        "can_apply": True,
        "can_execute": True,
    }
    assert pair["score"]["max_total"] == 100
    assert sum(item["weight"] for item in pair["score"]["components"]) == 100
    assert sum(item["earned"] for item in pair["score"]["components"]) == pair["score"]["total"]
    assert {item["code"] for item in pair["score"]["components"]} == {
        "DISEASE_ANATOMY",
        "MODALITY_VIEW",
        "TASK_TARGET",
        "INPUT_OUTPUT_SCHEMA",
        "POPULATION_TEMPORAL",
        "LICENSE_PURPOSE",
        "EVIDENCE",
        "LOCAL_OPERABILITY",
    }


def test_allowed_actions_are_honored_as_governed_purpose_codes() -> None:
    data = _fracture_xray_data()
    model = _fracture_xray_model()
    data["policy"] = {
        "use_mode": "controlled_compute",
        "allowed_actions": ["model_validation"],
    }
    model["policy"] = {
        "use_mode": "controlled_compute",
        "allowed_actions": ["model validation"],
    }

    result = recommend_research_demand(
        "我想使用腕部 X 线影像验证骨折图像分类模型，用于科研分析",
        data_products=[data],
        model_products=[model],
    )

    pair = result["pair_candidates"][0]
    license_check = next(
        check
        for check in pair["hard_gate"]["checks"]
        if check["code"] == "LICENSE_PURPOSE"
    )
    assert license_check["result"] == "pass"
    assert pair["hard_gate"]["status"] == "pass"


def test_catalog_revalidation_is_not_treated_as_model_validation() -> None:
    data = _fracture_xray_data()
    model = _fracture_xray_model()
    model["policy"] = {"allowed_purposes": ["governance_revalidation"]}

    result = recommend_research_demand(
        "我想使用腕部 X 线影像验证骨折图像分类模型，用于科研分析",
        data_products=[data],
        model_products=[model],
    )

    pair = result["pair_candidates"][0]
    license_check = next(
        check
        for check in pair["hard_gate"]["checks"]
        if check["code"] == "LICENSE_PURPOSE"
    )
    assert license_check["result"] == "fail"
    assert pair["actions"]["can_select"] is False


def test_external_algorithm_template_is_compare_only_and_training_required() -> None:
    result = recommend_research_demand(
        "我想使用腕部 X 线影像训练骨折图像分类模型，用于科研分析",
        data_products=[],
        model_products=[],
        pair_data_products=[_fracture_xray_data(source="external_catalog")],
        pair_model_products=[
            _fracture_xray_model(source="external_catalog", algorithm_template=True)
        ],
    )

    assert result["status"] == "catalog_gap"
    assert result["data_recommendations"] == []
    assert result["model_recommendations"] == []
    assert result["pair_matching_status"] == "on_hold"
    assert result["catalog_gaps"] == [
        {
            "code": "NO_APPLICATION_ELIGIBLE_PAIR",
            "message": "已找到可比较的数据—模型目录候选，但尚无同时满足申请资格与固定执行条件的组合。",
            "assessed_count": 1,
        }
    ]
    assert "数字病理" not in result["catalog_gaps"][0]["message"]
    pair = result["pair_candidates"][0]
    assert pair["stage"] == "catalog_only"
    assert pair["workflow_role"] == "training_required"
    assert pair["hard_gate"]["status"] == "hold"
    assert pair["actions"] == {
        "can_compare": True,
        "can_select": False,
        "can_apply": False,
        "can_execute": False,
    }
    assert any("训练" in reason for reason in pair["limitations"])


def test_xray_catalog_gap_uses_the_requested_modality_instead_of_pathology() -> None:
    result = recommend_research_demand(
        "我想用成人多部位骨骼 X 光判断是否存在骨折，输出准确率和混淆矩阵。",
        data_products=[],
        model_products=[],
    )

    data_gap = next(
        item
        for item in result["catalog_gaps"]
        if item["code"] == "NO_ELIGIBLE_DATA_PRODUCT"
    )
    assert "X 线影像" in data_gap["message"]
    assert "数字病理" not in data_gap["message"]


def test_static_and_application_candidate_stages_do_not_claim_execution() -> None:
    data = _fracture_xray_data()
    external_model = _fracture_xray_model(source="external_catalog")
    static_result = recommend_research_demand(
        "我想使用腕部 X 线影像验证骨折图像分类模型，用于科研分析",
        data_products=[data],
        model_products=[],
        pair_data_products=[data],
        pair_model_products=[external_model],
    )
    static_pair = static_result["pair_candidates"][0]
    assert static_pair["stage"] == "static_candidate"
    assert static_pair["workflow_role"] == "validation_ready"
    assert static_pair["hard_gate"]["status"] == "hold"
    assert static_pair["actions"]["can_select"] is False
    assert static_pair["actions"]["can_execute"] is False

    internal_model = _fracture_xray_model()
    internal_model["entrypoint_id"] = None
    internal_model["profile"]["executor_registered"] = False
    application_result = recommend_research_demand(
        "我想使用腕部 X 线影像验证骨折图像分类模型，用于科研分析",
        data_products=[data],
        model_products=[internal_model],
    )
    application_pair = application_result["pair_candidates"][0]
    assert application_pair["stage"] == "application_candidate"
    assert application_pair["hard_gate"]["status"] == "pass"
    assert application_pair["actions"]["can_select"] is True
    assert application_pair["actions"]["can_execute"] is False


def test_knee_osteoarthritis_xray_grading_reaches_static_pair_matching() -> None:
    data = _fracture_xray_data(source="external_catalog")
    data.update(
        {
            "name": "OAI 膝骨关节炎 X 线数据",
            "disease_domain": "knee osteoarthritis",
            "task_type": ["ordinal_classification"],
        }
    )
    data["profile"].update(
        {
            "condition_codes": ["knee_osteoarthritis"],
            "anatomical_sites": ["knee"],
            "supported_tasks": ["ordinal_classification"],
            "label_schemas": [{"target_definition": "severity_grade"}],
        }
    )
    model = _fracture_xray_model(source="external_catalog")
    model.update(
        {
            "name": "OAI KL Grade Baseline",
            "disease_domain": "knee osteoarthritis",
            "task_type": "ordinal_classification",
        }
    )
    model["profile"].update(
        {
            "condition_codes": ["knee_osteoarthritis"],
            "anatomical_sites": ["knee"],
            "task_type": "ordinal_classification",
            "target_definition": "severity_grade",
            "output_schema": {"target_definition": "severity_grade", "classes": 5},
        }
    )

    result = recommend_research_demand(
        "我想对膝关节 X 光进行 KL 0 到 4 级骨关节炎分级，并比较基线模型和注意力模型。",
        data_products=[],
        model_products=[],
        pair_data_products=[data],
        pair_model_products=[model],
    )

    assert result["clarifications"] == []
    assert result["normalized_intent"]["condition_code"] == "knee_osteoarthritis"
    assert result["normalized_intent"]["task_family"] == "ordinal_classification"
    assert result["pair_matching_status"] == "on_hold"
    pair = result["pair_candidates"][0]
    assert pair["hard_gate"]["status"] == "hold"
    assert pair["workflow_role"] == "validation_ready"
    assert not any(
        check["result"] == "fail" for check in pair["hard_gate"]["checks"]
    )


def test_real_orthopedic_seed_ranks_fracatlas_training_pair_first() -> None:
    document = load_orthopedic_catalog_seed()
    pair_data_products = [
        {
            "candidate_source": "external_catalog",
            "product_id": item["external_id"],
            "version_id": item["external_id"],
            "product_code": item["external_id"],
            "name": item["display_name_cn"],
            "provider": item["official_source_name"],
            "disease_domain": item["disease_areas"],
            "modality": item["modalities"],
            "task_type": item["task_types"],
            "scale": {"sample_count": item["sample_count"]},
            "quality": item["quality_flags"],
            "license": {
                "name": item["license_name"],
                "status": item["license_status"],
            },
            "profile": item["medtrust_profile"],
            "application_eligible": False,
            "materialization_status": "not_materialized",
        }
        for item in document["datasets"]
    ]
    pair_model_products = [
        {
            "candidate_source": "external_catalog",
            "product_id": item["external_model_id"],
            "version_id": item["external_model_id"],
            "product_code": item["external_model_id"],
            "name": item["display_name_cn"],
            "provider": item["upstream_provider"],
            "disease_domain": item["disease_areas"],
            "modality": item["modalities"],
            "task_type": item["task_types"],
            "input_schema": item["input_schema"],
            "output_schema": item["output_schema"],
            "license": {
                "name": item["license_name"],
                "status": item["license_status"],
            },
            "profile": item["medtrust_profile"],
            "application_eligible": False,
            "materialization_status": item["execution_status"],
        }
        for item in document["models"]
    ]

    result = recommend_research_demand(
        "我想用成人多部位骨骼 X 光判断是否存在骨折，模型要小、能在 CPU 上运行，并输出准确率和混淆矩阵。",
        data_products=[],
        model_products=[],
        pair_data_products=pair_data_products,
        pair_model_products=pair_model_products,
    )

    first = result["pair_candidates"][0]
    assert first["data_version_id"] == "medtrust_orthopedic_dataset_fracatlas_v6"
    assert (
        first["model_version_id"]
        == "medtrust_model_torchvision_mobilenet_v3_small_template"
    )
    assert first["hard_gate"]["status"] == "hold"
    assert first["workflow_role"] == "training_required"
    assert first["actions"]["can_execute"] is False

    digital_knee_localizer = next(
        pair
        for pair in result["pair_candidates"]
        if pair["data_version_id"] == "medtrust_orthopedic_dataset_digital_knee_v1"
        and pair["model_version_id"] == "medtrust_model_oai_knee_localizer_resnet18"
    )
    assert digital_knee_localizer["hard_gate"]["status"] == "fail"
    assert any(
        check["code"] in {"CONDITION_ANATOMY", "TASK_TARGET"}
        and check["result"] == "fail"
        for check in digital_knee_localizer["hard_gate"]["checks"]
    )


def test_curated_orthopedic_source_wins_an_exact_score_tie() -> None:
    curated = _fracture_xray_data(source="external_catalog")
    curated.update(
        {
            "product_id": "curated-data",
            "version_id": "curated-data-v1",
            "product_code": (
                "external:medtrust-orthopedic-curated-datasets-v1:fracatlas"
            ),
            "name": "Curated FracAtlas",
        }
    )
    generic = _fracture_xray_data(source="external_catalog")
    generic.update(
        {
            "product_id": "generic-data",
            "version_id": "generic-data-v1",
            "product_code": "external:bulk-public-catalog:fracatlas-copy",
            "name": "Generic FracAtlas copy",
        }
    )
    model = _fracture_xray_model(source="external_catalog")

    result = recommend_research_demand(
        "我想用成人多部位骨骼 X 光判断是否存在骨折，输出准确率和混淆矩阵。",
        data_products=[],
        model_products=[],
        pair_data_products=[generic, curated],
        pair_model_products=[model],
    )

    assert result["pair_candidates"][0]["data_name"] == "Curated FracAtlas"
    assert result["pair_candidates"][0]["data_product_code"].startswith(
        "external:medtrust-orthopedic-curated-"
    )


def test_risk_prediction_pair_explicitly_hard_fails_image_classifier() -> None:
    result = recommend_research_demand(
        "使用入院时可获得的成人骨折结构化电子病历，预测入院后30天内再入院风险，用于科研分析",
        data_products=[_fracture_data()],
        model_products=[_fracture_xray_model()],
    )

    assert result["data_recommendations"]
    assert result["model_recommendations"] == []
    assert result["normalized_intent"]["purpose_code"] == "research_analysis"
    assert result["can_apply_pair_selection"] is False
    assert result["pair_matching_status"] == "incompatible"
    pair = result["pair_candidates"][0]
    assert pair["hard_gate"]["status"] == "fail"
    assert pair["actions"]["can_select"] is False
    assert pair["actions"]["can_execute"] is False
    task_check = next(
        check for check in pair["hard_gate"]["checks"] if check["code"] == "TASK_TARGET"
    )
    assert task_check["result"] == "fail"
    assert "影像分类" in task_check["reason"]
    assert task_check["reason"] in pair["limitations"]


def test_platform_verified_relation_is_the_only_verified_pair_promotion() -> None:
    data = _fracture_xray_data()
    model = _fracture_xray_model()
    relation = {
        "id": "relation-1",
        "data_version_id": data["version_id"],
        "model_version_id": model["version_id"],
        "current_status": "verified",
        "strongest_evidence_level": "platform_verification",
        "public_visible": True,
    }

    result = recommend_research_demand(
        "我想使用腕部 X 线影像验证骨折图像分类模型，用于科研分析",
        data_products=[data],
        model_products=[model],
        pair_relations=[relation],
    )

    pair = result["pair_candidates"][0]
    assert pair["stage"] == "verified_pair"
    assert pair["evidence"] == {
        "relation_id": "relation-1",
        "status": "verified",
        "level": "platform_verification",
        "public_visible": True,
    }


def test_complete_request_exposes_ohdsi_style_research_definition_without_fake_codes() -> None:
    result = recommend_research_demand(
        "使用入院时可获得的成人骨折结构化电子病历构建模型，预测入院后30天内再入院风险，输出准确率和混淆矩阵，用于科研分析",
        data_products=[_fracture_data()],
        model_products=[_fracture_model()],
    )

    intent = result["normalized_intent"]
    assert result["assistant_version"] == "deterministic-rules-v2"
    assert intent["research_definition_status"] == "defined"
    assert intent["study_mode_code"] == "training"
    assert intent["care_setting_code"] == "inpatient"
    assert intent["data_modality_code"] == "structured_ehr"
    assert "疾病或研究对象：骨折" in intent["inclusion_criteria"]
    assert "准确率" in intent["evaluation_outputs"]
    assert "混淆矩阵" in intent["evaluation_outputs"]
    assert intent["exclusion_criteria"] == []
    assert intent["concept_mappings"]
    assert all(item["mapping_status"] == "not_mapped" for item in intent["concept_mappings"])
    assert all(item["coding_system"] is None for item in intent["concept_mappings"])
    assert all(item["code"] is None for item in intent["concept_mappings"])
    assert intent["study_definition"]["operation_mode"]["code"] == "training"
    assert intent["study_definition"]["terminology"]["condition"]["standard_code"] is None


def test_prediction_window_is_not_confused_with_historical_feature_window() -> None:
    result = recommend_research_demand(
        "使用成人骨折患者过去90天结构化病史，在入院时预测未来30天内再入院风险，用于科研分析",
        data_products=[_fracture_data()],
        model_products=[_fracture_model()],
    )

    assert result["normalized_intent"]["prediction_horizon"] == "30_days"
    assert result["normalized_intent"]["prediction_horizon_label"] == "30天"


def test_validation_phrase_with_words_between_verb_and_model_is_recognized() -> None:
    result = recommend_research_demand(
        "我想使用结直肠组织病理图像验证一个分类模型，输出准确率和混淆矩阵，用于科研分析",
        data_products=[_pathmnist_data()],
        model_products=[_pathmnist_model()],
    )

    assert result["normalized_intent"]["study_mode_code"] == "validation"
    assert result["normalized_intent"]["study_definition"]["operation_mode"]["code"] == "validation"


def test_pathmnist_only_catalog_reports_real_no_match_for_fracture_request() -> None:
    text = "使用入院时可获得的成人骨折电子病历，预测入院后30天内再入院风险，用于科研分析"
    result = recommend_research_demand(
        text,
        data_products=[_pathmnist_data()],
        model_products=[_pathmnist_model()],
    )

    assert result["status"] == "catalog_gap"
    assert result["data_recommendations"] == []
    assert result["model_recommendations"] == []
    assert {item["code"] for item in result["catalog_gaps"]} == {
        "NO_ELIGIBLE_DATA_PRODUCT",
        "NO_ELIGIBLE_MODEL_PRODUCT",
    }
    assert result["can_apply_catalog_selection"] is False
    assert result["method_suggestions"]
    assert all(item["registered"] is False for item in result["method_suggestions"])
    assert all(item["executable"] is False for item in result["method_suggestions"])


def test_colorectal_pathology_classification_recommends_real_catalog_pair() -> None:
    result = recommend_research_demand(
        "我想使用结直肠组织病理图像构建分类模型，用于科研分析",
        data_products=[_pathmnist_data()],
        model_products=[_pathmnist_model()],
    )

    assert result["status"] == "ready"
    assert result["normalized_intent"]["condition_code"] == "colorectal_pathology"
    assert result["normalized_intent"]["task_family"] == "image_classification"
    assert result["clarifications"] == []
    assert [item["version_id"] for item in result["data_recommendations"]] == [
        "data-pathmnist-v1"
    ]
    assert [item["version_id"] for item in result["model_recommendations"]] == [
        "model-pathmnist-v1"
    ]
    assert result["draft_patch"]["profile"]["project_type"] == "model_external_validation"
    assert result["draft_patch"]["profile"]["purpose_code"] == "model_validation"
    assert result["can_apply_catalog_selection"] is True


def test_model_outcome_must_match_the_requested_specific_outcome() -> None:
    mortality_model = _fracture_model("model-mortality-v1")
    mortality_model.update(
        {
            "name": "骨折患者院内死亡风险研究模型",
            "task_type": "binary_classification risk_prediction mortality",
            "output_schema": {"label": "in_hospital_mortality"},
        }
    )
    result = recommend_research_demand(
        "使用入院时可获得的成人骨折电子病历，预测入院后30天内再入院风险，用于科研分析",
        data_products=[_fracture_data()],
        model_products=[mortality_model],
    )

    assert result["status"] == "catalog_gap"
    assert result["data_recommendations"]
    assert result["model_recommendations"] == []
    assert "NO_ELIGIBLE_MODEL_PRODUCT" in {
        item["code"] for item in result["catalog_gaps"]
    }


def test_histopathology_is_not_mistaken_for_hospital_his_data() -> None:
    pathology_data = _fracture_data("data-histopathology-v1")
    pathology_data.update(
        {
            "name": "骨折组织 histopathology 图像集",
            "modality": "histopathology image",
            "scale": {"image_count": 1000},
        }
    )
    pathology_model = _fracture_model("model-histopathology-v1")
    pathology_model.update(
        {
            "name": "骨折组织 histopathology 再入院分类模型",
            "modality": "histopathology image",
        }
    )
    result = recommend_research_demand(
        "使用入院时可获得的成人骨折电子病历，预测入院后30天内再入院风险，用于科研分析",
        data_products=[pathology_data],
        model_products=[pathology_model],
    )

    assert result["status"] == "catalog_gap"
    assert result["data_recommendations"] == []
    assert result["model_recommendations"] == []


def test_personal_clinical_or_raw_export_request_is_blocked_without_echoing_input() -> None:
    requests = [
        "请根据我的骨折病历预测我会不会住院并给出治疗建议",
        "请导出骨折患者的原始病历明细供我下载建立模型",
    ]

    for text in requests:
        result = recommend_research_demand(
            text,
            data_products=[_fracture_data()],
            model_products=[_fracture_model()],
        )
        assert result["status"] == "blocked"
        assert result["data_recommendations"] == []
        assert result["model_recommendations"] == []
        assert result["draft_patch"] == {}
        assert result["method_suggestions"] == []
        assert text not in repr(result)


def test_nonclinical_research_wording_is_not_mistaken_for_clinical_use() -> None:
    allowed = recommend_research_demand(
        "使用入院时可获得的成人骨折电子病历，预测入院后30天再入院风险，用于临床研究但不用于临床诊断",
        data_products=[_fracture_data()],
        model_products=[_fracture_model()],
    )
    blocked = recommend_research_demand(
        "使用成人骨折电子病历建立模型，直接用于临床诊断和治疗决策",
        data_products=[_fracture_data()],
        model_products=[_fracture_model()],
    )

    assert allowed["status"] == "ready"
    assert blocked["status"] == "blocked"
    assert "CLINICAL_USE_NOT_SUPPORTED" in {
        item["code"] for item in blocked["blocking_reasons"]
    }


def test_response_keeps_prototype_and_governance_boundaries_explicit() -> None:
    result = recommend_research_demand(FRACTURE_REQUEST, [], [])
    boundary = result["boundary"]

    assert boundary == {
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


def test_application_route_contract_is_requester_only_and_read_only() -> None:
    source = (
        __import__("pathlib")
        .Path(__file__)
        .parents[1]
        .joinpath("app", "api", "routes", "applications.py")
        .read_text(encoding="utf-8")
    )
    route = source.split('@router.post("/application-assistant/recommend")', 1)[1]
    route = route.split("\n\n@router.", 1)[0]

    assert '_actor(session, identity, "data_requester")' in route
    assert "recommend_research_demand(" in route
    assert "_assistant_pair_catalog_payload(" in route
    for forbidden in (
        "create_application_draft(",
        "submit_application_for_review(",
        "run_compatibility_check(",
        "session.add(",
        "session.commit(",
    ):
        assert forbidden not in route


def test_route_is_registered_and_request_text_is_bounded_and_trimmed() -> None:
    matching_routes = [
        route
        for route in application_routes.router.routes
        if getattr(route, "path", None) == "/application-assistant/recommend"
    ]
    assert len(matching_routes) == 1
    assert matching_routes[0].methods == {"POST"}

    payload = application_routes.ResearchDemandRecommendationRequest(
        demand_text="  我想构建一个骨折患者住院风险预测模型  "
    )
    assert payload.demand_text == FRACTURE_REQUEST
    with pytest.raises(ValidationError):
        application_routes.ResearchDemandRecommendationRequest(demand_text="太短")
    with pytest.raises(ValidationError):
        application_routes.ResearchDemandRecommendationRequest(demand_text="研" * 2001)


def test_route_delegates_to_the_governed_options_projection(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    async def fake_actor(session, identity: str, expected: str):
        calls.append(("actor", (identity, expected)))
        return type("Context", (), {"space_id": "space-1"})(), object()

    async def fake_options(session, space_id):
        calls.append(("options", space_id))
        return {
            "data_products": [_fracture_data()],
            "model_products": [_fracture_model()],
            "sample": {"data_version_id": None, "model_version_id": None},
        }

    async def fake_pair_catalog(session, space_id):
        calls.append(("pair_catalog", space_id))
        return {
            "data_products": [],
            "model_products": [],
            "pair_relations": [],
        }

    monkeypatch.setattr(application_routes, "_actor", fake_actor)
    monkeypatch.setattr(application_routes, "_application_options_payload", fake_options)
    monkeypatch.setattr(
        application_routes, "_assistant_pair_catalog_payload", fake_pair_catalog
    )
    payload = application_routes.ResearchDemandRecommendationRequest(
        demand_text="使用入院时可获得的成人骨折结构化电子病历，预测入院后30天内再入院风险，用于科研分析"
    )

    result = asyncio.run(
        application_routes.recommend_application_demand(
            payload=payload,
            identity="data_requester",
            session=object(),
        )
    )

    assert calls == [
        ("actor", ("data_requester", "data_requester")),
        ("options", "space-1"),
        ("pair_catalog", "space-1"),
    ]
    assert result["status"] == "ready"
    assert result["data_recommendations"][0]["version_id"] == "data-fracture-v1"
    assert result["model_recommendations"][0]["version_id"] == "model-fracture-v1"


def test_external_pair_catalog_projection_is_compare_only() -> None:
    profile = {
        "catalog_stage": "catalog_only",
        "condition_codes": ["fracture"],
        "anatomical_sites": ["wrist"],
        "modalities": ["x_ray"],
        "supported_tasks": ["image_classification"],
        "application_eligible": False,
        "materialization_status": "not_materialized",
    }
    source = SimpleNamespace(source_code="orthopedic", display_name="官方目录")
    data_record = SimpleNamespace(
        id="external-data",
        external_id="fracatlas-v6",
        display_name_cn="FracAtlas 骨折影像",
        display_name_en=None,
        canonical_name="FracAtlas",
        official_source_name="Figshare",
        disease_areas=["fracture"],
        modalities=["x_ray"],
        task_types=["image_classification"],
        sample_count=4083,
        patient_count=None,
        file_count=None,
        approximate_size_bytes=322_740_000,
        quality_flags=[],
        license_name="CC BY 4.0",
        license_status="permissive",
        access_level="open_download",
        registration_required=False,
        dataset_version="v6",
    )
    data_version = SimpleNamespace(
        id="external-data-version",
        normalized_payload={"medtrust_profile": profile},
        record_digest="a" * 64,
        catalog_version="2026-08-29",
    )
    model_record = SimpleNamespace(
        id="external-model",
        external_model_id="mobilenet-v3-small",
        display_name_cn="MobileNetV3 算法模板",
        display_name_en=None,
        canonical_name="MobileNetV3-Small",
        upstream_provider="TorchVision",
        disease_areas=[],
        modalities=["x_ray"],
        task_types=["image_classification"],
        input_schema="single image",
        output_schema="class logits",
        license_name="BSD-3-Clause",
        license_status="permissive",
        access_status="public_available",
        weights_status="public_available",
        revision="v1",
        release_tag=None,
        execution_status="not_materialized",
        clinical_use_status="non_clinical",
    )
    model_version = SimpleNamespace(
        id="external-model-version",
        normalized_payload={
            "medtrust_profile": {
                **profile,
                "asset_kind": "algorithm_template",
                "target_task_weights": False,
                "operation_modes": ["training"],
            }
        },
        record_digest="b" * 64,
        catalog_version="2026-08-29",
    )

    class FakeRows:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class FakeSession:
        def __init__(self):
            self.execute_results = [
                FakeRows([(data_record, data_version, source)]),
                FakeRows([(model_record, model_version, source)]),
            ]

        async def execute(self, statement):
            return self.execute_results.pop(0)

        async def scalars(self, statement):
            return FakeRows([])

    payload = asyncio.run(
        application_routes._assistant_pair_catalog_payload(
            FakeSession(), "space-1"
        )
    )

    assert payload["data_products"][0]["candidate_source"] == "external_catalog"
    assert payload["data_products"][0]["application_eligible"] is False
    assert payload["model_products"][0]["profile"]["asset_kind"] == "algorithm_template"
    assert payload["model_products"][0]["materialization_status"] == "not_materialized"
    assert payload["pair_relations"] == []
