# Phase 5.13E Next Task

Phase 5.13E-0 freezes architecture only. The next eligible stage is
Phase 5.13E-1: an inert Hospital Executor control skeleton.

Phase 5.13E-1 may add:

- a separate local Executor Manager identity;
- inert registration and health status;
- immutable manifest types and validators;
- a local execution-approval state machine;
- deny-by-default sandbox-policy validation;
- negative-test fixtures that do not read data or invoke a model.

Phase 5.13E-1 must not:

- execute PathMNIST, ResNet-18, or any model;
- read a LocalAsset projection;
- create a real LocalRun or Local Artifact;
- start an execution container;
- enable data/model transfer or Artifact egress;
- expose a central Executor endpoint;
- set `hard_isolation=true`;
- create or move a tag.

Do not start Phase 5.13E-1 until the Phase 5.13E-0 documentation commit is
reviewed and explicitly accepted.
