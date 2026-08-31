# Phase 5.13C Metadata Schema

The central bundle allowlist contains:

- local asset key, display metadata, asset kind, modality, source category;
- sensitivity classification and disclosure policy;
- immutable version, schema, metadata, quality, and bundle digests;
- count disclosure objects using `exact`, `range`, `suppressed`, `unknown`, or
  `not_applicable`;
- approved quality summary, de-identification status summary, limitations, and
  warning flags.

It excludes paths, locators, filenames, file lists, patient identifiers,
database connections, credentials, keys, internal addresses, and binary
content. Central records are metadata-only, not requestable, not materialized,
and not executable.
