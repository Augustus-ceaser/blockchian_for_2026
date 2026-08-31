# Phase 5.13C Isolated Database Test Environment

`compose.phase513c-test.yml` provides PostgreSQL 16 on loopback port 55433 with
the dedicated volume `medtrust-phase513c-test_postgres_data`. It does not share
canonical PGDATA. The volume is retained after testing.

## Results

- Empty database migration through `20260729_0051`: passed.
- Phase 5.13B plus Phase 5.13C focused suite: 20 passed.
- Backend suite without database variables: 183 passed, 66 environment-gated
  tests skipped.
- Full suite against a fresh isolated PostgreSQL: 10 historical failures whose
  schema tests hard-code `20260725_0032`; all other executable tests passed.
- The earlier outbox lease failure disappeared after using a fresh database,
  confirming it was cross-test state contamination.

The ten stale version assertions are not recorded as passed and are not a
Phase 5.13C acceptance blocker. Destructive concurrency and PathMNIST controlled
smoke tests remain gated by their separate explicit environments.
