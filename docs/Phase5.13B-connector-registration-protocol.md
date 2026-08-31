# Phase 5.13B Registration Protocol

1. Operator creates a 15-minute, one-time token bound to organization and
   Connector name.
2. Only the token digest is persisted; plaintext is returned once.
3. Connector creates stable installation and instance IDs, RSA 3072 key and
   CSR locally.
4. Connector submits token, claims, CSR, nonce and timestamp.
5. Central validates token state, claims, time window, nonce, CSR signature
   and key strength.
6. Operator independently approves or rejects.
7. Approval signs a seven-day Local Test CA client certificate and activates
   only control-plane identity.

Hospital self-approval and requester/model-provider writes return HTTP 403.
Used, expired, mismatched and replayed enrollment evidence is rejected.

