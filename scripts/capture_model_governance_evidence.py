from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


MAX_BYTES = 10 * 1024 * 1024
ALLOWED_HOSTS = {
    "api.github.com",
    "arxiv.org",
    "github.com",
    "huggingface.co",
    "www.nature.com",
    "pmc.ncbi.nlm.nih.gov",
    "zenodo.org",
}
BLOCKED_SUFFIXES = (
    ".safetensors", ".pth", ".pt", ".ckpt", ".onnx", ".bin", ".pb", ".h5",
    ".pkl", ".joblib", ".tflite", ".mlmodel", ".gguf", ".tar", ".tar.gz",
    ".zip", ".7z", ".rar", ".whl", ".conda", ".sif", ".img", ".iso",
)
ALLOWED_CONTENT_TYPES = (
    "application/json",
    "application/ld+json",
    "application/vnd.github+json",
    "text/html",
    "text/plain",
)


def capture(url: str, destination: Path) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"URL host is not allowlisted: {url}")
    lowered_path = parsed.path.lower()
    if any(lowered_path.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        raise ValueError(f"Binary or archive URL is blocked: {url}")

    request = Request(
        url,
        headers={
            "Accept": "application/json,text/plain,text/html;q=0.9",
            "User-Agent": "MedTrust-Metadata-Governance/1.0",
        },
    )
    with urlopen(request, timeout=30) as response:
        final_url = response.geturl()
        final_parsed = urlparse(final_url)
        if final_parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"Redirect left the allowlist: {final_url}")
        disposition = response.headers.get("Content-Disposition", "")
        if "attachment" in disposition.lower():
            raise ValueError(f"Attachment response is blocked: {url}")
        content_type = response.headers.get_content_type().lower()
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"Response content type is blocked: {content_type}")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > MAX_BYTES:
            raise ValueError(f"Response exceeds {MAX_BYTES} bytes: {url}")
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError(f"Response exceeds {MAX_BYTES} bytes: {url}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    return {
        "requested_url": url,
        "final_url": final_url,
        "content_type": content_type,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "destination": str(destination),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    entries = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or len(entries) > 80:
        raise ValueError("Manifest must contain at most 80 requests.")
    results = []
    for entry in entries:
        results.append(
            capture(
                str(entry["url"]),
                args.output_root / str(entry["path"]),
            )
        )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps({"requests": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"captured": len(results)}, sort_keys=True))


if __name__ == "__main__":
    main()
