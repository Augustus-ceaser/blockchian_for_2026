from __future__ import annotations

import hashlib
import http.cookiejar
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

API_ROOT = "http://127.0.0.1:8000/api/v1"
ORIGIN = "http://127.0.0.1:5173"
WORKSPACE = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = Path(r"D:\MedTrustData\model-governance-evidence\phase5.12.3B1")


def candidate(
    record_id: str,
    name: str,
    *,
    source: str,
    paper: str,
    paper_decision: str = "official_paper_confirmed",
    model_card: str = "incomplete",
    license_decision: str,
    license_payload: dict,
    weights: str,
    weights_payload: dict,
    revision: str,
    revision_value: str,
    technical: str = "accepted",
    confirmed_fields: tuple[str, ...] = (),
    security: str = "review_required",
    resolved_flags: tuple[str, ...] = (),
    productization: str = "unreviewed",
) -> dict:
    return {
        "record_id": record_id,
        "name": name,
        "source": source,
        "paper": paper,
        "paper_decision": paper_decision,
        "model_card": model_card,
        "license_decision": license_decision,
        "license_payload": license_payload,
        "weights": weights,
        "weights_payload": weights_payload,
        "revision": revision,
        "revision_value": revision_value,
        "technical": technical,
        "confirmed_fields": list(confirmed_fields),
        "security": security,
        "resolved_flags": list(resolved_flags),
        "productization": productization,
    }


RESEARCH_ONLY = {
    "research_use": "true",
    "commercial_use": "false",
    "redistribution": "unknown",
    "derivatives": "unknown",
    "clinical_use": "false",
}

CANDIDATES = [
    candidate(
        "ac887c58-a077-4864-ab67-09e8293fce0c",
        "CONCH",
        source="https://github.com/mahmoodlab/CONCH",
        paper="https://www.nature.com/articles/s41591-024-02856-4",
        model_card="official_model_card_confirmed",
        license_decision="noncommercial",
        license_payload={
            **RESEARCH_ONLY,
            "license_name": "CC BY-NC-ND 4.0 with project terms",
            "redistribution": "false",
            "derivatives": "false",
        },
        weights="gated",
        weights_payload={
            "integrity_metadata_present": True,
            "files": [{
                "name": "pytorch_model.bin",
                "size_bytes": 802235437,
                "lfs_oid_present": True,
            }],
            "downloaded": False,
        },
        revision="model_revision_pinned",
        revision_value="f9ca9f877171a28ade80228fb195ac5d79003357",
        confirmed_fields=("revision", "weight_file_metadata"),
        security="cleared",
        resolved_flags=("dependency_unpinned", "weight_integrity_unknown"),
        productization="approved",
    ),
    candidate(
        "ab7e9193-c73e-4d40-9f26-5a7c124ec4ce",
        "UNI",
        source="https://github.com/mahmoodlab/UNI",
        paper="https://www.nature.com/articles/s41591-024-02857-3",
        model_card="official_model_card_confirmed",
        license_decision="noncommercial",
        license_payload={
            **RESEARCH_ONLY,
            "license_name": "CC BY-NC-ND 4.0 with project terms",
            "redistribution": "false",
            "derivatives": "false",
        },
        weights="gated",
        weights_payload={
            "integrity_metadata_present": True,
            "files": [{
                "name": "pytorch_model.bin",
                "size_bytes": 1213527781,
                "lfs_oid_present": True,
            }],
            "downloaded": False,
        },
        revision="model_revision_pinned",
        revision_value="b55a5ec6cade1a39edfe6534189a9b8ca7a022f0",
        confirmed_fields=("revision", "weight_file_metadata"),
        security="cleared",
        resolved_flags=("dependency_unpinned", "weight_integrity_unknown"),
        productization="approved",
    ),
    candidate(
        "ef77c69c-86dd-49f4-afb0-e9bdcd9a046a",
        "Prov-GigaPath",
        source="https://github.com/prov-gigapath/prov-gigapath",
        paper="https://www.nature.com/articles/s41586-024-07441-w",
        model_card="official_model_card_confirmed",
        license_decision="research_only",
        license_payload={
            **RESEARCH_ONLY,
            "license_name": "Apache-2.0 code; model terms limit use to research and reproducibility",
            "deployed_use": "false",
        },
        weights="gated",
        weights_payload={
            "integrity_metadata_present": True,
            "files": [
                {"name": "pytorch_model.bin", "size_bytes": 4540023137, "lfs_oid_present": True},
                {"name": "slide_encoder.pth", "size_bytes": 345406235, "lfs_oid_present": True},
            ],
            "downloaded": False,
        },
        revision="model_revision_pinned",
        revision_value="eba85dd46097c3eedfcc2a3a9a930baecb6bcc19",
        confirmed_fields=("revision", "weight_file_metadata"),
        security="cleared",
        resolved_flags=("dependency_unpinned", "weight_integrity_unknown"),
        productization="approved",
    ),
    candidate(
        "605421d1-1f5d-4601-83d8-db0afa3f9da1",
        "ST-Net",
        source="https://github.com/bryanhe/ST-Net",
        paper="https://www.nature.com/articles/s41551-020-0578-x",
        model_card="incomplete",
        license_decision="unverified",
        license_payload={"license_name": None, **RESEARCH_ONLY},
        weights="not_released",
        weights_payload={"integrity_metadata_present": False, "downloaded": False},
        revision="commit_pinned",
        revision_value="43022c1cb7de1540d5a74ea2338a12c82491c5ad",
        technical="incomplete",
    ),
    candidate(
        "77cdb8fe-9f06-4511-8f94-f4e87c16b80a",
        "DeepPT",
        source="https://github.com/PangeaResearch/enlight-deeppt-data",
        paper="https://pmc.ncbi.nlm.nih.gov/articles/PMC10543028/",
        model_card="missing",
        license_decision="unverified",
        license_payload={"license_name": None, **RESEARCH_ONLY},
        weights="not_released",
        weights_payload={"integrity_metadata_present": False, "downloaded": False},
        revision="commit_pinned",
        revision_value="d96708890937d595b513006fe1ff1e777e271be2",
        technical="incomplete",
    ),
    candidate(
        "da18ca31-6643-4c74-a024-8874b38f2c64",
        "CellViT",
        source="https://github.com/TIO-IKIM/CellViT",
        paper="https://doi.org/10.1016/j.media.2024.103143",
        model_card="incomplete",
        license_decision="custom_terms",
        license_payload={
            **RESEARCH_ONLY,
            "license_name": "Apache-2.0 with Commons Clause and component-specific terms",
        },
        weights="public_available",
        weights_payload={"integrity_metadata_present": False, "downloaded": False},
        revision="conflicting_versions",
        revision_value="05097e18e3d194a65121042f631b5753069f5ee3",
    ),
    candidate(
        "33bf0a43-b314-47fa-a16d-eff4e049552c",
        "HoVer-Net",
        source="https://github.com/vqdang/hover_net",
        paper="https://arxiv.org/abs/1812.06499",
        model_card="incomplete",
        license_decision="noncommercial",
        license_payload={
            **RESEARCH_ONLY,
            "license_name": "MIT code; PanNuke-derived weights CC BY-NC-SA 4.0",
        },
        weights="public_available",
        weights_payload={"integrity_metadata_present": False, "downloaded": False},
        revision="unpinned",
        revision_value="67e2ce5e3f1a64a2ece77ad1c24233653a9e0901",
    ),
    candidate(
        "03146d23-e03a-46b4-8a3e-948b3d56f132",
        "PraNet",
        source="https://github.com/DengPingFan/PraNet",
        paper="https://arxiv.org/abs/2006.11392",
        paper_decision="preprint_only",
        model_card="incomplete",
        license_decision="research_only",
        license_payload={
            **RESEARCH_ONLY,
            "license_name": "Research and education use; commercial permission required",
        },
        weights="public_available",
        weights_payload={"integrity_metadata_present": False, "downloaded": False},
        revision="unpinned",
        revision_value="95f12bd4e32ec954b5069654abae7de9473fd1f5",
    ),
]

DIMENSIONS = (
    "source",
    "paper",
    "repository",
    "model_card",
    "license",
    "weights",
    "revision",
    "technical_contract",
    "clinical_boundary",
    "security",
    "model_family",
    "productization",
)


def load_operator_password() -> str:
    path = WORKSPACE / "config" / "phase4-demo.env"
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("MEDTRUST_DEMO_OPERATOR_PASSWORD="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("Operator password is not configured.")


def request_json(opener, method: str, path: str, payload: dict | None = None, key: str | None = None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "Origin": ORIGIN}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Idempotency-Key"] = key
    request = Request(f"{API_ROOT}{path}", data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=30) as response:
            content = response.read()
            return None if not content else json.loads(content)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc


def review_payload(item: dict, dimension: str) -> dict:
    source = item["source"]
    if dimension == "source":
        decision = "official_source_confirmed"
        payload = {"official_source_name": item["name"], "official_source_url": source}
        reference = source
        note = "Official project source confirms model identity."
    elif dimension == "paper":
        decision = item["paper_decision"]
        payload = {"paper_url": item["paper"]}
        reference = item["paper"]
        note = "Official journal or preprint record confirms the cited research."
    elif dimension == "repository":
        decision = "official_repository_confirmed"
        payload = {"repository_url": source}
        reference = source
        note = "Repository ownership and current default branch were checked through the official host."
    elif dimension == "model_card":
        decision = item["model_card"]
        payload = {"model_card_url": source}
        reference = source
        note = "The official README or model card was reviewed for intended use and limitations."
    elif dimension == "license":
        decision = item["license_decision"]
        payload = item["license_payload"]
        reference = source
        note = "Code, model and weight terms were recorded separately where the official source distinguishes them."
    elif dimension == "weights":
        decision = item["weights"]
        payload = item["weights_payload"]
        reference = source
        note = "Only metadata was inspected; no model weight was downloaded."
    elif dimension == "revision":
        decision = item["revision"]
        payload = {"revision": item["revision_value"], "weights_downloaded": False}
        reference = source
        note = "The reviewed repository or model revision is recorded without materializing the model."
    elif dimension == "technical_contract":
        decision = item["technical"]
        payload = {
            "confirmed_fields": item["confirmed_fields"],
            "metadata_only": True,
            "platform_validation": "not_validated",
        }
        reference = source
        note = "This is a static metadata review, not runtime or compatibility validation."
    elif dimension == "clinical_boundary":
        decision = "research_only"
        payload = {"clinical_use": False, "medical_device": False}
        reference = source
        note = "No clinical, diagnostic, medical-device or production-use approval is inferred."
    elif dimension == "security":
        decision = item["security"]
        payload = {
            "resolved_flags": item["resolved_flags"],
            "review_scope": "static_metadata_only",
            "sandbox_validation": "not_performed",
            "external_code_executed": False,
        }
        reference = source
        note = "Static metadata review only; weights and external code were not loaded or executed."
    elif dimension == "model_family":
        decision = "none"
        payload = {"batch_duplicate_found": False}
        reference = source
        note = "No duplicate family relationship was established within the selected batch."
    else:
        decision = item["productization"]
        payload = {
            "metadata_only_draft": decision == "approved",
            "executable": False,
            "weights_downloaded": False,
            "commercial_or_clinical_approval": False,
        }
        reference = source
        note = (
            "Approved only for a metadata-only draft under recorded restrictions."
            if decision == "approved"
            else "Not approved for a draft because evidence or integrity checks remain incomplete."
        )
    return {
        "review_dimension": dimension,
        "decision": decision,
        "decision_payload": payload,
        "evidence_type": "official_page",
        "evidence_reference": reference,
        "evidence_note": note,
    }


def validate_batch() -> None:
    review_count = len(CANDIDATES) * len(DIMENSIONS)
    if review_count > 100:
        raise RuntimeError("Review batch exceeds the 100-review limit.")
    if len(CANDIDATES) != 8 or len({item["record_id"] for item in CANDIDATES}) != 8:
        raise RuntimeError("The primary batch must contain exactly eight unique models.")
    if not EVIDENCE_ROOT.is_dir():
        raise RuntimeError("The D-drive evidence root does not exist.")
    reports = list((EVIDENCE_ROOT / "manifests").glob("*-report.json"))
    request_count = sum(
        len(json.loads(path.read_text(encoding="utf-8"))["requests"])
        for path in reports
    )
    if request_count > 80:
        raise RuntimeError("Evidence request manifest exceeds the hard 80-request limit.")


def main() -> int:
    validate_batch()
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    request_json(
        opener,
        "POST",
        "/auth/login",
        {"username": "operator.demo", "password": load_operator_password()},
    )
    profile = request_json(opener, "GET", "/auth/me")
    if profile.get("role") != "space_operator":
        raise RuntimeError("Authenticated account is not the space operator.")

    submitted = 0
    for item in CANDIDATES:
        for dimension in DIMENSIONS:
            payload = review_payload(item, dimension)
            material = f"phase5.12.3B1:{item['record_id']}:{dimension}:v1"
            key = f"phase5123b1-{hashlib.sha256(material.encode()).hexdigest()[:32]}"
            request_json(
                opener,
                "POST",
                f"/external-model-catalog/models/{item['record_id']}/reviews",
                payload,
                key,
            )
            submitted += 1

    request_json(
        opener,
        "POST",
        "/external-model-catalog/governance/recalculate",
        {},
        "phase5123b1-final-profile-recalculation-v2",
    )
    summary = request_json(opener, "GET", "/external-model-catalog/governance/summary")
    print(json.dumps({
        "submitted_reviews": submitted,
        "candidate_records": len(CANDIDATES),
        "summary": summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
