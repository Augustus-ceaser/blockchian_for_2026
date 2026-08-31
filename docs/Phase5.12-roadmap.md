# Phase 5.12 Roadmap

## Completed

- Phase 5.12.1-5.12.2: 16-record public model candidate catalog in the canonical
  runtime alongside the 982-record dataset candidate catalog.
- Phase 5.12.3: governed CONCH, UNI and Prov-GigaPath metadata-only drafts.
- Phase 5.12.4: published CONCH and UNI for discovery; retained Prov-GigaPath
  as a draft.
- Phase 5.12.5: version-locked, append-only dataset-model static evidence graph
  with six public relationships and bidirectional UI.

## Next: Phase 5.12.6

Select one pair for a controlled materialization plan. Selection must be based
on exact license/access evidence, immutable revisions, download size, hardware
fit and reproducible preprocessing. A static relationship is not sufficient.

Candidate planning order:

1. compare CAMELYON17 and CPTAC-COAD subset accessibility;
2. compare CONCH and UNI weight access, license, revision and CPU/GPU needs;
3. choose one pair only;
4. estimate D-drive space before any download;
5. require SHA-256 verification and atomic promotion;
6. register no Executor until a local smoke test passes.

Phase 5.12.7 may create runtime evidence only from a real controlled run and
may create verification evidence only after Artifact and release review.
# Phase 5.12.6A update (2026-07-28)

The controlled materialization preflight is complete with a fail-closed
terminal result:

```text
selected candidate = 0
approved plan = 0
Phase 5.12.6B = blocked
```

The four existing pathology pairs remain static metadata relations. CONCH and
UNI require gated account approval and private user tokens, so neither can be
approved under the current no-private-credential policy. The next roadmap gate
is discovery and governance of a smaller, publicly downloadable,
CPU-compatible pathology model before repeating Phase 5.12.6A.

## Phase 5.12.6B-R complete (2026-07-28)

The existing PathMNIST plus fixed ResNet-18 historical chain is now represented
by one runtime-execution Evidence and one platform-verification Evidence. This
completes the reference evidence loop without new execution or materialization.
Phase 5.12.7 should seal the roadshow, regression evidence, and handoff; new
external model materialization is an independent enhancement.
