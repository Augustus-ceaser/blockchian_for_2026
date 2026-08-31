# Phase 5.13E-2A Runtime Admission Binding

## Bound evidence

Each prepared Runtime binds the following local facts:

```text
Executor identity
approved admission
security profile
approved execution image
exact image digest
bounded resource policy
sandbox identity
idempotency digest
```

The binding is evaluated again during preparation. A historical approval is
not enough if the current image is revoked or another required object is no
longer valid.

## Image reuse correction

E-2A found that the local image manifest table treated `image_digest` as
globally unique. That prevented two Executors from independently admitting the
same approved immutable image. Migration `phase5.13E_0005` removes only that
global uniqueness constraint while preserving manifest rows and per-Executor
admission binding.

This does not permit tags, mutable references, runtime pulls, or unapproved
digests.

## Non-authorization statement

`prepared` means the immutable control evidence and empty workspace were
assembled. It does not mean:

- a hospital authorized execution;
- a model or dataset was available;
- a task started or succeeded;
- output is safe to release;
- central control can trigger local execution.

The phase ends at `prepared / Not Executed`.
