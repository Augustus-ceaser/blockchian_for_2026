# Phase 5.12.7-Z2E Canonical 状态差异

## 比较结论

- 测试基线：`097ac29393323b17c69cf3cd894814a70de0c5f6`
- Alembic：前后均为 `20260728_0049`
- 原始只读业务状态 SHA-256：前后均为 `d5d0d086a6185effcceacecbbd4da99de8cc73fc4eb0a5ede36a5b597356289a`
- 带快照上下文的文档：逐字节一致
- canonical 业务状态变化：`false`
- audit chain：前后均有效，`invalid_sequence=null`

## 计数对比

| 对象 | Before | After | 差异 |
|---|---:|---:|---:|
| Application | 3 | 3 | 0 |
| Contract | 3 | 3 | 0 |
| ComputeJob | 3 | 3 | 0 |
| ComputeRun | 2 | 2 | 0 |
| Artifact | 2 | 2 | 0 |
| ReleasePackage | 2 | 2 | 0 |
| DownloadGrant | 2 | 2 | 0 |
| DataProduct | 7 | 7 | 0 |
| ModelProduct | 4 | 4 | 0 |
| Dataset GovernanceReview | 80 | 80 | 0 |
| Model GovernanceReview | 96 | 96 | 0 |
| DatasetModelRelation | 7 | 7 | 0 |
| DatasetModelEvidence | 8 | 8 | 0 |
| MaterializationPlan | 0 | 0 | 0 |
| MinIO objects | 30 | 30 | 0 |
| AuditEvent | 353 | 353 | 0 |

## 存储与审计

- canonical PostgreSQL volume：`medtrust-space_postgres_data`
- canonical MinIO volume：`medtrust-space_minio_data`
- audit head sequence：前后均为 `353`
- audit head digest：前后均为 `sha256:75b9b969d8a3d81f65aab15c5fbfa8ab3b6c805581624cc0cac602332db7e866`
- canonical MinIO bucket/object 清单：前后完全一致
- Z2E PostgreSQL 与 MinIO 使用独立 volume；没有共享 PGDATA 或对象存储目录
- 验收期间 canonical PostgreSQL 和 MinIO 保持原运行状态，Z2E 应用仅写入隔离环境

## 判定

`CANONICAL-STATE-BEFORE.json` 与 `CANONICAL-STATE-AFTER.json` 的业务内容和快照上下文均一致。Phase 5.12.7-Z2E 没有向 canonical 正式环境写入业务对象，主环境保护条件通过。
