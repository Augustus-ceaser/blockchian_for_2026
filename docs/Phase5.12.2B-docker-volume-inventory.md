# Phase 5.12.2B Docker volume inventory

## Inventory

Audit date: 2026-07-27.

| Volume | Compose owner label | Contents | Classification |
| --- | --- | --- | --- |
| `medtrust-space_postgres_data` | `medtrust-space/postgres_data` | PostgreSQL 16 cluster with `medtrust` and `medtrust_phase4_demo` databases | canonical local PostgreSQL volume |
| `medtrust-space_minio_data` | `medtrust-space/minio_data` | six historical result buckets, quarantine and release objects | canonical local MinIO volume |
| `medtrust-space-lan_coordinator_workspaces` | `medtrust-space-lan/coordinator_workspaces` | controlled-execution workspace | retained non-database runtime volume |
| `medtrust-inspect-pg-01` | recovery-only | offline clone used to validate transaction-ID recovery | temporary audit clone |

No second source PostgreSQL or MinIO named volume existed. The apparent split
was between two databases in one PostgreSQL cluster:

| Database | Alembic | Dataset records | Reviews | Source links | Model records | Historical business |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `medtrust_phase4_demo` | `20260727_0042` after recovery | 982 | 80 | 6 | 16 | 3 applications, 3 contracts, 3 jobs, 2 runs, 2 artifacts |
| `medtrust` | `20260727_0042` | 0 | 0 | 0 | 16 | separate development history |

## Backups

Recovery root:
`D:\MedTrustData\volume-recovery\phase5.12.2B-20260727-202054`

- PostgreSQL physical tar: 1,169,773,056 bytes, SHA-256
  `62B607BB1A3400BA9CB9A55BBA42AFFE34709CFE6633CA22557EC265314DFADB`.
- MinIO physical tar: 417,792 bytes, SHA-256
  `021DDAFED15C1B1D6CE49095D122BE8831829DDC0429C894CAF0BDE8B0B0D7CE`.
- Recovered canonical logical dump: 1,221,830 bytes, SHA-256
  `224CCCDB5A1DD31717274F6F89AC8A6C839D981B6F98AB654A03EB361851DE9B`.
- Recovered canonical schema: 476,548 bytes, SHA-256
  `F54B0C67EFCA5BF5FB1203CF7D404CF0318913A22AA9679687EAF2A054B5B8BA`.

No source volume was deleted or renamed. No business row was copied with SQL.
