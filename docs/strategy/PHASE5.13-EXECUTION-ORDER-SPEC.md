# Phase 5.13 ExecutionOrder 规范

ExecutionOrder 是一次执行请求，必须引用一个有效 PolicyBundle，不能复制后放宽政策。若订单字段与政策冲突，以更严格规则为准，否则拒绝。

## 字段

```json
{
  "order_id": "ord_...",
  "policy_bundle_id": "pol_...",
  "policy_bundle_digest": "sha256:...",
  "connector_id": "con_...",
  "local_asset_version": {"id": "lav_...", "digest": "sha256:..."},
  "model_version": {"id": "mpv_...", "digest": "sha256:..."},
  "execution_image_digest": "sha256:...",
  "input_manifest_digest": "sha256:...",
  "expected_output_schema": {"id": "schema_...", "digest": "sha256:..."},
  "correlation_id": "cor_...",
  "idempotency_key": "...",
  "issued_at": "...",
  "expires_at": "...",
  "nonce": "...",
  "signer": {"key_id": "key_...", "algorithm": "Ed25519"},
  "signature": "base64url..."
}
```

订单使用与 PolicyBundle 相同的 canonical serialization、摘要和签名规则。Connector 必须先验证政策，再验证订单；所有资产、模型、镜像、输入和输出 schema 均使用不可变摘要。

## 接受状态

`accepted`、`rejected`、`expired`、`revoked`、`digest_mismatch`、`resource_unavailable`、`local_approval_required`、`security_posture_failed`、`duplicate_replay`、`unsupported_capability`。

同一 idempotency key、相同摘要返回原决定；相同 key、不同摘要返回冲突。accepted 只表示进入本地任务队列，不表示运行成功或允许出域。每次决定生成 LocalTaskDecision 和审计事件。

