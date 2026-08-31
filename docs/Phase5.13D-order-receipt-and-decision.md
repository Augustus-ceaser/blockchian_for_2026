# Phase 5.13D Order Receipt and Decision

The Connector signs a receipt after automated validation and signs a separate
decision after local review. Both include `execution_started=false`.

The central platform independently verifies the Connector RSA signature and
payload digest against the active mTLS certificate. Decisions are append-only:
the operator cannot replace a local acceptance or rejection, and a second human
decision for the same order is rejected.

Revocation is supplemental evidence. For an already accepted order, the
Connector records `revoked_after_acceptance`; this does not erase the original
acceptance and does not imply that execution ever started.
