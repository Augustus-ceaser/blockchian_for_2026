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
SELECTED_NAMES = ("CONCH", "UNI", "Prov-GigaPath")


def load_operator_password() -> str:
    path = WORKSPACE / "config" / "phase4-demo.env"
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("MEDTRUST_DEMO_OPERATOR_PASSWORD="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("Operator password is not configured.")


def request_json(opener, method: str, path: str, payload=None, key: str | None = None):
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
    models = request_json(opener, "GET", "/external-model-catalog/models?limit=100")["items"]
    by_name = {item["canonical_name"]: item for item in models}
    drafts = []
    for name in SELECTED_NAMES:
        record = by_name.get(name)
        if record is None:
            raise RuntimeError(f"Selected external model is missing: {name}.")
        record_id = record["id"]
        detail = request_json(
            opener, "GET", f"/external-model-catalog/models/{record_id}/governance"
        )
        if not detail["profile"]["productization_eligible"]:
            raise RuntimeError(f"Selected model is no longer eligible: {name}.")
        status = request_json(
            opener, "GET", f"/external-model-catalog/models/{record_id}/model-product-draft"
        )
        draft = status.get("draft")
        operation = "existing"
        if draft is None:
            material = f"phase5.12.3B2:{record_id}:metadata-draft:v1"
            key = f"phase5123b2-{hashlib.sha256(material.encode()).hexdigest()[:32]}"
            draft = request_json(
                opener,
                "POST",
                f"/external-model-catalog/models/{record_id}/model-product-draft",
                {"curator_note": "First governed metadata-only external model draft."},
                key,
            )
            operation = "created"
        version = draft["version"]
        link = draft["source_link"]
        if (
            draft["product"]["lifecycle_status"] != "draft"
            or version["status"] != "draft"
            or version["entrypoint_id"] != "external-metadata-only"
            or version["runtime"] != "external_metadata_only"
            or link["materialization_status"] != "metadata_only"
            or link["weight_holder_status"] != "external_upstream"
            or link["execution_readiness"] != "not_ready"
            or link["platform_validation"] != "not_validated"
            or version["compatibility_metadata"]["execution_ready"] is not False
        ):
            raise RuntimeError(f"Draft safety invariants failed for {name}.")
        drafts.append(
            {
                "name": name,
                "record_id": record_id,
                "product_code": draft["product"]["product_code"],
                "version_id": version["id"],
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
