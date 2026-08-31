from types import SimpleNamespace

from app.api.routes.applications import (
    _is_pathmnist_data_sample,
    _is_pathmnist_resnet_sample,
)
from app.modules.applications.lifecycle import canonical_modality, registered_data_modality


def test_pathmnist_sample_detection_uses_registered_metadata_not_display_name() -> None:
    data_product = SimpleNamespace(
        product_code="DP-DEMO",
        name="Browser acceptance data product A",
        is_demo=True,
    )
    data_version = SimpleNamespace(
        linkage_metadata={
            "short_name": "PathMNIST validation set",
            "resource_identifier": "PATHMNIST-DEMO-20",
        }
    )
    model_product = SimpleNamespace(
        product_code="MP-DEMO",
        name="Browser acceptance model A",
        is_demo=True,
    )
    model_version = SimpleNamespace(entrypoint_id="pathmnist_resnet18_v1")

    assert _is_pathmnist_data_sample(data_product, data_version)
    assert _is_pathmnist_resnet_sample(model_product, model_version)
    data_version.scope_metadata = {"image_specification": "28 x 28 RGB"}
    assert (
        registered_data_modality(data_product, data_version, None)
        == "digital_pathology"
    )
    assert canonical_modality("数字病理图像") == canonical_modality(
        "digital_pathology"
    )


def test_non_demo_or_unrelated_products_are_not_selected_as_samples() -> None:
    data_product = SimpleNamespace(
        product_code="DP-OTHER",
        name="Other data",
        is_demo=False,
    )
    data_version = SimpleNamespace(
        linkage_metadata={"resource_identifier": "PATHMNIST-DEMO-20"},
        scope_metadata={"image_specification": "28 x 28 RGB"},
    )
    model_product = SimpleNamespace(
        product_code="MP-OTHER",
        name="Other model",
        is_demo=True,
    )
    model_version = SimpleNamespace(entrypoint_id="unrelated_classifier_v1")

    assert not _is_pathmnist_data_sample(data_product, data_version)
    assert not _is_pathmnist_resnet_sample(model_product, model_version)
    assert registered_data_modality(data_product, data_version, None) is None
