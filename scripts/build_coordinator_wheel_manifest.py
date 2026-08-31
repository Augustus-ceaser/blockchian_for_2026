from __future__ import annotations

import argparse
import hashlib
import json
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from zipfile import ZipFile

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.tags import Tag
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

SCHEMA_VERSION = "medtrust-coordinator-wheel-manifest/v1"
TARGET_PLATFORM = "linux/amd64"
PYTHON_VERSION = "3.12.13"
DIRECT = {
    "alembic",
    "asyncpg",
    "fastapi",
    "minio",
    "numpy",
    "psutil",
    "pydantic-settings",
    "pyyaml",
    "sqlalchemy",
    "torch",
    "uvicorn",
}
FORBIDDEN_MARKERS = ("cuda", "rocm", "nvidia", "nightly")
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metadata(path: Path) -> tuple[str, str, list[str], str | None]:
    with ZipFile(path) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA") and name.count("/") == 1
        ]
        if len(names) != 1:
            raise ValueError(f"{path.name}: expected exactly one METADATA file")
        document = BytesParser(policy=default).parsebytes(archive.read(names[0]))
    return (
        str(document["Name"]),
        str(document["Version"]),
        sorted(str(value) for value in document.get_all("Requires-Dist", [])),
        str(document["Requires-Python"]) if document["Requires-Python"] else None,
    )


def selected_requirements(values: list[str]) -> list[Requirement]:
    environment = default_environment()
    environment.update(
        {
            "python_version": "3.12",
            "python_full_version": PYTHON_VERSION,
            "platform_machine": "x86_64",
            "platform_system": "Linux",
            "sys_platform": "linux",
        }
    )
    result = []
    for value in values:
        requirement = Requirement(value)
        if requirement.marker is None or requirement.marker.evaluate(environment):
            result.append(requirement)
    return result


def validate_tags(filename: str, tags: frozenset[Tag]) -> None:
    if any(marker in filename.lower() for marker in FORBIDDEN_MARKERS):
        raise ValueError(f"{filename}: forbidden accelerator or release marker")
    for tag in tags:
        if tag.platform == "any":
            if tag.abi != "none" or tag.interpreter not in {"py3", "cp312"}:
                raise ValueError(f"{filename}: unsupported universal tag {tag}")
            continue
        if not (
            tag.platform == "linux_x86_64"
            or (
                tag.platform.startswith("manylinux")
                and tag.platform.endswith("_x86_64")
            )
        ):
            raise ValueError(f"{filename}: unsupported platform tag {tag.platform}")
        if tag.interpreter == "cp312":
            if tag.abi not in {"cp312", "abi3"}:
                raise ValueError(f"{filename}: unsupported ABI tag {tag.abi}")
        elif tag.interpreter in {"cp39", "cp38", "cp37", "cp36"}:
            if tag.abi != "abi3":
                raise ValueError(f"{filename}: unsupported ABI tag {tag.abi}")
        else:
            raise ValueError(f"{filename}: unsupported interpreter tag {tag.interpreter}")


def build(wheelhouse: Path) -> dict[str, object]:
    files = sorted(wheelhouse.glob("*.whl"), key=lambda value: value.name.lower())
    extras = sorted(
        path.name
        for path in wheelhouse.iterdir()
        if path.is_file() and path.suffix != ".whl" and path.name != "SHA256SUMS"
    )
    if extras:
        raise ValueError(f"wheelhouse contains unsupported files: {extras}")
    packages: dict[str, dict[str, object]] = {}
    for path in files:
        parsed_name, parsed_version, _, tags = parse_wheel_filename(path.name)
        validate_tags(path.name, tags)
        name, version, requires_dist, requires_python = metadata(path)
        normalized = canonicalize_name(name)
        if normalized != parsed_name or Version(version) != parsed_version:
            raise ValueError(f"{path.name}: filename and METADATA disagree")
        if normalized in packages:
            raise ValueError(f"{path.name}: duplicate package {normalized}")
        packages[normalized] = {
            "name": normalized,
            "version": version,
            "filename": path.name,
            "sha256": sha256(path),
            "size_bytes": path.stat().st_size,
            "source": "pytorch-official-cpu" if normalized == "torch" else "pypi-official",
            "direct": normalized in DIRECT,
            "requires_dist": requires_dist,
            "requires_python": requires_python,
            "tags": sorted(str(tag) for tag in tags),
        }
    missing_direct = sorted(DIRECT - packages.keys())
    if missing_direct:
        raise ValueError(f"missing direct dependencies: {missing_direct}")
    required = set(DIRECT)
    queue = list(DIRECT)
    while queue:
        current = queue.pop()
        for requirement in selected_requirements(packages[current]["requires_dist"]):
            dependency = canonicalize_name(requirement.name)
            if dependency not in packages:
                raise ValueError(f"{current}: missing dependency {dependency}")
            if Version(str(packages[dependency]["version"])) not in requirement.specifier:
                raise ValueError(
                    f"{current}: {dependency} {packages[dependency]['version']} "
                    f"does not satisfy {requirement.specifier}"
                )
            if dependency not in required:
                required.add(dependency)
                queue.append(dependency)
    unknown = sorted(packages.keys() - required)
    if unknown:
        raise ValueError(f"wheelhouse contains unknown packages: {unknown}")
    return {
        "schema_version": SCHEMA_VERSION,
        "target_platform": TARGET_PLATFORM,
        "python_version": PYTHON_VERSION,
        "packages": [packages[name] for name in sorted(packages)],
    }


def write_outputs(document: dict[str, object], output: Path, lock: Path, sums: Path) -> None:
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    packages = document["packages"]
    lock.write_text(
        "".join(
            f"{item['name']}=={item['version']} \\\n"
            f"    --hash=sha256:{item['sha256']}\n"
            for item in packages
        ),
        encoding="utf-8",
    )
    sums.write_text(
        "".join(f"{item['sha256']}  {item['filename']}\n" for item in packages),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--sums", type=Path, required=True)
    args = parser.parse_args()
    document = build(args.wheelhouse.resolve(strict=True))
    write_outputs(document, args.manifest, args.lock, args.sums)
    print(f"verified {len(document['packages'])} wheels")


if __name__ == "__main__":
    main()
