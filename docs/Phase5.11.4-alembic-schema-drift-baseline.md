# Phase 5.11.4 Alembic Schema Drift Baseline

## Start Baseline

Recorded before Phase 5.11.4 implementation on 2026-07-27:

- database revision: `20260727_0040`
- repository head: `20260727_0040`
- `alembic current`: pass
- `alembic heads`: one head
- `alembic check`: fails with pre-existing comparison noise

The check reports removal of `public.alembic_version` and a large set of
remove/add foreign-key pairs where reflected foreign keys omit the `medtrust`
schema but model metadata includes it. This is the same schema/FK comparison
class documented in B2. It is pre-existing technical debt, not evidence that
the database is behind the migration head.

## Phase Rule

Any Phase 5.11.4 schema change must use a new independent migration and appear
in both model metadata and the database. At completion:

- `upgrade head`, `current`, and `heads` must pass;
- new tables/constraints/indexes must be directly verified;
- the final `alembic check` operation classes must be compared with this
  baseline;
- no new unexplained table, column, constraint, or index difference may be
  hidden inside the existing foreign-key noise.

The historical migration chain will not be rewritten during this phase.

