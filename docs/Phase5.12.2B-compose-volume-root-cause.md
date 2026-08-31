# Phase 5.12.2B Compose and PostgreSQL root cause

## Root cause

`compose.yaml` used project `medtrust-space`; `compose.lan.yml` used
`medtrust-space-lan`. The LAN override explicitly mounted the same external
PostgreSQL and MinIO volumes as local mode. Docker therefore created two
PostgreSQL containers and two MinIO containers, while both pairs mounted the
same writable storage.

The two PostgreSQL containers also selected different databases:
`medtrust` and `medtrust_phase4_demo`. This initially looked like a named-volume
split. It was not safe isolation: two PostgreSQL server processes had written
the same PGDATA.

The second server committed transactions with IDs ahead of the transaction ID
stored in the cluster control state. After both servers stopped, PostgreSQL
treated those rows as future transactions. Effects included:

- Alembic 0042 and external catalog tables temporarily appearing absent;
- 982 dataset rows and 16 model rows appearing in different runtime views;
- `pg_dump` failing on temporarily invisible catalog relations;
- demo login `FOR UPDATE` waiting on future tuple `xmax` values.

## Recovery

The source volume was stopped and physically backed up. Recovery was first
validated on `medtrust-inspect-pg-01`. Advancing PostgreSQL transaction IDs
beyond all observed future IDs and restarting made committed catalog and
business tuples visible. The same PostgreSQL-level recovery was then applied to
the source cluster. No business `INSERT`, `UPDATE`, `DELETE`, dump merge, or
state-machine change was used.

## Prevention

- Local, loopback and LAN now use the single Compose project
  `medtrust-space`.
- PostgreSQL and MinIO are explicit external volumes controlled by
  `MEDTRUST_POSTGRES_VOLUME_NAME` and `MEDTRUST_MINIO_VOLUME_NAME`.
- Missing and empty volumes fail before initialization.
- An unexpected or duplicate running container on either volume fails startup.
- Local defaults select `medtrust_phase4_demo`.
- Remote preview and production examples remain separate configurations and
  are not pointed at local canonical storage.
