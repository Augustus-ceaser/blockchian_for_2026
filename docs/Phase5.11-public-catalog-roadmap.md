# Phase 5.11 Public Catalog Roadmap

## Completed

1. Imported 982 immutable external metadata candidates.
2. Added versioned synchronization, governance profiles and append-only reviews.
3. Completed evidence-led governance for a selected set.
4. Created five metadata-only DataProduct drafts plus one archived permission
   test object with retained provenance.
5. Published three products as discoverable, unmaterialized and non-executable.

## Current Boundary

The public catalog is a discovery and governance layer. It is not a local data
lake, download service, redistribution grant, compute registry, or clinical
dataset. Upstream terms and access conditions remain authoritative.

## Next Controlled Phase

Phase 5.12 may design a public model catalog, but should not automatically
download weights or bind models to these metadata products. Before any dataset
materialization, add a separate request and evidence process covering current
license terms, authorization, checksum/manifest, storage budget, data quality,
compatibility and revocation.

Do not infer compute readiness from `published`. Materialization and execution
must remain separate approvals.
