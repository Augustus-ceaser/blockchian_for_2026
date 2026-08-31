import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "capture_catalog_governance_evidence.py"
SPEC = importlib.util.spec_from_file_location("catalog_evidence_tool", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/page",
        "https://example.test/archive.zip",
        "https://example.test/images/case.nii.gz",
        "https://user:secret@example.test/page",
    ],
)
def test_rejects_non_https_download_and_credential_urls(url):
    with pytest.raises(MODULE.EvidenceCaptureError):
        MODULE._validate_url(url, resolve_host=False)


def test_accepts_normal_https_page_url():
    MODULE._validate_url(
        "https://example.test/datasets/project?view=metadata", resolve_host=False
    )


def test_counter_rejects_malformed_state(tmp_path):
    (tmp_path / "request-counter.json").write_text("[]", encoding="utf-8")

    with pytest.raises(MODULE.EvidenceCaptureError):
        MODULE._read_counter(tmp_path)
