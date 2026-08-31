# Phase 5.13D Control Readiness Design

`ControlReadinessSnapshot` is a control-plane projection, not an execution
authorization. Its digest binds the Application, Contract revision, Connector,
local metadata mirror version, quality digest, and model metadata reference.

Database constraints force:

```text
readiness_mode = CONTROL_POLICY_VALIDATION
execution_authorized = false
hard_isolation = false
```

A blocked check prevents PolicyBundle compilation. A passed snapshot permits
only compilation of a signed control policy. Phase 5.13E must perform a new,
separate fixed-task execution decision.
