# LAN Four-Device Guide

Assign one independent browser profile or device to each portal:

- Hospital: `/portal/hospital`, account `hospital.demo`
- Model provider: `/portal/model-provider`, account `model.demo`
- Requester: `/portal/requester`, account `requester.demo`
- Operator: `/portal/operator`, account `operator.demo`

Passwords are stored only in the ignored local credential configuration. The `/join` page and QR codes never include credentials.

Place the operator screen where the presenter can monitor lifecycle requests and next responsibility. Keep requester download actions on the requester device. If only two physical devices are available, use two isolated browser profiles on each and report the real physical-device count.

Fallback: stop LAN mode and use the existing four local browser profiles. Do not replace independent sessions with the debug role switch.
