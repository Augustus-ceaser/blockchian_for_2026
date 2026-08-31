# Phase 5.10 Coordinator Runtime Dependency Audit

Date: 2026-07-26

## Target

- Platform: `linux/amd64`
- Base image: `python:3.12.13-slim-bookworm`
- Resolved base digest: `sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b`
- Python: 3.12.13
- Execution boundary: fixed CPU-only PathMNIST entrypoint
- Isolation boundary: `hard_isolation=false`

## Direct Numerical Dependencies

| Package | Version | Reason |
|---|---:|---|
| torch | 2.13.0+cpu | Load the fixed ResNet-18 state dict and run deterministic CPU inference |
| numpy | 2.3.5 | Read the fixed NPZ dataset, validate tensors and calculate metrics |
| psutil | 7.2.2 | Record bounded process memory evidence |

The execution code builds ResNet-18 internally. It does not import or require
`torchvision`, Pillow, SciPy, pandas or medmnist.

## Coordinator Application Dependencies

The Coordinator imports the existing MedTrust backend package and therefore
needs the production backend runtime set: Alembic, asyncpg, FastAPI, MinIO,
Pydantic Settings, PyYAML, SQLAlchemy and Uvicorn with their resolved runtime
dependencies. Test-only packages and frontend dependencies are excluded.

## Trusted Sources

- Torch CPU wheel: PyTorch official CPU wheel index and its official download CDN.
- Other Python wheels: PyPI and files.pythonhosted.org.
- Base image: Docker Official Image for Python.

No mirror, nightly, CUDA, ROCm, source distribution or Windows environment is
used.

## Verified Wheel

`torch-2.13.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl`

The wheelhouse is local, ignored and must not be committed. The next packaging
step generates exact SHA-256 locks, validates the complete file allowlist and
installs with `--no-index`, `--require-hashes` and
`--only-binary=:all:` during a network-disabled image build.

## Explicit Exclusions

- no Fake Executor
- no arbitrary model or code execution
- no CUDA, cuDNN, ROCm or GPU packages
- no runtime package installation
- no copying of the Windows virtual environment
- no model or dataset download
- no migration or business-state change
