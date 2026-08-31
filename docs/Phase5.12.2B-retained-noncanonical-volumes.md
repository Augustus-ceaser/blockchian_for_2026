# Phase 5.12.2B retained noncanonical state

No second noncanonical source volume was found.

The following state is retained and is not the default local business target:

- Database `medtrust` inside `medtrust-space_postgres_data`: contains a
  model-only catalog view and unrelated development history. It was dumped as
  `model-catalog-temporary-database.dump`; it was not merged into canonical.
- Stopped legacy `medtrust-space-lan-*` containers: they are not started by the
  corrected Compose project and must never mount canonical storage while the
  canonical services run.
- Recovery clone `medtrust-inspect-pg-01`: recovery evidence only, not a
  business volume.

All source storage was retained. Future deletion requires separate approval.
The canonical local database is `medtrust_phase4_demo`; the canonical physical
volumes are `medtrust-space_postgres_data` and
`medtrust-space_minio_data`.
