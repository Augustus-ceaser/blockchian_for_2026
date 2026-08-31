# Phase 5.13B Connector PKI Boundary

This is a Local Test CA only. It is non-production, loopback-only, not hospital
PKI, not enterprise identity proof and not a regulatory certificate.

CA private material exists only below
`D:\MedTrustData\hospital-connector-alpha\pki` and is ignored by Git. Connector
private keys exist only in each Connector's D-drive bind mount. The central
database stores certificate PEM and metadata, never a private key.

Certificate rotation creates a new RSA 3072 key and CSR locally. The current
mTLS certificate authenticates the rotation request. On success the old
certificate becomes `superseded`; revocation sets the current certificate to
`revoked`.

