# Phase 5.11.2 External Catalog Connector

## Scope

This phase adds a metadata-only connector for the versioned LXLTX public
medical dataset catalog. It does not download dataset files or model weights,
create `DataProduct`/`ModelProduct` records, publish products, or modify the
Phase 5.1-5.10 lifecycle and execution state machines.

## Controlled source

- Source code: `lxltx-public-medical-datasets`
- Schema version: `1.0`
- Local host URL: `http://127.0.0.1:3000/api/v1`
- Docker URL: `http://host.docker.internal:3000/api/v1`
- Production policy: HTTPS is mandatory.
- Local HTTP requires `MEDTRUST_ALLOW_INSECURE_LOCAL_CATALOG=true`.
- The source URL is backend configuration. Clients cannot submit arbitrary URLs.
- Redirects and non-HTTP(S) schemes are rejected.
- Each response is limited to 50 MiB and must be UTF-8 JSON.

## Storage

Host defaults:

- `D:\MedTrustData`
- `D:\MedTrustCache`

Compose maps these roots to `/var/lib/medtrust/data` and
`/var/cache/medtrust`. Successful versions are stored under separate manifest,
dataset snapshot, and provenance trees. Files are staged in `partial`, checked,
then atomically moved. A same-version digest conflict is rejected.

No snapshot path, cookie, token, credential, or environment dump is returned by
the API or written into provenance.

## Synchronization

1. Request `/catalog/manifest` with the previous ETag.
2. Return `not_modified` without record/version mutation on HTTP 304.
3. Validate source identity, schema, version, record count, asset boundary, and
   manifest digest fields.
4. Fetch the static versioned `datasets.json` byte stream without following
   redirects.
5. Verify its exact SHA-256 and all `external_id` values before database writes.
6. Write the D-drive snapshot.
7. Apply inserts, digest-based updates, unchanged counts, and logical `stale`
   status in one transaction.
8. Append a real actor-bound audit event.

`ExternalDatasetVersion` is created only when a record digest changes.

## API and permissions

All four authenticated demo roles can read sources, records, details, and sync
runs. Only `space_operator` can configure the controlled source and trigger
synchronization. Other roles receive HTTP 403.

The UI routes are:

- `/external-catalog/datasets`
- `/portal/operator/external-catalog`

The UI labels every record as not materialized and warns that catalog inclusion
does not establish possession, redistribution rights, quality, or executability.

## Acceptance

Runtime acceptance completed on 2026-07-27: existing and empty PostgreSQL
migrations, the real 982-record import, HTTP 304 replay, redacted unreachable
failure, D-drive evidence, audit chain, and four-account browser checks passed.
