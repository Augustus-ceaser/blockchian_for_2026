# Phase 5.13D Known Limitations

- Control acceptance is not execution approval and does not create a Job, Run, or Artifact.
- `hard_isolation=false`; this is not a hospital production isolation claim.
- The Ed25519 key is a D-drive local test key, not an HSM/KMS key.
- Connector certificates use the local test CA and loopback-only ingress.
- Model references are metadata only; local model availability was not proven.
- Raw data, local paths, patient identifiers, and model weights were not transferred.
- Global `alembic check` still contains the pre-existing schema-reflection drift.
- The raw canonical dump SHA changes because PostgreSQL 17-style dumps emit a
  random `\restrict` token. Removing only the matching `\restrict` and
  `\unrestrict` lines yields identical before/after SHA-256
  `b9ca49abb6ce7ea9cc95c97f2c6e6c3d4cdc0c0a06902970ab652cfe7e15431`.
- Phase 5.13E has not started.
