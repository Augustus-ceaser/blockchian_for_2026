from types import SimpleNamespace

from app.modules.contracts.lifecycle import PERMANENT_DENIALS, converge_policy


def _decision(**evidence):
    return SimpleNamespace(evidence=evidence)


def test_strictest_policy_convergence_never_broadens_approved_limits() -> None:
    result = converge_policy(
        request={
            "execution": {
                "run_count": 5,
                "valid_days": 30,
                "internet_required": True,
                "requested_outputs": [
                    "aggregate_metrics",
                    "confusion_matrix",
                    "execution_summary",
                ],
            },
            "review_requirements": {
                "hospital_egress_review": False,
                "model_technical_confirmation": False,
            },
        },
        data_policy={
            "max_runs": 3,
            "valid_days": 20,
            "network_allowed": True,
            "allowed_outputs": [
                "aggregate_metrics",
                "confusion_matrix",
                "execution_summary",
            ],
            "prohibited_outputs": ["raw_images"],
        },
        model_policy={
            "max_runs": 2,
            "valid_days": 10,
            "network_allowed": False,
            "allowed_outputs": ["aggregate_metrics", "execution_summary"],
            "prohibited_outputs": ["model_weights"],
        },
        decisions=[
            _decision(
                max_runs=1,
                valid_days=7,
                allowed_outputs=["aggregate_metrics"],
                prohibited_outputs=["raw_features"],
                requires_egress_review=True,
                requires_technical_confirmation=True,
            )
        ],
    )
    final = result["final"]
    assert final["run_count"] == 1
    assert final["valid_days"] == 7
    assert final["allowed_outputs"] == ["aggregate_metrics"]
    assert final["network_allowed"] is False
    assert final["hospital_egress_review"] is True
    assert final["model_technical_confirmation"] is True
    assert PERMANENT_DENIALS <= set(final["forbidden_outputs"])
    assert result["blockers"] == []


def test_empty_output_intersection_is_a_blocker() -> None:
    result = converge_policy(
        request={
            "execution": {
                "run_count": 1,
                "valid_days": 30,
                "internet_required": False,
                "requested_outputs": ["aggregate_metrics"],
            },
            "review_requirements": {},
        },
        data_policy={
            "allowed_outputs": ["aggregate_metrics"],
            "prohibited_outputs": [],
        },
        model_policy={
            "allowed_outputs": ["execution_summary"],
            "prohibited_outputs": [],
        },
        decisions=[],
    )
    assert result["final"]["allowed_outputs"] == []
    assert {item["code"] for item in result["blockers"]} == {
        "empty_output_intersection"
    }
