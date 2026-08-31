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


def candidate(
    record_id: str,
    name: str,
    source_url: str,
    license_decision: str,
    license_name: str | None,
    access_decision: str,
    access_note: str,
    *,
    approved: bool = False,
    license_url: str | None = None,
    permissions: tuple[str, str, str, str, str] = (
        "unknown", "unknown", "unknown", "unknown", "unknown"
    ),
) -> dict:
    return {
        "record_id": record_id,
        "name": name,
        "source_url": source_url,
        "license_decision": license_decision,
        "license_name": license_name,
        "license_url": license_url or source_url,
        "access_decision": access_decision,
        "access_note": access_note,
        "permissions": permissions,
        "approved": approved,
    }


YES = ("true", "true", "true", "true", "true")
CC_BY = ("true", "true", "true", "true", "true")
NONCOMMERCIAL = ("true", "false", "true", "true", "true")
NO_DERIVATIVES = ("true", "false", "true", "false", "true")

CANDIDATES = [
    candidate("377e1ee7-9647-437b-b1e1-79e770cb7234", "CPTAC-COAD", "https://www.cancerimagingarchive.net/collection/cptac-coad/", "permissive", "CC BY 4.0", "open_download", "TCIA version 1 provides public pathology retrieval tooling; citation is required.", approved=True, permissions=CC_BY),
    candidate("2a38c775-bb89-4309-87e1-642faa01ce1d", "CRC_FFPE-CODEX_CellNeighs", "https://www.cancerimagingarchive.net/collection/crc_ffpe-codex_cellneighs/", "permissive", "CC BY 4.0", "open_download", "TCIA lists public access; the approximately 2 TB package requires TCIA large-file tooling.", approved=True, permissions=CC_BY),
    candidate("ffda3efd-6ff8-4dcb-9b1e-c8028e56053b", "CoNIC2022", "https://conic-challenge.grand-challenge.org/Rules/", "unverified", None, "registration_required", "Official challenge participation requires an account; no explicit dataset license was found.", permissions=("unknown", "unknown", "false", "unknown", "false")),
    candidate("0ba0ad85-e873-448f-be2a-1dc226ccf28c", "CoNSeP", "https://warwick.ac.uk/fac/sci/dcs/research/tia/data/hovernet/", "unverified", None, "unknown", "Warwick confirms the dataset identity, but current access conditions were not explicit.", permissions=("unknown", "unknown", "unknown", "unknown", "unknown")),
    candidate("f8310c03-6d64-44a1-a0d4-cb24564bcd0c", "Colorectal Histology MNIST", "https://zenodo.org/records/53169", "unverified", None, "open_download", "Zenodo exposes the record and files publicly; the reviewed page did not expose an explicit license.", permissions=("unknown", "unknown", "unknown", "unknown", "unknown")),
    candidate("1719e15d-b714-4533-89d0-b29f46c08547", "DigestPath19", "https://digestpath2019.grand-challenge.org/", "unverified", None, "unknown", "The official challenge confirms the dataset identity; explicit continuing access and license terms were not found.", permissions=("unknown", "unknown", "unknown", "unknown", "unknown")),
    candidate("ff8764d5-c73f-43c9-b9e0-8de303a68435", "GlaS", "https://warwick.ac.uk/fac/cross_fac/tia/data/glascontest-backup/", "unverified", None, "unavailable", "The official archived challenge states that registration is closed; no explicit dataset license was found.", permissions=("unknown", "unknown", "false", "unknown", "false")),
    candidate("51d66407-975c-4d8d-8761-ad2bfb0c4a40", "Hungarian-Colorectal-Screening", "https://www.cancerimagingarchive.net/collection/hungarian-colorectal-screening/", "permissive", "CC BY 4.0", "open_download", "TCIA version 3 is public; large-file retrieval uses TCIA tooling and citation is required.", approved=True, permissions=CC_BY),
    candidate("112612b1-94d8-4c4b-9308-5b5968c85e7f", "OCELOT2023", "https://ocelot2023.grand-challenge.org/datasets/", "unverified", None, "registration_required", "The official page sends users to Zenodo after collection of basic identity and purpose information.", permissions=("unknown", "unknown", "unknown", "unknown", "unknown")),
    candidate("b9301532-789e-4803-89e1-19318b1bd394", "PAIP2021", "https://paip2021.grand-challenge.org/Rules/", "noncommercial", "CC BY-NC 4.0 with challenge DUA", "registration_required", "Access requires a Grand Challenge account and acceptance of the data-use agreement.", permissions=NONCOMMERCIAL),
    candidate("ef30ab88-3b4b-4657-b2d2-e3b5c8122ff6", "CPTAC-BRCA", "https://www.cancerimagingarchive.net/collection/cptac-brca/", "permissive", "CC BY 4.0", "open_download", "TCIA version 1 pathology slides are publicly retrievable with attribution.", approved=True, permissions=CC_BY),
    candidate("88c69508-f7d6-4ade-abc1-ae56a07c41ca", "CPTAC-HNSCC", "https://www.cancerimagingarchive.net/collection/cptac-hnscc/", "controlled", "Component-specific: CC BY 4.0 pathology; NIH controlled radiology", "controlled_access", "Pathology is public, while face-reconstructable radiology requires NIH controlled access.", permissions=("true", "unknown", "unknown", "unknown", "unknown")),
    candidate("d9741bfd-2f0f-42be-bd92-f93baa86d397", "CPTAC-OV", "https://www.cancerimagingarchive.net/collection/cptac-ov/", "permissive", "CC BY 4.0", "open_download", "TCIA version 1 provides public collection retrieval tooling and requires citation.", approved=True, permissions=CC_BY),
    candidate("c2b0de98-aa8f-4800-ab2d-2cff4a616025", "TIL-WSI-TCGA", "https://www.cancerimagingarchive.net/analysis-result/til-wsi-tcga/", "permissive", "CC BY 3.0 for derived TIL maps", "open_download", "TCIA exposes the derived analysis result publicly; this conclusion does not extend to every upstream TCGA asset.", approved=True, permissions=CC_BY),
    candidate("5c1b2324-fc02-4cfd-9256-b1c1c1542b9a", "CAMELYON17", "https://camelyon17.grand-challenge.org/Data/", "permissive", "CC0 on current Data page", "open_download", "The current official Data page states that the complete dataset is open access.", approved=True, permissions=YES),
    candidate("88464daf-47b6-4d86-8f0a-b2af1c3bfb85", "MedMNIST", "https://github.com/MedMNIST/MedMNIST", "custom_terms", "CC BY 4.0 except DermaMNIST CC BY-NC 4.0", "open_download", "The official project identifies Zenodo as the distribution source and documents a component-specific license exception.", permissions=("true", "unknown", "true", "true", "true")),
    candidate("9bc78474-b4b2-4a53-8b59-642001b10ff1", "3D-IRCADb", "https://www.ircad.fr/research-and-development/data-sets/liver-segmentation-3d-ircadb-01/", "noncommercial", "CC BY-NC-ND 4.0", "open_download", "IRCAD lists 20 cases and direct public access; no files were requested in this review.", permissions=NO_DERIVATIVES),
    candidate("0d34e89b-e4f5-4e90-8ed2-118e3e031934", "4D-Lung", "https://www.cancerimagingarchive.net/collection/4d-lung/", "permissive", "CC BY 3.0", "open_download", "TCIA version 2 is publicly retrievable using TCIA tooling and requires attribution.", approved=True, permissions=CC_BY),
    candidate("a3e947a6-c4e4-4194-a8a9-e8abeb47e5fc", "AIDA-E_3", "https://aidasub-chromogastro.grand-challenge.org/", "unverified", None, "unknown", "The official challenge confirms identity, but explicit current access and license terms were not found.", permissions=("unknown", "unknown", "unknown", "unknown", "unknown")),
    candidate("d6f56362-0d6a-4d5e-a603-8ceb1598e705", "HyperKvasir", "https://www.nature.com/articles/s41597-020-00622-y", "permissive", "CC BY 4.0", "open_download", "The official data descriptor states that HyperKvasir is open access under CC BY 4.0.", approved=True, permissions=CC_BY),
]


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
    if dimension == "source":
        decision = "official_source_confirmed"
        payload = {
            "official_source_name": item["name"],
            "official_source_url": item["source_url"],
        }
        note = f"Official institutional or project page confirms the identity of {item['name']}."
    elif dimension == "license":
        decision = item["license_decision"]
        permission_keys = (
            "research_use", "commercial_use", "redistribution", "derivatives", "rehosting"
        )
        payload = {
            "license_name": item["license_name"],
            "license_url": item["license_url"],
            **dict(zip(permission_keys, item["permissions"], strict=True)),
        }
        note = (
            f"License conclusion: {item['license_name']}."
            if item["license_name"]
            else "No explicit dataset license was found on the reviewed official pages; status remains unverified."
        )
    elif dimension == "access":
        decision = item["access_decision"]
        payload = {"access_url": item["source_url"], "access_note": item["access_note"]}
        note = item["access_note"]
    else:
        decision = "approved" if item["approved"] else "unreviewed"
        payload = {
            "reason": (
                "Source, license and access evidence support metadata-only draft eligibility."
                if item["approved"]
                else "Insufficient or restrictive evidence; no product draft is approved."
            )
        }
        note = payload["reason"]
    return {
        "dimension": dimension,
        "decision": decision,
        "decision_payload": payload,
        "evidence_type": "official_page",
        "evidence_reference": item["source_url"],
        "evidence_note": note,
    }


def main() -> int:
    if len(CANDIDATES) > 25 or len({item["record_id"] for item in CANDIDATES}) != len(CANDIDATES):
        raise RuntimeError("Candidate batch must contain 25 or fewer unique records.")
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

    created = 0
    for item in CANDIDATES:
        for dimension in ("source", "license", "access", "productization"):
            payload = review_payload(item, dimension)
            key_material = f"phase5.11.3B1:{item['record_id']}:{dimension}:v1"
            key = f"phase5113b1-{hashlib.sha256(key_material.encode()).hexdigest()[:32]}"
            request_json(
                opener,
                "POST",
                f"/external-catalog/datasets/{item['record_id']}/reviews",
                payload,
                key,
            )
            created += 1
    request_json(
        opener,
        "POST",
        "/external-catalog/governance/recalculate",
        {},
        "phase5113b1-final-profile-recalculation-v1",
    )
    summary = request_json(opener, "GET", "/external-catalog/governance/summary")
    print(json.dumps({"submitted_reviews": created, "candidate_records": len(CANDIDATES), "summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
