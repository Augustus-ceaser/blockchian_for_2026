# Phase 5.13E-2C-R1-EXEC Browser Evidence Index

Evidence root:
`D:\MedTrustData\phase5.13E-2C-R1-EXEC\browser-evidence`.
Screenshots are retained outside Git.

| Range | Evidence |
|---|---|
| `01` | expired Snapshot rejection |
| `02`-`04` | fresh Status v2 and central source |
| `05`-`07` | fresh Readiness, Policy, and Order |
| `08`-`10` | mTLS pull, 44/44 validation, local review, Snapshot |
| `11`-`14` | prebindings, consumption, result, quarantine, replay |
| `15`-`22` | central consumption/audit and six isolated role sessions |
| `23`-`28` | central/local pages at 390, 768, and 1920 widths |

Final browser matrix:

```text
role/page combinations = 14
390x844 = passed
768x1024 = passed
1366x768 = passed
1920x1080 = passed
page overflow = 0
post-auth Console errors = 0
unexpected failed requests = 0
external requests = 0
sensitive exposure = 0
```

The initial anonymous `/auth/me` 401 belongs to login bootstrap and was
excluded before the authenticated observation window. The old Snapshot 409 is
the required denial evidence, not an unexpected failed request.

Full SHA-256 inventory is retained at
`D:\MedTrustData\phase5.13E-2C-R1-EXEC\reports\browser-hashes.md`.

