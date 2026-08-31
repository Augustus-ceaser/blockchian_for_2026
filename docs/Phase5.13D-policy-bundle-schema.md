# Phase 5.13D PolicyBundle Schema

Schema: `phase5.13D/policy-bundle/v1`.

The payload binds application and contract digests, control readiness, Connector
ID, local asset key/version and metadata/quality digests, model metadata
reference, purpose, time window, nonce, signing key, allowed operations, and
forbidden operations.

Frozen boundaries:

```text
requested_action = VALIDATE_POLICY_ONLY
execution_authorized = false
hard_isolation = false
model_materialization_status = NOT_EVALUATED_IN_PHASE_5_13D
```

Allowed operations are signature verification, digest verification, and local
policy review. Raw-data access, model loading, execution, Artifact creation,
and egress are forbidden.
