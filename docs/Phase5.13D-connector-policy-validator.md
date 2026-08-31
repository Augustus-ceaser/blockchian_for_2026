# Phase 5.13D Connector Policy Validator

The Hospital Connector verifies, in its own SQLite-backed control domain:

- payload size and exact supported schemas;
- exact field sets and absence of sensitive/prohibited keys;
- Connector ID binding;
- active known Ed25519 key and fingerprint;
- policy and order digests and signatures;
- policy/order digest binding;
- control-only mode and action;
- execution and hard-isolation false boundaries;
- disjoint allowed/forbidden operations and supported output policy;
- validity window, nonce replay, and sequence monotonicity;
- locally approved asset version plus matching metadata and quality digests;
- metadata-only model reference format.

Any failed automatic check produces `validation_failed`. The local reviewer
cannot override that result. Valid orders enter `awaiting_local_review`.
