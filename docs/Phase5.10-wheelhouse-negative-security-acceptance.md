# Phase 5.10 Wheelhouse Negative Security Acceptance

Date: 2026-07-26

Status: passed against temporary copies; the authoritative wheelhouse was not modified.

## Baseline

- Wheels: 40
- Manifest SHA-256: `283b048f8c166459a4f95a15b5d73a670d8d8c397fe43fe21dcd6ea6e42be12c`
- Lock SHA-256: `6a6e525615308a8eb1253386da12df7b00dfe4fedd1564d652b368bf36faee9d`
- `SHA256SUMS` SHA-256 starts with `fd96b5f998f727c32ac5860c7f8514b91add6fa14`

## Results

| Attack | Result | Rejection |
|---|---|---|
| Wheel byte mutation | Passed | Explicit SHA-256 mismatch |
| Missing or unknown wheel | Passed | Missing/unknown file list |
| Source distribution | Passed | Unsupported file |
| Windows, macOS, arm64 or musllinux wheel | Passed | Unsupported platform |
| Wrong CPython or ABI tag | Passed | Unsupported interpreter/ABI |
| CUDA, NVIDIA or ROCm marker | Passed | Forbidden accelerator marker |
| Lock mutation | Passed | Committed lock mismatch |
| Traversal, absolute or drive path | Passed | Unsafe path |
| Duplicate, hidden, extra or over-deep ZIP entry | Passed | Explicit structural rejection |
| ZIP symbolic link | Passed | Symbolic link |
| Missing manifest, lock or sums | Passed | Missing/mismatch/verification failure |

The arm64 case initially exposed a real defect: a broad `manylinux` substring
check accepted `aarch64`. The validator now requires `manylinux*_x86_64` or
`linux_x86_64`, and the complete matrix passes.

Final verification again reported `verified 40 wheels` with unchanged baseline
digests. No dependency was downloaded during attack testing.
