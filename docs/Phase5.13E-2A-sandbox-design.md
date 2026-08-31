# Phase 5.13E-2A Sandbox Design

## Layout

The Connector creates a server-generated workspace below its configured
D-drive data root:

```text
runtime-sandboxes/
  sbx-<uuid>/
    input/
    runtime/
    output/
    logs/
```

All four directories are empty at preparation. The caller cannot select the
sandbox identifier or provide an absolute path.

## E-2A rules

- exactly `input`, `runtime`, `output`, and `logs` are created;
- no file, model, dataset, script, credential, package, or token is copied in;
- no input is mounted or read;
- no output or log is generated;
- no network or container runtime is invoked;
- database state stores `sandbox/<id>`, not the host path;
- destruction is limited to a verified child of the configured sandbox root.

The accepted browser flow produced one prepared workspace whose four
directories contained zero input and zero output files.

## Later-phase requirements

E-2A does not prove read-only input mounts, read-only runtime mounts, bounded
scratch space, output quarantine, no-network execution, non-root execution, or
resource enforcement. Those controls require a separately authorized execution
phase and attack testing before any task may start.
