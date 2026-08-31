from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from build_coordinator_wheel_manifest import build


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    wheelhouse = args.wheelhouse.resolve(strict=True)
    expected = json.loads(args.manifest.read_text(encoding="utf-8"))
    expected_files = {
        item["filename"]: item["sha256"] for item in expected["packages"]
    }
    actual_files = {path.name: path for path in wheelhouse.iterdir() if path.is_file()}
    allowed_files = set(expected_files) | {"SHA256SUMS"}
    missing = sorted(set(expected_files) - set(actual_files))
    if missing:
        raise SystemExit(f"wheelhouse is missing files: {missing}")
    unknown = sorted(set(actual_files) - allowed_files)
    if unknown:
        raise SystemExit(f"wheelhouse contains unknown files: {unknown}")
    for filename, expected_digest in expected_files.items():
        actual_digest = sha256(actual_files[filename])
        if actual_digest != expected_digest:
            raise SystemExit(f"{filename}: SHA-256 mismatch")
    actual = build(wheelhouse)
    if actual != expected:
        raise SystemExit("wheelhouse does not match the committed manifest")
    sums = wheelhouse / "SHA256SUMS"
    expected_sums = "".join(
        f"{item['sha256']}  {item['filename']}\n" for item in actual["packages"]
    )
    if not sums.is_file() or sums.read_text(encoding="utf-8") != expected_sums:
        raise SystemExit("SHA256SUMS is missing or invalid")
    print(f"verified {len(actual['packages'])} wheels")


if __name__ == "__main__":
    main()
