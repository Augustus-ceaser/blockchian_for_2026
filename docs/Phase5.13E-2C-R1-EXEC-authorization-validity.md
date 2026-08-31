# Phase 5.13E-2C-R1-EXEC Authorization Validity

## Rule

The deployment authority is:

```text
PHASE513E2CR1_EXEC_AUTHORIZATION_TTL_SECONDS=3600
PHASE513E2CR1_EXEC_AUTHORIZATION_SAFETY_MARGIN_SECONDS=300
```

Both central and Connector services receive the corresponding
`MEDTRUST_FIXED_REFERENCE_*` values. The runtime timeout is frozen from the
approved Resource Policy. Policy activation, Order issue, Snapshot creation,
and execution start all require:

```text
remaining validity >= runtime timeout 900 + safety margin 300 = 1200 seconds
```

No UI field can override either value. Policy, Order, Readiness, Status v2,
and Snapshot expiry are capped by their upstream signed evidence and are never
extended in place.

## Negative Evidence

The retained chain was not modified:

- Policy `55c5293f-4278-4bf3-91d1-f65ced92aba3`;
- Order `e293644f-fb55-4cf0-8f0f-71d4fbf4ac7d`;
- Snapshot `d1a65bb8-64a2-4cb7-ac31-94751b1474c2`;
- expiry `2026-07-29T15:04:29.624798+00:00`.

Its browser start attempt returned HTTP 409 `POLICY_EXPIRED`. Snapshot status
remains `validated`, `consumed_at` remains null, and Order
`consumed_count=0`. It created no Task, Runtime, ReferenceExecution, or
Artifact.

## Fresh Start Evidence

The accepted chain expired at `2026-07-29T17:12:53.904049+00:00`.
Execution reservation occurred at `2026-07-29T16:22:22.717377+00:00` with
3031 seconds remaining, exceeding the 1200-second minimum.

