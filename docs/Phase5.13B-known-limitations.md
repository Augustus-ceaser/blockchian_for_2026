# Phase 5.13B Known Limitations

- Local Test CA only; no hospital PKI integration or certificate revocation
  distribution infrastructure.
- Loopback-only engineering alpha; no LAN, Internet, Tunnel or firewall change.
- SQLite local state is not an HSM, TPM or production secret store.
- mTLS ingress is a control skeleton, not a security certification.
- No PACS/HIS/LIS/EMR access and no real patient data.
- No Local Asset Registry, data transfer, model transfer, Executor,
  ExecutionOrder, Artifact or EvidenceBundle.
- No automated offline scheduler; offline status is represented but heartbeat
  timeout automation remains future work.
- `hard_isolation=false`; maturity remains L1.
- Existing global Alembic autogenerate drift remains known technical debt.

Phase 5.13C may add metadata-only Local Asset Registry objects. Phase 5.13D may
add signed policy/order refusal. Phase 5.13E may add controlled local execution
and EvidenceBundle, each behind separate acceptance.

