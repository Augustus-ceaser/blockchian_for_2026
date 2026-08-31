# Phase 5.13C Known Limitations

- Public/synthetic metadata fixtures only; no PACS, HIS, LIS, EMR, or patient
  data integration.
- Local quality is a minimum summary, not Phase 5.14 governance evidence.
- De-identification is a recorded status boundary, not an automated guarantee.
- No model transfer, local execution, result egress, or hospital storage access.
- `hard_isolation=false`; local test CA and loopback runtime are not production.
- 原 5.13C 验收时仅完成桌面视觉复核；A1 已使用独立浏览器上下文补齐
  390×844、768×1024、1366×768、1920×1080 四档证据。
- Ten legacy PostgreSQL tests still hard-code migration `20260725_0032`.
- `alembic check` 仍报告既有 ORM/schema comparison drift；A1 没有新增中央
  migration，也没有将该历史技术债误报为通过。
