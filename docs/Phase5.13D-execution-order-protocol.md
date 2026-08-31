# Phase 5.13D ExecutionOrder Protocol

Despite its domain name, this phase's ExecutionOrder is a signed control
message:

```text
order_mode = CONTROL_VALIDATION_ONLY
requested_action = VALIDATE_POLICY_ONLY
execution_authorized = false
execution_started = false
```

The central service allocates a monotonically increasing sequence per Connector,
binds the PolicyBundle version digest, signs the canonical JSON, and exposes it
only through the mTLS ingress. Delivery is retryable: delivered orders remain
pullable until the Connector has persisted them.

Database identifiers are carried in the outer transport envelope. They are not
inserted into or used to mutate an already signed payload.
