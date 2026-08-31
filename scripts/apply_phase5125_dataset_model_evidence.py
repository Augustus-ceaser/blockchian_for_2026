from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

API_ROOT = os.environ.get(
    "MEDTRUST_PHASE5125_API_ROOT", "http://127.0.0.1:8000/api/v1"
).rstrip("/")
ORIGIN = os.environ.get("MEDTRUST_PHASE5125_ORIGIN", "http://127.0.0.1:5173")
WORKSPACE = Path(__file__).resolve().parents[1]


def request_json(opener, method: str, path: str, payload=None, key: str | None = None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json", "Origin": ORIGIN}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Idempotency-Key"] = key
    request = Request(f"{API_ROOT}{path}", data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read()
            return response.status, {} if not raw else json.loads(raw)
    except HTTPError as exc:
        raw = exc.read()
        return exc.code, {} if not raw else json.loads(raw)


def password_candidates() -> list[str]:
    values: dict[str, str] = {}
    env_path = WORKSPACE / "config" / "phase4-demo.env"
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return list(dict.fromkeys([
        values["MEDTRUST_DEMO_OPERATOR_PASSWORD"],
        "operator.demo",
    ]))


def login(username: str, candidates: list[str]):
    for password in candidates:
        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status, _ = request_json(
            opener, "POST", "/auth/login", {"username": username, "password": password}
        )
        if status == 200:
            return opener
    raise RuntimeError(f"Local demo login failed for {username}.")


def stable_key(data_name: str, model_name: str) -> str:
    raw = f"phase5.12.5:static-review:{data_name}:{model_name}:v1"
    return f"phase5125-{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


def payload(data: dict, model: dict) -> dict:
    pathology = data["name"] in {"CAMELYON17", "CPTAC-COAD"}
    evidence_type = (
        "static_schema_compatible_with_transformation"
        if pathology
        else "static_schema_incompatible"
    )
    note = (
        f"{data['name']} is published as histopathology whole-slide metadata and "
        f"{model['name']} expects histopathology image tiles. Static compatibility "
        "requires tissue masking, patch extraction, and the official model transform; "
        "the implementation and parameters have not been verified."
        if pathology
        else
        f"{data['name']} is an endoscopy dataset while {model['name']} expects "
        "histopathology image tiles. The modality and source-object contracts conflict, "
        "so this metadata-only static review records incompatibility."
    )
    return {
        "data_product_version_id": data["version_id"],
        "model_product_version_id": model["version_id"],
        "evidence_type": evidence_type,
        "outcome": "supports",
        "evidence_scope": "input_schema",
        "evidence_note": note,
        "structured_assessment": {
            "dataset_modality": "histopathology_wsi" if pathology else "endoscopy",
            "model_input": "histopathology_image_tiles",
            "metadata_only": True,
            "runtime_execution_performed": False,
            "clinical_validity_assessed": False,
        },
        "transformation_requirements": [] if not pathology else [
            {
                "name": "tissue_masking",
                "implementation_available": False,
                "implementation_verified": False,
            },
            {
                "name": "patch_extraction",
                "parameters_known": False,
                "implementation_available": False,
                "implementation_verified": False,
            },
            {
                "name": "official_model_transform",
                "implementation_available": False,
                "implementation_verified": False,
            },
        ],
        "blocking_reasons": [] if pathology else [
            "Dataset modality is endoscopy rather than histopathology.",
            "Dataset source objects are not pathology whole slides or pathology tiles.",
        ],
        "warning_reasons": [
            "Dataset file format, resolution, and patch parameters are incomplete.",
            "No model weights were downloaded and no execution was performed.",
        ],
    }


def main() -> int:
    operator = login("operator.demo", password_candidates())
    status, matrix = request_json(operator, "GET", "/dataset-model-relations?matrix=true")
    if status != 200:
        raise RuntimeError(f"Matrix request failed: HTTP {status}: {matrix}")
    data_rows = {item["name"]: item for item in matrix["matrix"]["data_versions"]}
    model_rows = {item["name"]: item for item in matrix["matrix"]["model_versions"]}
    if set(data_rows) != {"CAMELYON17", "CPTAC-COAD", "HyperKvasir"}:
        raise RuntimeError(f"Unexpected published external datasets: {sorted(data_rows)}")
    if set(model_rows) != {"CONCH", "UNI"}:
        raise RuntimeError(f"Unexpected published external models: {sorted(model_rows)}")
    results = []
    for data_name in sorted(data_rows):
        for model_name in sorted(model_rows):
            status, response = request_json(
                operator,
                "POST",
                "/dataset-model-relations/static-review",
                payload(data_rows[data_name], model_rows[model_name]),
                stable_key(data_name, model_name),
            )
            if status != 200:
                raise RuntimeError(
                    f"Review failed for {data_name}/{model_name}: HTTP {status}: {response}"
                )
            results.append({
                "data": data_name,
                "model": model_name,
                "status": response["current_status"],
                "created": response["created"],
            })
    print(json.dumps({"reviews": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
