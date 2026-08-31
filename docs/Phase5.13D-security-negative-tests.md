# Phase 5.13D Security Negative Tests

## Automated coverage

Backend and Connector tests cover role guards, control-only database checks,
private-key exclusion, OpenAPI surface, mTLS certificate/status enforcement,
independent local sessions, local migration, validation check presence, and the
absence of execution/data-transfer endpoints.

Runtime mutation tests used a real signed accepted order and in-memory copies.
They confirmed rejection for replay/sequence rollback, unknown and revoked
keys, policy/order digest changes, policy/order signature changes,
`execution_authorized=true`, wrong action, additional property, prohibited
`patient_id`, metadata mismatch, and quality mismatch.

## Runtime business negatives

- one real order reached `validation_failed`; local human override was not available;
- one valid order was manually rejected by `local.policy-reviewer`;
- one accepted order became `revoked_after_acceptance`;
- the final formal order remains `accepted` and not executed.

No negative test wrote a forged state with SQL/ORM. Mutation tests were
read-only and did not persist payloads.

## Remaining depth

This alpha does not claim exhaustive protocol fuzzing, hardware-backed key
attestation, production certificate revocation infrastructure, or network
adversary testing.
