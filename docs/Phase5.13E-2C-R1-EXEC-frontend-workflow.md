# Phase 5.13E-2C-R1-EXEC Frontend Workflow

All formal human writes were completed in role-authenticated browser pages:

1. `local.connector-admin` signed fresh Executor Status v2 sequence 18.
2. `operator.demo` compiled, signed, and activated the fresh fixed Policy.
3. `operator.demo` created the fresh signed ExecutionOrder.
4. `local.policy-reviewer` pulled the Order over mTLS, reviewed 44/44 checks,
   and recorded `ACCEPT_FIXED_REFERENCE_EXECUTION`.
5. `local.execution-operator` viewed the validated Snapshot and frozen
   prebindings, then selected **Start approved fixed reference execution**.

The execution page states:

```text
PATHMNIST_REFERENCE_V1
Fixed 20-sample non-clinical reference
max executions=1
no network
no data or model transfer
hard_isolation=false
```

After completion it shows `20 samples / 19 correct / accuracy 0.95` and one
`quarantined` Artifact. It exposes no scan, review, evidence, release,
download, or publish action. No local path, credential, private key, token,
patient identifier, raw data, or model weight is rendered.

