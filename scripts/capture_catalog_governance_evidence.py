from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import socket
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_TOTAL_REQUESTS = 75
MAX_RECORD_REQUESTS = 3
ALLOWED_CONTENT_TYPES = {
    "application/json",
    "application/ld+json",
    "text/html",
    "text/plain",
}
BLOCKED_SUFFIXES = {
    ".7z",
    ".bz2",
    ".csv",
    ".dcm",
    ".dicom",
    ".fastq",
    ".fastq.gz",
    ".gz",
    ".h5",
    ".hdf5",
    ".mha",
    ".mhd",
    ".nii",
    ".nii.gz",
    ".npz",
    ".rar",
    ".svs",
    ".tar",
    ".tar.gz",
    ".tif",
    ".tiff",
    ".tsv",
    ".whl",
    ".xlsx",
    ".zip",
}
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class EvidenceCaptureError(RuntimeError):
    pass


def _validate_url(url: str, *, resolve_host: bool = True) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise EvidenceCaptureError("Only credential-free HTTPS URLs are allowed.")
    path = parsed.path.lower()
    if any(path.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        raise EvidenceCaptureError("Dataset and archive file URLs are blocked.")
    if resolve_host:
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(
                    parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
                )
            }
        except socket.gaierror as exc:
            raise EvidenceCaptureError("The evidence host cannot be resolved.") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise EvidenceCaptureError("Private and non-global hosts are blocked.")


class BoundedRedirectHandler(HTTPRedirectHandler):
    def __init__(self) -> None:
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirect_count += 1
        if self.redirect_count > MAX_REDIRECTS:
            raise EvidenceCaptureError("Redirect limit exceeded.")
        resolved = urljoin(req.full_url, newurl)
        _validate_url(resolved)
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def _read_counter(root: Path) -> dict:
    path = root / "request-counter.json"
    if not path.exists():
        return {"total": 0, "records": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("records"), dict):
        raise EvidenceCaptureError("Evidence request counter is malformed.")
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def capture(url: str, record_id: str, evidence_root: Path, note: str) -> dict:
    root = evidence_root.resolve()
    if root.drive.upper() != "D:":
        raise EvidenceCaptureError("Evidence must be written to the D drive.")
    _validate_url(url)
    counter = _read_counter(root)
    record_count = int(counter["records"].get(record_id, 0))
    if int(counter.get("total", 0)) >= MAX_TOTAL_REQUESTS:
        raise EvidenceCaptureError("Total official-page request limit reached.")
    if record_count >= MAX_RECORD_REQUESTS:
        raise EvidenceCaptureError("Per-record official-page request limit reached.")

    redirect_handler = BoundedRedirectHandler()
    opener = build_opener(redirect_handler)
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/json,text/plain;q=0.8",
            "User-Agent": "MedTrust-Catalog-Governance/1.0 metadata-only",
        },
        method="GET",
    )
    fetched_at = datetime.now(timezone.utc)
    try:
        with opener.open(request, timeout=20) as response:
            final_url = response.geturl()
            _validate_url(final_url)
            disposition = response.headers.get("Content-Disposition", "").lower()
            if "attachment" in disposition:
                raise EvidenceCaptureError("Attachment responses are blocked.")
            content_type = response.headers.get_content_type().lower()
            if content_type not in ALLOWED_CONTENT_TYPES:
                raise EvidenceCaptureError(
                    f"Response content type is blocked: {content_type}"
                )
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_RESPONSE_BYTES:
                raise EvidenceCaptureError("Response exceeds the 10 MiB limit.")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise EvidenceCaptureError("Response exceeds the 10 MiB limit.")
            status = response.status
            charset = response.headers.get_content_charset() or "utf-8"
    except (HTTPError, URLError, TimeoutError) as exc:
        raise EvidenceCaptureError(f"Official page request failed: {exc}") from exc

    title = None
    if content_type == "text/html":
        text = body.decode(charset, errors="replace")
        match = TITLE_RE.search(text)
        if match:
            title = re.sub(r"\s+", " ", unescape(match.group(1))).strip()[:500]
    digest = hashlib.sha256(body).hexdigest()
    timestamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    evidence = {
        "schema_version": "phase5.11.3B1/official-page-evidence/v1",
        "record_id": record_id,
        "requested_url": url,
        "final_url": final_url,
        "source_domain": urlparse(final_url).hostname,
        "page_title": title,
        "fetched_at": fetched_at.isoformat(),
        "http_status": status,
        "content_type": content_type,
        "response_bytes": len(body),
        "content_sha256": digest,
        "redirect_count": redirect_handler.redirect_count,
        "evidence_note": note.strip(),
        "body_persisted": False,
    }
    safe_record_id = re.sub(r"[^A-Za-z0-9_.-]", "_", record_id)
    output = root / safe_record_id / f"{timestamp}-{digest[:12]}.json"
    _write_json(output, evidence)
    counter["total"] = int(counter.get("total", 0)) + 1
    counter["records"][record_id] = record_count + 1
    _write_json(root / "request-counter.json", counter)
    evidence["evidence_file"] = str(output)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture bounded metadata-only official-page evidence."
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    try:
        result = capture(args.url, args.record_id, args.evidence_root, args.note)
    except EvidenceCaptureError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
