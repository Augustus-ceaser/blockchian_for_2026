# Production Deployment Checklist

`compose.production.example.yml` is a review template, not a production-ready deployment.

Before any real deployment, a qualified team must provide:

- managed secrets and rotation;
- TLS termination, Access policy and origin protection;
- backup and restore tests;
- monitoring, alerting and incident response;
- hardened worker isolation and egress controls;
- vulnerability and dependency management;
- capacity, availability and disaster recovery design;
- privacy, legal, clinical and security review;
- removal of development defaults and demonstration identities.

Phase 5.10 does not satisfy these controls and does not claim clinical, hospital-production, privacy-computing or certification readiness.
