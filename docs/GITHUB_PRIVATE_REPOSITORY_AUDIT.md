# Private GitHub Repository Pre-Push Audit

Audit date: 2026-08-04

## Decision

The tracked MedTrust Space repository is suitable for upload to a private
GitHub repository after the preparation commit containing this report. No
verified credential, private key, patient data file, database, model weight,
or deployment archive was found in reachable Git history.

Private repository access is still disclosure to every invited collaborator.
Only trusted collaborators should be invited, and runtime secrets must remain
outside Git.

## Scope And Evidence

- Reachable revisions: 106 across local branches, tags, and stash references.
- Gitleaks: official v8.30.1 Windows x64 binary, verified against the release
  SHA-256 checksum before use.
- Gitleaks Git-history scan: 104 content-bearing commits, about 7.48 MB of
  textual patch content, recursive decoding depth 3, archive depth 2.
- Manual high-confidence scan covered every local branch and tag for private
  key headers and common GitHub, AWS, and OpenAI token formats.
- Historical path review covered all filenames ever committed.
- Git object review covered 1,691 reachable blob records.
- `git fsck --full --strict` found no reachable object corruption. Unreachable
  objects left by normal local Git operations are not included in a normal
  branch/tag push.

The redacted machine-readable Gitleaks reports are stored outside the
repository under `D:\MedTrustData\audit-reports`.

## Findings

### Verified secrets

None.

### Scanner false positive

Gitleaks reported one `generic-api-key` candidate in commit `3cf321f`:

`backend/tests/integration/test_phase5114_metadata_publication_postgresql.py`

The matched value is the fixed test-only HTTP idempotency key
`phase5114-test-wrong-submitter`. It is not an authentication credential,
provider token, or runtime secret. No allowlist was added because preserving
the unmodified scanner result is more useful for later audits.

### Historical filenames

No committed path used a private-key, database, deployment archive, model
weight, or medical-image extension. The six security-related path matches
were limited to `.env.example`, credential tests/tools, and the production
secret-initialization script.

### Object sizes

- Blobs larger than 100 MB: 0.
- Blobs larger than 50 MB: 0.
- Largest reachable blob: 222,177 bytes.

GitHub's normal per-file limit is therefore not a blocker for this history.

### Operational metadata

The previous history contains the Tencent server's public IP and historical
local workstation paths. These values are operational metadata, not
credentials. The current `main` version replaces the active public-IP defaults
and local workstation paths with explicit parameters or placeholders.

The old values remain recoverable from Git history. History was deliberately
not rewritten because doing so would change every later commit identifier and
invalidate 14 annotated engineering freeze tags. Collaborators must therefore
be treated as authorized to know historical deployment metadata.

## Local-Only Material

Ignored local directories and environment files contain runtime dependencies,
test key material, and local credentials. They produced findings in a raw
working-directory scan, but none are tracked. The following safeguards were
verified:

- `.runtime/`, `.cache/`, `backend/.venv/`, and `frontend/node_modules/` are
  ignored.
- Local deployment and connector environment files are ignored.
- `backend/.env` and `backend/.env.local` are ignored.
- No ignored file is also tracked (`git ls-files -ci --exclude-standard`
  returned no path).
- Deployment packages and offline image archives live outside the repository.
- The Tencent SSH private key lives outside the repository.

Never use `git add -f` on an ignored runtime, credential, database, evidence,
or deployment path.

## Branch Preparation

- Original `main`: `0689f80` (Phase 5.9 documentation baseline).
- Accepted engineering baseline: `749a0fd`, tagged
  `v0.14-hospital-controlled-execution-alpha`.
- Pre-ICP deployment branch: `b0b5921`, seven commits after the accepted
  baseline.
- `main` was a strict ancestor of the deployment branch and was advanced with
  `git merge --ff-only`; no merge commit and no history rewrite were used.
- The deployment branch remains fixed at `b0b5921`. Repository-specific
  collaboration cleanup is committed only on `main`.

## Required GitHub Controls

1. Create an empty private repository; do not initialize it with a README,
   license, or `.gitignore`.
2. Require two-factor authentication for every collaborator where the account
   or organization settings support it.
3. Protect `main`: require pull requests, at least one approval, resolved
   conversations, and passing checks before merge.
4. Disable direct force-push and branch deletion on `main`.
5. Enable secret scanning and push protection when available for the selected
   GitHub plan.
6. Invite collaborators with the lowest practical repository role.
7. Push branches and annotated tags only after verifying the new remote URL.

## Upload Boundary

A normal upload should include branches and tags:

```powershell
git push -u origin main
git push origin --all
git push origin --tags
```

Do not push `refs/stash`, replacement refs, local backup refs, deployment
archives, or ignored runtime state.
