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
SELECTED = ("CPTAC-COAD", "CAMELYON17", "HyperKvasir")


def load_password(name: str) -> str:
    values: dict[str, str] = {}
    path = WORKSPACE / "config" / "phase4-demo.env"
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"')
    key = f"MEDTRUST_DEMO_{name.upper()}_PASSWORD"
    password = values.get(key) or values.get("MEDTRUST_LOCAL_DEMO_PASSWORD")
    if not password:
        raise RuntimeError(f"{name} password is not configured")
    return password


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


def authenticated(username: str, password_name: str, role: str):
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    request_json(
        opener,
        "POST",
        "/auth/login",
        {"username": username, "password": load_password(password_name)},
    )
    profile = request_json(opener, "GET", "/auth/me")
    if profile.get("role") != role:
        raise RuntimeError(f"{username} authenticated with unexpected role")
    return opener


def command_key(action: str, version_id: str) -> str:
    digest = hashlib.sha256(
        f"phase5.11.4:{action}:{version_id}:v1".encode()
    ).hexdigest()[:32]
    return f"phase5114-{digest}"


def main() -> int:
    curator = authenticated(
        "catalog.curator.demo", "catalog_curator", "catalog_curator"
    )
    operator = authenticated("operator.demo", "operator", "space_operator")
    management = request_json(curator, "GET", "/data-product-management")
    by_name = {item["name"]: item for item in management["items"]}
    if not set(SELECTED) <= by_name.keys():
        raise RuntimeError("one or more selected active drafts are missing")

    results = []
    review = {
        "review_opinion": "Metadata provenance, license, access and non-computable policy verified.",
        "additional_conditions": "Revalidate upstream terms before any future materialization.",
        "requested_materials": "",
        "risk_level": "low",
        "allow_catalog": True,
    }
    for name in SELECTED:
        item = by_name[name]
        version_id = item["version_id"]
        lifecycle_status = item["status"]
        version_status = item["version_status"]
        if version_status == "draft":
            submitted = request_json(
                curator,
                "POST",
                f"/data-product-versions/{version_id}/submit",
                key=command_key("submit", version_id),
            )
            version_status = submitted["status"]
        if version_status == "under_review":
            approved = request_json(
                operator,
                "POST",
                f"/data-product-versions/{version_id}/approve",
                review,
                command_key("approve", version_id),
            )
            lifecycle_status = approved["status"]
        if lifecycle_status != "published":
            raise RuntimeError(
                f"{name} did not reach published state: "
                f"{lifecycle_status}/{version_status}"
            )
        detail = request_json(curator, "GET", f"/data-product-versions/{version_id}")
        external = detail.get("external_metadata") or {}
        if (
            external.get("materialization_status") != "metadata_only"
            or external.get("execution_readiness") != "not_ready"
            or external.get("application_eligibility") is not False
        ):
            raise RuntimeError(f"{name} publication safety invariants failed")
        results.append(
            {"name": name, "version_id": version_id, "status": lifecycle_status}
        )

    print(json.dumps({"published_or_replayed": len(results), "items": results}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
