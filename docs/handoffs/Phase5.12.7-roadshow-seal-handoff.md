# Phase 5.12.7 Handoff

Phase 5.12.7 seals the public catalog and trusted reference-run engineering
roadshow. Start from `docs/roadshow/PHASE5.12.7-ACCEPTANCE.md` and
`docs/roadshow/ROADSHOW-STATE-MANIFEST.json`.

The accepted canonical state uses Alembic `20260728_0049`, PostgreSQL volume
`medtrust-space_postgres_data`, and MinIO volume
`medtrust-space_minio_data`. Do not reset or replace these volumes.

The unified overview is read-only. The public CONCH and UNI products remain
metadata-only and blocked from execution. The only platform-verified relation
is the locked historical PathMNIST/ResNet-18 reference chain.

Next work is not an extension of this frozen roadshow transaction. Open a new
manual phase for one of:

1. externally licensed model weight materialization;
2. hospital-side Connector, object storage and Executor;
3. separately reviewed LAN or remote deployment.

Do not create `v0.13` until the owner explicitly authorizes a release tag.
