# Data Exclusion Rules

The uploadable package is built with `git archive`. It must never include:

- `.env`, local override files, passwords, cookies, sessions, or tokens;
- private keys, private certificate material, browser profiles, or credentials;
- SQLite, `.db`, PostgreSQL volumes, MinIO volumes, Docker data, or backups;
- Phase 5.13 Connector/Executor state, Artifact bytes, or EvidenceBundle files;
- patient data, DICOM, WSI, H5AD, patient identifiers, or real hospital exports;
- `D:\MedTrustData`, Windows absolute local paths, downloads, or screenshots;
- `node_modules`, Python virtual environments, caches, or build output.

The verification script rejects forbidden names, extensions, content markers,
absolute Windows paths, private-key blocks, and untracked archive content.
Documentation may name forbidden categories, so content scans exclude this
rules document and other deployment guidance while still scanning executable
files, manifests, and configuration.
