# Phase 5.12.6B-R Reference Run Evidence Design

## Purpose

This phase connects one completed historical PathMNIST and fixed ResNet-18
business chain to the append-only dataset-model evidence graph. It does not
execute a model, download an asset, or create a Job, Run, Artifact, package, or
download grant.

## Trust Gates

The internal command derives all values from canonical objects. It requires:

- a terminal succeeded Run and Job using the `local-builtin` adapter;
- exact data/model version IDs and snapshot digest matches;
- a completed executor callback with unchanged dataset and verified model;
- a quarantined Artifact whose digest matches the callback output;
- three required approved result reviews;
- one available three-file allowlisted package with no prohibited content;
- one exhausted one-time grant with usage 1/1;
- complete run/release audit events and a valid Space audit chain.

Clients cannot submit runtime metrics. Existing public APIs continue to reject
manual `executed`, `execution_failed`, and `verified` evidence.

## Storage

Native reference products have no external-catalog source links. Migration
`20260728_0049` therefore permits only the external-source lock fields to be
null. Exact product-version IDs and snapshot digests remain mandatory.
Existing six external relations retain every source and governance lock.

Evidence is append-only and database-protected. Deterministic IDs and
idempotency digests make replay a no-op.

## Scope Boundary

The result covers 20 fixed PathMNIST demonstration images and one fixed
ResNet-18 version. The observed aggregate accuracy is not a full-dataset,
clinical, or diagnostic performance claim. The historical executor records
`hard_isolation=false`.
