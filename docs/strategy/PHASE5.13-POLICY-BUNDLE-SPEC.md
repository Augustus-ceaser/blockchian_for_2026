# Phase 5.13 PolicyBundle 规范

## 目的

PolicyBundle 是中央治理事实编译出的、可签名、可版本化、可由 Connector 独立验证的最小政策单元。它不能替代医院本地政策，Connector 可附加更严格限制或拒绝。

## 核心结构

```json
{
  "bundle_version": "1.0",
  "policy_id": "pol_...",
  "connector_id": "con_...",
  "organization_id": "org_...",
  "application": {"id": "app_...", "digest": "sha256:..."},
  "contract": {"id": "ctr_...", "digest": "sha256:..."},
  "study_protocol": {"id": "pro_...", "version": "3", "digest": "sha256:..."},
  "data_product_version": {"id": "dpv_...", "digest": "sha256:..."},
  "local_asset_version": {"id": "lav_...", "digest": "sha256:..."},
  "model_product_version": {"id": "mpv_...", "digest": "sha256:..."},
  "subject": {"organization_id": "org_...", "principal_id": "usr_..."},
  "purpose": {"code": "research_validation", "text": "..."},
  "allowed_operations": ["fixed_inference"],
  "prohibited_operations": ["raw_export", "runtime_network", "dynamic_code"],
  "data_scope": {"cohort_digest": "sha256:...", "sample_range": {"max": 1000}},
  "usage": {"max_runs": 1},
  "validity": {"not_before": "...", "expires_at": "..."},
  "network_policy": {"mode": "deny_all"},
  "filesystem_policy": {"input_read_only": true, "allowed_outputs": ["metrics.json"]},
  "resources": {"cpu": 4, "ram_mb": 8192, "gpu": 0, "disk_mb": 4096, "timeout_seconds": 3600},
  "output_policy": {"small_cell_threshold": 10, "artifact_retention_days": 30},
  "review_roles": ["hospital_data_owner", "hospital_egress_reviewer"],
  "revocation_policy": {"check_before_start": true, "check_before_egress": true},
  "nonce": "...",
  "issued_at": "...",
  "expires_at": "...",
  "signer": {"key_id": "key_...", "algorithm": "Ed25519"},
  "digest_algorithm": "SHA-256",
  "bundle_digest": "sha256:...",
  "signature": "base64url..."
}
```

## 编码、摘要与签名

- JSON Schema 使用封闭对象，未知关键字段拒绝；版本升级遵循显式兼容规则。
- Canonical serialization 使用 RFC 8785 JCS；时间为 UTC RFC 3339；ID 和枚举区分大小写。
- `bundle_digest` 对移除 `signature` 和 `bundle_digest` 后的 canonical bytes 计算 SHA-256。
- 签名覆盖包含 `bundle_digest` 的 canonical bytes；`key_id` 必须解析到有效且未撤销的公钥。
- Connector 同时验证 schema、摘要、签名、证书链、用途、能力、本地审批和所有引用摘要。

## 生命周期

```text
draft -> issued -> active -> expired
                    |-> revoked
                    |-> superseded
```

issued 后政策内容不可变。supersede 创建新 ID/版本；撤销不能删除历史。nonce 在 Connector 范围内一次性消费；过期、撤销和摘要不一致均 fail closed。

## 稳定错误码

`POLICY_SCHEMA_INVALID`、`POLICY_SIGNATURE_INVALID`、`POLICY_DIGEST_MISMATCH`、`POLICY_EXPIRED`、`POLICY_REVOKED`、`POLICY_REPLAYED`、`POLICY_REFERENCE_MISMATCH`、`LOCAL_APPROVAL_REQUIRED`、`CAPABILITY_UNSUPPORTED`、`SECURITY_POSTURE_FAILED`。

