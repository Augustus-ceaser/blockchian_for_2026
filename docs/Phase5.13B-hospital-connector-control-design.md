# Phase 5.13B Hospital Connector Control Design

The central platform owns enrollment, registration review, Local Test CA
issuance metadata, control status and a dedicated connector audit chain. The
independent Connector owns its installation ID, RSA private key, CSR, client
certificate, SQLite state and local audit chain.

The Connector runs in Compose project `medtrust-hospital-connector-alpha`.
Its state is under `D:\MedTrustData\hospital-connector-alpha*`; it mounts
neither canonical PostgreSQL nor MinIO. Central ingress is nginx mTLS on
`127.0.0.1:18443`; local pages are `127.0.0.1:18600` and `18601`.

All alpha manifests are database-constrained to:

```text
execution_enabled=false
data_transfer_enabled=false
model_transfer_enabled=false
local_asset_registry_enabled=false
artifact_egress_enabled=false
hard_isolation=false
```

The legacy demo Connector remains unchanged for the fixed PathMNIST chain.

