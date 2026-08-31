# Phase 5.13E-0 Controlled Execution Threat Model

## Method and rule

This model extends the Hospital Connector threat model to execution, output,
and evidence. Every row defines the protected asset, attacker/path, required
control, and acceptance test. A documented control is not an implemented
control; all rows block a production or `hard_isolation=true` claim.

| # | Asset and threat | Attacker / path | Frozen control | Acceptance evidence |
|---:|---|---|---|---|
| 1 | Task integrity: forged order | network or central insider | signed order, exact schema, local approval | forged signer and altered byte rejected |
| 2 | Policy integrity: bypass | central/Connector defect | policy/order/approval digest binding | missing or mismatched binding rejected |
| 3 | Local authority: central override | central operator | independent append-only hospital decision | override and self-review rejected |
| 4 | Replay: reused launch | network/host attacker | nonce, sequence, expiry, idempotency ledger | exact replay stable; changed replay rejected |
| 5 | Revocation race | stale/offline node | short validity, deny cache, pre-launch recheck | revoked queued launch never starts |
| 6 | Malicious image | image supplier | digest, signature, provenance, SBOM, scan | unknown/altered/revoked image rejected |
| 7 | Dependency pollution | build/runtime attacker | pinned offline build; no runtime install | package-manager and network install fail |
| 8 | Malicious model code | model supplier | fixed format/loader; no remote code | script/plugin/`trust_remote_code` rejected |
| 9 | Arbitrary user code | requester | fixed task schema and entrypoint ID | shell/notebook/source fields rejected |
| 10 | Network exfiltration | workload | `network_mode=none`, no DNS/proxy | IP, DNS, proxy, metadata probes fail |
| 11 | Local service attack | workload | no host/bridge network | Connector/PACS/HIS/LIS probes fail |
| 12 | Host filesystem read | workload | explicit mounts, read-only root | sensitive host path probes fail |
| 13 | Container socket attack | workload | no runtime socket/device mounts | socket and daemon API unavailable |
| 14 | Privilege escalation | workload | non-root, drop all caps, no-new-privileges | UID/capability/escalation tests fail |
| 15 | Path traversal | input/output name | rooted safe-open, no raw paths | absolute/drive/UNC/`..` rejected |
| 16 | Symlink/junction escape | local or archive attacker | reject links/reparse points | symlink, hardlink, junction tests fail |
| 17 | Archive escape/bomb | model/output supplier | full member validation and quotas | zip-slip and expansion bomb rejected |
| 18 | Input mutation | workload | read-only projection and digests | write/rename/delete and digest change fail |
| 19 | Unauthorized input | Connector/workload | exact LocalAssetVersion projection | alternate asset/version rejected |
| 20 | Resource exhaustion | workload | cgroup/job limits, quotas, timeout | CPU/RAM/process/disk/time tests terminate |
| 21 | Log exfiltration | workload | structured allowlist, byte/rate limits, DLP | canary path/secret absent from status |
| 22 | Output exfiltration | workload | output schema, quota, quarantine | undeclared file and oversized field rejected |
| 23 | Steganographic/active output | workload | file allowlist, magic scan, human review | executable/macro/polyglot rejected |
| 24 | Small-cell disclosure | valid analytics | suppression/threshold policy and review | low-count fixture cannot leave |
| 25 | Patient identifier leakage | workload/data | DLP and hospital review | identifier canaries block evidence |
| 26 | Artifact bypass | insider/service defect | no direct egress mount/API | created/quarantined cannot reach central |
| 27 | Scanner bypass | local insider | signed scan record and state guards | approval without required scan rejected |
| 28 | Reviewer impersonation | local insider | independent IAM/MFA target and audit | self-review and wrong role rejected |
| 29 | Evidence forgery | hospital/central attacker | canonical digest, dedicated signature | field/file/signature mutation rejected |
| 30 | Evidence replay | network/central defect | bundle ID/version/digest ledger | duplicate stable; conflicting duplicate rejected |
| 31 | Audit deletion/fork | local/central insider | append-only chain and signed head | deletion, reordering, fork detected |
| 32 | Residual workspace | crash/host restart | reconciliation and cleanup gate | kill/reboot test leaves no accessible scratch |
| 33 | Secret exposure | config/process/log | no runtime secrets; scoped local identities | env/proc/log scans find no control keys |
| 34 | Management mistake | operator | two-step approval, preview, deny defaults | wrong image/asset/order cannot be approved silently |

## Additional systemic risks

- Container isolation is not equivalent to hardware isolation.
- A compromised hospital host can undermine container and audit controls.
- Signature verification does not prove image or model safety.
- Aggregate outputs may still leak sensitive information.
- Human review is fallible and needs training, separation of duties, and
  measurable procedures.
- Availability pressure must never convert a deny into allow.

## Required red-team groups

Before any model run, test at least:

1. schema/signature/replay/revocation;
2. image/model/dependency supply chain;
3. network and DNS exfiltration;
4. host, socket, device, and privilege access;
5. path, link, archive, and mount escapes;
6. CPU, memory, process, disk, log, output, and time exhaustion;
7. identifier, secret, path, small-cell, and active-content leakage;
8. quarantine, reviewer, evidence, and audit bypass;
9. crash, restart, cleanup, and stale-state recovery.
