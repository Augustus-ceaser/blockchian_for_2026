from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener


API_ROOT = os.environ.get(
    "MEDTRUST_PHASE5124_API_ROOT", "http://127.0.0.1:8000/api/v1"
).rstrip("/")
ORIGIN = os.environ.get(
    "MEDTRUST_PHASE5124_ORIGIN", "http://127.0.0.1:5173"
)
WORKSPACE = Path(__file__).resolve().parents[1]
SELECTED_NAMES = ("CONCH", "UNI")
KEEP_DRAFT_NAMES = ("Prov-GigaPath",)


def load_passwords() -> dict[str, list[str]]:
    values: dict[str, str] = {}
    path = WORKSPACE / "config" / "phase4-demo.env"
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return {
        "catalog.curator.demo": list(dict.fromkeys([
            values.get(
                "MEDTRUST_DEMO_CATALOG_CURATOR_PASSWORD",
                values["MEDTRUST_LOCAL_DEMO_PASSWORD"],
            ),
            "catalog.curator.demo",
        ])),
        "operator.demo": list(dict.fromkeys([
            values["MEDTRUST_DEMO_OPERATOR_PASSWORD"],
            "operator.demo",
        ])),
    }


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
        raise RuntimeError(
            f"{method} {path} failed with HTTP {exc.code}: {detail}"
        ) from exc


def login(username: str, password: str):
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    request_json(
        opener,
        "POST",
        "/auth/login",
        {"username": username, "password": password},
    )
    return opener


def login_first(username: str, candidates: list[str]):
    for password in candidates:
        try:
            return login(username, password)
        except RuntimeError:
            continue
    raise RuntimeError(f"Local demo login failed for {username}.")


def stable_key(action: str, record_id: str) -> str:
    material = f"phase5.12.4:{action}:{record_id}:v1"
    return f"phase5124-{hashlib.sha256(material.encode()).hexdigest()[:32]}"


def main() -> int:
    passwords = load_passwords()
    curator = login_first(
        "catalog.curator.demo", passwords["catalog.curator.demo"]
    )
    operator = login_first("operator.demo", passwords["operator.demo"])
    models = request_json(
        curator, "GET", "/external-model-catalog/models?limit=100"
    )["items"]
    by_name = {item["canonical_name"]: item for item in models}
    results = []
    for name in SELECTED_NAMES:
        record = by_name.get(name)
        if record is None:
            raise RuntimeError(f"Selected external model is missing: {name}.")
        record_id = record["id"]
        state = request_json(
            curator,
            "GET",
            f"/external-model-catalog/models/{record_id}/model-product-publication",
        )
        if state["publication"] is None and state["version"]["status"] == "draft":
            request_json(
                curator,
                "POST",
                f"/external-model-catalog/models/{record_id}/model-product-publication/submit",
                key=stable_key("submit", record_id),
            )
            state = request_json(
                curator,
                "GET",
                f"/external-model-catalog/models/{record_id}/model-product-publication",
            )
        if (
            state["publication"] is None
            and state["version"]["status"] == "under_review"
        ):
            request_json(
                operator,
                "POST",
                f"/external-model-catalog/models/{record_id}/model-product-publication/approve",
                {
                    "allow_catalog": True,
                    "review_opinion": (
                        "Source, governance snapshot and metadata-only boundary "
                        "reviewed and accepted."
                    ),
                    "risk_level": "medium",
                    "additional_conditions": (
                        "Catalog discovery only; no download, application, "
                        "readiness or execution."
                    ),
                },
                stable_key("approve", record_id),
            )
            state = request_json(
                operator,
                "GET",
                f"/external-model-catalog/models/{record_id}/model-product-publication",
            )
        if (
            state["publication"] is None
            or state["version"]["status"] != "approved"
            or state["product"]["lifecycle_status"] != "active"
            or state["source_link"]["materialization_status"] != "metadata_only"
            or state["source_link"]["execution_readiness"] != "not_ready"
            or state["source_link"]["platform_validation"] != "not_validated"
        ):
            raise RuntimeError(f"Published boundary failed for {name}.")
        results.append(
            {
                "name": name,
                "record_id": record_id,
                "product_code": state["product"]["product_code"],
                "publication_id": state["publication"]["id"],
                "published_at": state["publication"]["published_at"],
            }
        )

    for name in KEEP_DRAFT_NAMES:
        record = by_name.get(name)
        if record is None:
            raise RuntimeError(f"Draft candidate is missing: {name}.")
        state = request_json(
            curator,
            "GET",
            f"/external-model-catalog/models/{record['id']}/model-product-publication",
        )
        if state["publication"] is not None or state["version"]["status"] != "draft":
            raise RuntimeError(f"Keep-draft boundary failed for {name}.")

    print(
        json.dumps(
            {
                "published": results,
                "kept_draft": list(KEEP_DRAFT_NAMES),
                "weights_downloaded": 0,
                "executor_registered": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
