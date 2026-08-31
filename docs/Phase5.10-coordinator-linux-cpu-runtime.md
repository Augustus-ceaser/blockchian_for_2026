# Phase 5.10 Coordinator Linux CPU Runtime

Date: 2026-07-26

Status: offline image and fixed-asset smoke passed; Compose business-chain acceptance remains open.

## Reproducible Runtime

- Platform: `linux/amd64`
- Base: `python:3.12.13-slim-bookworm`
- Base digest: `sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b`
- Python: 3.12.13
- Torch: 2.13.0+cpu
- NumPy: 2.3.5
- psutil: 7.2.2
- CUDA runtime: absent
- Runtime user: `10001:10001`
- Wheel count: 40

The committed manifest and lock contain exact versions and one SHA-256 for
every selected Linux or universal wheel. The wheel payloads remain in ignored
local storage.

## Offline Build

The dependency installation was tested in a temporary container with no
network and then repeated in `docker/coordinator.Dockerfile` using:

- `--no-index`
- `--find-links`
- `--only-binary=:all:`
- `--require-hashes`
- `--no-cache-dir`
- `RUN --network=none`

`pip check` passed. The build log contained only local wheel processing during
dependency installation.

## Image Evidence

- Tag: `medtrust-space-coordinator:phase5.10`
- Image ID: `sha256:3c26323fa51cc80da9459c1ef9e7f4fe1c7f9f36cab110d7388706e0d3060df1`
- Size: 330,581,585 bytes
- Client-facing ports: none
- Healthcheck: Python import and CPU-only CUDA assertions

The final image does not contain the wheelhouse, model asset, dataset asset,
frontend, tests, Git data or local configuration.

## Runtime And Fixed Assets

The non-root runtime check passed:

- Python, Torch, NumPy and psutil exact versions
- `torch.version.cuda is None`
- `torch.cuda.is_available() is False`
- deterministic CPU forward

The fixed model and dataset were mounted read-only and executed through the
existing PathMNIST implementation:

- input images: 20
- correct predictions: 19
- accuracy: 0.95
- mean confidence: 0.960102856159
- model digest: verified and unchanged
- dataset digest: verified and unchanged
- outputs: exactly `aggregate_metrics.json`, `confusion_matrix.csv`,
  `execution_summary.json`

## Remaining Work

- productize fetch, export, import and controlled-context build scripts;
- connect the fixed image to loopback/LAN/remote Compose;
- run the existing Coordinator, Dispatcher and Callback business chain;
- verify Callback replay, quarantined Artifact and audit chain;
- complete browser Console, Network and responsive acceptance.

The runtime remains an allowlisted in-process engineering prototype with
`hard_isolation=false`. It is not a production sandbox or clinical system.
