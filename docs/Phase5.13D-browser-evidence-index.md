# Phase 5.13D Browser Evidence Index

Evidence root: `D:\MedTrustData\phase5.13D-browser-evidence`.
Screenshots are intentionally excluded from Git.

| File | Route | Role | Viewport | Purpose | SHA-256 | Sensitive review | Result |
|---|---|---|---:|---|---|---|---|
| central-390x844.png | `/portal/operator/policy-control` | operator | 390x844 | signed policy detail | f1ed0189f5a3c7982cb2484e338028f770d93fdf38407c389eec5aaae34b0ce1 | clear | pass |
| connector-390x844.png | `/local/orders/{id}` | local policy reviewer | 390x844 | accepted/not executed | b2a8bd27cd322cf3b9a6a776b838fd69accf2e98d76fe404097c28a71dba6e65 | clear | pass |
| central-768x1024.png | central policy detail | operator | 768x1024 | responsive policy | 2c8478d437c65d6abe70935076fc15c77d1ea4921f46f8283ecb82da3e846ae1 | clear | pass |
| connector-768x1024.png | local order detail | local policy reviewer | 768x1024 | validation list | 803b5d39c8e6c7905b1c1a5b1459c0ae11f760c6163f9c985d721f85457f6437 | clear | pass |
| central-1366x768.png | central policy detail | operator | 1366x768 | desktop policy | 8ae4ea176124f7207149a3fd0f306da94dc7100610a53988df7e09a38fcddafe | clear | pass |
| connector-1366x768.png | local order detail | local policy reviewer | 1366x768 | desktop validation | 47d21e542a756fc3543c580150dab3c801d6a3704d614bdf0bda40e0478d4ddb | clear | pass |
| central-1920x1080.png | central policy detail | operator | 1920x1080 | wide policy | d5943de248f91bdb14dd72bf260acd694e9baf99b6589162716922ed93046924 | clear | pass |
| connector-1920x1080.png | local order detail | local policy reviewer | 1920x1080 | wide validation | 216746ac58ca79a46105b111bcf27e2c5538b7373b5ac3e84490c6f3d4d6e66b | clear | pass |
| 01-policy-reviewer-home.png | `/local` | local policy reviewer | 1366x768 | independent login | 2a53b144d23b8c0f31682074184a8daf7ed37d6a9f1444755cb5ee7e0eda098f | clear | pass |
| 02-policy-order-pulled.png | `/local/orders` | local policy reviewer | 1366x768 | mTLS pull | 7ae7913d3e352ea5beedfe7053a8d12fde54eb705ac08fc2b4ac1c62cfc22e2c | clear | pass |
| 04-registration-approved.png | operator Connector page | operator | 1366x768 | formal registration | 579405a0a9b8e8b43172d044f296ca0ecc52ae3aa4f391fdaf58728a1af79fbd | clear | pass |
| 05-control-order-issued.png | operator policy page | operator | 1366x768 | signed order issue | 5629f3969136eb8e6c1e4f0f2a93afe4eec88b1b8004f830e64bc9e0c0e8d157 | clear | pass |
| 06-policy-validation-passed.png | local order detail | local policy reviewer | 1366x768 | automated checks | 4eb6e0929699a816dffaa526d11aaef6002c80bcf0e298bae5300c352e4de2dd | clear | pass |
| 07-policy-locally-accepted.png | local order detail | local policy reviewer | 1366x768 | signed accept | 47d21e542a756fc3543c580150dab3c801d6a3704d614bdf0bda40e0478d4ddb | clear | pass |
| 08-policy-locally-rejected.png | local order detail | local policy reviewer | 1366x768 | manual reject | 4ef927ca6435508a1131a8cc294000c18ee561b5f3ba8d095658918a97802330 | clear | pass |
| 09-central-policy-revoked.png | operator policy page | operator | 1366x768 | revocation | 7c2ed87a1b17a07960ef618176075b1f4b6bd7dcbe2029b38b9c60b8eb4f1436 | clear | pass |
| 10-central-audit.png | `/audit` | operator | 1366x768 | central audit | e5cc8f424b6d25fcd3255fcdf2807db12c1c2ea21779e1d254d42239a2425c80 | clear | pass |
| 11-local-audit.png | `/local/audit` | local policy reviewer | 1366x768 | local audit | 2534d2bc6c0a1039575c497e1dd4e7a70ab81972052971bc4802e2123d53c468 | clear | pass |

All eight checks reported overflow 0, Console errors 0, unexpected failed
requests 0, external requests 0, sensitive path/patient/private-key exposure 0,
and execution buttons 0.
