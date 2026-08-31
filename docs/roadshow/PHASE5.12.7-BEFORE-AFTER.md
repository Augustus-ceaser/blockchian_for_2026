# Phase 5.12.7 Before/After

The canonical business projection before and after acceptance is byte-for-byte
identical:

- before: `Phase5.12.7-business-state-before.json`
- after: `Phase5.12.7-business-state-after.json`
- SHA-256:
  `D5D0D086A6185EFFCCEACECBBD4DA99DE8CC73FC4EB0A5EDE36A5B597356289A`

## Code Changes

- deterministic read-only state and manifest generator;
- authenticated GET-only roadshow overview API;
- unified live roadshow overview at `/roadshow`;
- previous workflow retained at `/roadshow/workflow`;
- fail-closed start, preflight, status and stop scripts;
- responsive containment for the 390px evidence matrix;
- focused backend and frontend tests.

## Explicit Non-Changes

- no migration;
- no business state-machine change;
- no API write endpoint;
- no reset or direct SQL mutation;
- no external data or model download;
- no materialization plan approval;
- no Executor registration;
- no new ComputeJob or ComputeRun;
- no Artifact, package or grant mutation;
- no LAN, firewall, Tunnel or gateway exposure;
- no tag and no `v0.13`.

