from pathlib import Path

import pytest
import yaml

from app.tools.seed_public_alpha_demo import RESOURCE_ROOT, validate_safe_manifest


ROOT = Path(__file__).resolve().parents[2]


def test_account_bootstrap_has_no_phase4_or_manifest_dependency() -> None:
    tool = (ROOT / "backend/app/tools/bootstrap_public_alpha_accounts.py").read_text()
    service = (ROOT / "backend/app/modules/identity/public_alpha.py").read_text()
    script = (ROOT / "deploy/tencent-gz-public-alpha/create-admin.sh").read_text()
    combined = tool + service
    assert "ensure_phase4_demo_initial" not in combined
    assert "load_pathmnist_model_registry" not in combined
    assert "model_manifest" not in combined
    assert "bootstrap_public_alpha_accounts" in script
    assert "bootstrap_public_alpha)" not in script


def test_public_alpha_identity_subjects_are_accepted_by_local_auth() -> None:
    local_auth = (
        ROOT / "backend/app/modules/identity/local_auth.py"
    ).read_text(encoding="utf-8")
    for role in (
        "space_operator",
        "data_provider",
        "model_provider",
        "data_requester",
        "catalog_curator",
    ):
        assert f'"public-alpha:{role}": "{role}"' in local_auth
    assert "ROLE_BY_IDENTITY_SUBJECT.get(user.identity_subject)" in local_auth


def test_admin_password_is_read_from_tty_and_not_a_process_argument() -> None:
    script = (ROOT / "deploy/tencent-gz-public-alpha/create-admin.sh").read_text()
    assert "read -r -s" in script
    assert "--password" not in script
    assert "password=" not in script
    assert "admin-bootstrap.audit.log" in script


def test_safe_demo_manifest_is_versioned_metadata_only() -> None:
    digest = validate_safe_manifest()
    assert len(digest) == 64
    path = (
        RESOURCE_ROOT
        / "registered_assets/pathmnist_resnet18_v1/model_manifest.yaml"
    )
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["non_clinical"] is True
    assert document["synthetic_or_public"] is True
    assert document["contains_model_weights"] is False
    assert document["network_access"] is False


def test_missing_demo_manifest_has_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(
        RuntimeError, match="versioned demo model manifest is unavailable"
    ):
        validate_safe_manifest(tmp_path)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("contains_model_weights", True, "model weights"),
        ("asset_locator", "D:\\private\\model", "absolute local path"),
        ("patient_id", "example", "patient field"),
    ],
)
def test_unsafe_demo_manifest_is_rejected(
    tmp_path: Path, key: str, value: object, message: str
) -> None:
    source = (
        RESOURCE_ROOT
        / "registered_assets/pathmnist_resnet18_v1/model_manifest.yaml"
    )
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    document[key] = value
    target = (
        tmp_path
        / "registered_assets/pathmnist_resnet18_v1/model_manifest.yaml"
    )
    target.parent.mkdir(parents=True)
    target.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(RuntimeError, match=message):
        validate_safe_manifest(tmp_path)


def test_optional_demo_seed_is_separate_and_explicit() -> None:
    script = (
        ROOT / "deploy/tencent-gz-public-alpha/seed-public-alpha-demo.sh"
    ).read_text()
    assert "seed_public_alpha_demo" in script
    assert "[yes/NO]" in script
    assert "create-admin.sh" not in script
