# Phase 5.13E-2C-R Causal Chain Gap Audit

## Verdict

The Phase 5.13E-2B-1 execution was a real, bounded engineering execution, but
it is not eligible for an EvidenceBundle. It must remain unchanged.

```text
classification = engineering_execution_only
evidence_eligible = false
evidence_ineligibility_reason = MISSING_PRE_EXECUTION_AUTHORIZATION_BINDING
retroactive_binding_allowed = false
```

The accepted central order was limited to `CONTROL_VALIDATION_ONLY`, carried
`execution_authorized=false`, and expired before execution started. The local
admission also recorded `execution_enabled=0`. Runtime, task, execution, and
Artifact records contain no immutable PolicyBundle or ExecutionOrder binding.
Adding those references after execution would create a false causal history.

## Audited Chain

| Object | ID | Created or effective time | State | Existed before execution | Policy ref | Order ref | Digest | Immutable | Causality proved | Evidence eligible | Finding |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Application | `2d33091b-d8c9-50ce-b9d6-084bb18abda1` | before policy | approved | yes | n/a | n/a | snapshot digest present in policy | historical | partial | no | Application alone is not execution authorization. |
| Contract | `0b7ddd9a-e44a-5d3b-b7d1-54a8c5521029` | before policy | active | yes | n/a | n/a | contract digest present in policy | historical | partial | no | Contract alone is not execution authorization. |
| Readiness | `0962fada-2670-4030-8b28-881d9dc6d4a1` | before policy | passed | yes | n/a | n/a | `sha256:03d62f...a84894` | yes | no | no | Control validation only; execution authorization is false. |
| PolicyBundle | `0dbde3f3-f0f2-4e9e-b77d-b6ea08cf33cb` | before order | active at issue | yes | self | no | version digest below | signed version | no | no | Requested action is `VALIDATE_POLICY_ONLY`. |
| PolicyBundleVersion | `940934ef-e37b-46f2-b846-098da39145cb` | `2026-07-29T07:54:05Z` | signed | yes | self | no | `sha256:5826b1...952cd1` | yes | no | no | `execution_authorized=false`; `EXECUTE` is forbidden. |
| ExecutionOrder | `ORD-d8d04c521f01dc8d` | `2026-07-29T07:54:08Z` | accepted locally | yes | yes | self | `sha256:d8cf50...840df1` | signed payload | no | no | `CONTROL_VALIDATION_ONLY`; expired `09:54:08Z`. |
| Connector Receipt | local order `623e15dc-5d90-4e18-95a7-4599a6fe98c8` | `2026-07-29T07:54:24Z` | received | yes | yes | yes | signed receipt exists | append-only | no | no | Receipt proves delivery, not execution permission. |
| Connector Decision | `cb377a63-b5ca-4d7d-be2b-fe00808004f8` | `2026-07-29T07:54:29Z` | accepted | yes | indirect | indirect | signed decision exists | immutable | no | no | Reason explicitly says control-only and execution unauthorized. |
| Executor Admission Check | `0dcd13ab-c3b3-4382-8443-812fa09a0ccf` | `2026-07-29T11:03:08Z` | approved | yes | no | no | `sha256:075a2c...41fb2c` | immutable | no | no | Admission snapshot records `execution_enabled=0`. |
| Runtime Session | `e9393bfc-9f4d-45dd-bd03-c8afc5bd01e5` | `2026-07-29T11:04:35Z` | completed | no | no | no | runtime digest present | terminal lifecycle | no | no | Created after order expiry with no authorization snapshot. |
| ExecutionTaskManifest | `48edd32b-2c11-49fc-914c-e87f6de53fee` | `2026-07-29T11:04:35Z` | fixed task | no | no | no | `sha256:5e8aac...139fe2` | immutable | no | no | No pre-execution Policy/Order/Decision/Admission binding. |
| ExecutionInputManifest | `6aec82b6-8f96-4b01-90bd-5aa31fd4fcef` | `2026-07-29T11:04:35Z` | fixed 20 samples | no | no | no | `sha256:fcd919...196aed` | immutable | no | no | Input integrity is proved; authorization is not. |
| ReferenceExecution | `ba2d630a-8a9f-4115-915a-91b6b246bc38` | `2026-07-29T11:04:35Z` | completed | no | no | no | result digest present | terminal | no | no | Real 19/20 execution, but no prior authorization binding. |
| Local Artifact | `f483ef8f-8e41-4f27-a385-40f8913fa39a` | `2026-07-29T11:04:45Z` | approved | no | no | no | `sha256:998c04...49975` | terminal review protected | no | no | Aggregate output is valid but inherits the execution eligibility failure. |
| Scanner Report | `28213e9e-b533-4e0c-be5a-bef64ce8f7eb` | `2026-07-29T11:24:59Z` | passed | no | no | no | `sha256:6f1eb6...42fd6` | immutable | no | no | Scanner approval cannot repair missing execution authorization. |
| Artifact Review | `0144bf8f-21ca-4e3e-a578-8de260444d92` | `2026-07-29T11:25:01Z` | approved | no | no | no | `sha256:393708...493e` | immutable | no | no | Review establishes output acceptability, not causal authorization. |

## Required Compensating Chain

The only acceptable remediation is a new independent chain:

```text
execution-authorized PolicyBundleVersion
  -> unexpired FIXED_REFERENCE_EXECUTION order
  -> Connector automatic validation
  -> independent local policy acceptance
  -> Executor admission
  -> immutable ExecutionAuthorizationSnapshot
  -> pre-bound Runtime and Task Manifest
  -> one new PATHMNIST_REFERENCE_V1 execution
  -> new quarantined Artifact
  -> new scanner report and independent Artifact review
  -> causal-chain validation
  -> separately signed minimal EvidenceBundle
  -> central metadata-only verification and registration
```

The compensating environment must be isolated from canonical PostgreSQL,
MinIO, Connector state, and signing roots. No old object may be updated, no
old output may be copied into the new execution, and no raw data, model
weights, local paths, or original Artifact may be transmitted centrally.

## Compensating Result

Phase 5.13E-2C-R1 completed the required new chain without changing the
historical E-2B-1 objects. The first R1 Policy/Order/Snapshot expired and was
correctly rejected. A second fresh chain created Task, Runtime, and
ReferenceExecution bindings before Worker dispatch, consumed its Snapshot
once, and generated one new quarantined Artifact.

The old execution remains:

```text
evidence_eligible = false
reason = MISSING_PRE_EXECUTION_AUTHORIZATION_BINDING
retroactive_binding_allowed = false
```

The new Artifact is not yet evidence eligible. R2 scanning/review and R3
EvidenceBundle registration remain separate, unstarted gates.
