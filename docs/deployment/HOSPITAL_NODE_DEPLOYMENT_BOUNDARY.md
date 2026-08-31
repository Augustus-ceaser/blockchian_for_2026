# Hospital Node Deployment Boundary

The Tencent Guangzhou Public Alpha runs only the central platform.

Hospital Connector and Executor must be deployed separately inside an
environment approved by the participating hospital. They are not default
services in the public Compose files and must not be simulated as a real
hospital production node on this server.

The intended boundary is:

- the Hospital Connector initiates an authenticated mTLS relationship with
  the central platform;
- the hospital independently validates and approves central instructions;
- the Hospital Executor accesses hospital-local assets only inside the
  hospital-approved environment;
- central services cannot read raw hospital data, local paths, patient-level
  data, model weights, or quarantined Artifact bytes;
- only reviewed, signed safe evidence summaries may be registered centrally.

The public environment uses Synthetic/Public data only. Even a no-data
hospital proof of concept requires hospital authorization, security review,
and deployment approval.

`hard_isolation=false` remains explicit. This architecture is an Engineering
Alpha and does not constitute hospital production acceptance, legal advice,
clinical approval, or compliance certification.
