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

# These are the B2 selections. The script is intentionally API-only: it does
# not open a database connection and does not download any upstream payload.
SELECTED_CANDIDATES = (
    ("377e1ee7-9647-437b-b1e1-79e770cb7234", "CPTAC-COAD"),
    ("5c1b2324-fc02-4cfd-9256-b1c1c1542b9a", "CAMELYON17"),
    ("51d66407-975c-4d8d-8761-ad2bfb0c4a40", "Hungarian-Colorectal-Screening"),
    ("d6f56362-0d6a-4d5e-a603-8ceb1598e705", "HyperKvasir"),
    ("0d34e89b-e4f5-4e90-8ed2-118e3e031934", "4D-Lung"),
)


def load_operator_password() -> str:
    path = WORKSPACE / "config" / "phase4-demo.env"
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("MEDTRUST_DEMO_OPERATOR_PASSWORD="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("Operator password is not configured.")


def request_json(
    opener,
    method: str,
    path: str,
    payload: dict | None = None,
    key: str | None = None,
) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "Origin": ORIGIN}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Idempotency-Key"] = key
    request = Request(f"{API_ROOT}{path}", data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read()
            return {} if not raw else json.loads(raw)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc


def main() -> int:
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

    drafts = []
    for record_id, expected_name in SELECTED_CANDIDATES:
        detail = request_json(opener, "GET", f"/external-catalog/datasets/{record_id}/governance")
        if detail["dataset"]["canonical_name"] != expected_name:
            raise RuntimeError(f"Record identity mismatch for {record_id}.")
        if not detail["governance"]["productization_eligible"]:
            raise RuntimeError(f"Selected record is no longer eligible: {expected_name}.")
        draft_status = request_json(
            opener,
            "GET",
            f"/external-catalog/datasets/{record_id}/data-product-draft",
        )
        draft = draft_status.get("draft")
        operation = "existing"
        if draft is None:
            key_material = f"phase5.11.3B2:{record_id}:metadata-draft:v1"
            key = f"phase5113b2-{hashlib.sha256(key_material.encode()).hexdigest()[:32]}"
            draft = request_json(
                opener,
                "POST",
                f"/external-catalog/datasets/{record_id}/data-product-draft",
                {"curator_note": "B2 first governed metadata-only product draft."},
                key,
            )
            operation = "created"
        source_link = draft["source_link"]
        if (
            draft["product"]["lifecycle_status"] != "draft"
            or draft["version"]["status"] != "draft"
            or draft["version"]["default_use_mode"] != "external_metadata_catalog"
            or source_link["materialization_status"] != "metadata_only"
            or source_link["data_holder_status"] != "external_upstream"
            or source_link["execution_readiness"] != "not_ready"
        ):
            raise RuntimeError(f"Draft safety invariants failed for {expected_name}.")
        drafts.append(
            {
                "name": expected_name,
                "record_id": record_id,
                "product_code": draft["product"]["product_code"],
                "version_id": draft["version"]["id"],
                "operation": operation,
            }
        )

    print(json.dumps({"created_or_replayed": len(drafts), "drafts": drafts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
