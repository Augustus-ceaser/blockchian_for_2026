from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DEMO_USERNAMES = {
    "hospital.demo",
    "model.demo",
    "requester.demo",
    "operator.demo",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env", BACKEND_ROOT / ".env.local"),
        env_prefix="MEDTRUST_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "MedTrust Space API"
    app_version: str = "0.1.0"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    deployment_mode: Literal[
        "local",
        "lan-roadshow",
        "remote-preview",
        "production-template",
        "pre-icp",
        "public-alpha",
    ] = "local"
    public_origin: str = ""
    trusted_origins: str = ""
    allowed_hosts: str = "127.0.0.1,localhost,testserver"
    trusted_proxy_ips: str = "*"
    gateway_port: int = 8080
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    demo_api_enabled: bool = False
    enable_demo_role_switch: bool = False
    local_demo_password: str = ""
    demo_hospital_password: str = ""
    demo_model_password: str = ""
    demo_requester_password: str = ""
    demo_operator_password: str = ""
    demo_catalog_curator_password: str = ""
    allow_weak_local_demo_credentials: bool = False
    demo_hospital_password_file: Path | None = None
    demo_model_password_file: Path | None = None
    demo_requester_password_file: Path | None = None
    demo_operator_password_file: Path | None = None
    demo_catalog_curator_password_file: Path | None = None
    session_lifetime_seconds: int = 12 * 60 * 60
    password_min_length: int = 12
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 300
    role_assistant_provider: Literal["openai", "deepseek"] = "openai"
    role_assistant_openai_enabled: bool = False
    role_assistant_openai_api_key: SecretStr = SecretStr("")
    role_assistant_openai_api_key_file: Path | None = None
    role_assistant_openai_model: str = "gpt-4.1-mini"
    role_assistant_openai_base_url: str = "https://api.openai.com/v1"
    role_assistant_openai_timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)
    role_assistant_runtime: Literal["legacy", "pydantic_ai"] = "pydantic_ai"
    role_assistant_request_limit: int = Field(default=3, ge=1, le=6)
    role_assistant_tool_call_limit: int = Field(default=4, ge=1, le=8)
    role_assistant_state_enabled: bool = True
    role_assistant_semantic_search_enabled: bool = False

    web3_enabled: bool = False
    web3_chain_id: int = 31337
    web3_rpc_url: str = "http://127.0.0.1:8545"
    web3_required_confirmations: int = 1
    web3_siwe_domain: str = "127.0.0.1:5173"
    web3_siwe_uri: str = "http://127.0.0.1:5173"
    web3_challenge_ttl_seconds: int = 300
    web3_space_scope_digest: str = ""
    web3_role_credential_address: str = ""
    web3_agreement_registry_address: str = ""
    web3_escrow_address: str = ""
    web3_settlement_token_address: str = ""
    web3_credential_issuer_address: str = ""
    web3_execution_attestor_address: str = ""
    web3_delivery_attestor_address: str = ""
    web3_settlement_token_decimals: int = 6
    web3_escrow_refund_seconds: int = 24 * 60 * 60
    web3_rpc_timeout_seconds: float = 5.0
    web3_security_review_digest: str = ""

    database_url: str = (
        "postgresql+asyncpg://medtrust:medtrust_dev_only@127.0.0.1:5432/medtrust"
    )
    database_url_file: Path | None = None
    database_echo: bool = False

    outbox_publisher: Literal["unavailable", "in_memory", "database_inbox"] = "unavailable"
    outbox_batch_size: int = 50
    outbox_poll_interval: float = 1.0
    outbox_lease_seconds: int = 60
    outbox_max_attempts: int = 10
    outbox_shutdown_timeout: float = 30.0

    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "medtrust"
    minio_secret_key: str = "medtrust_dev_only"
    minio_access_key_file: Path | None = None
    minio_secret_key_file: Path | None = None
    minio_secure: bool = False
    minio_release_bucket: str = "medtrust-approved-results"
    minio_quarantine_bucket: str = "medtrust-quarantined-results"
    storage_root: Path = Path("D:/MedTrustData")
    cache_root: Path = Path("D:/MedTrustCache")
    external_catalog_base_url: str = ""
    external_model_catalog_base_url: str = ""
    allow_insecure_local_catalog: bool = False
    external_catalog_timeout_seconds: float = 30.0
    external_catalog_max_response_bytes: int = 50 * 1024 * 1024
    fixed_reference_authorization_ttl_seconds: int = 3600
    fixed_reference_authorization_safety_margin_seconds: int = 300

    @staticmethod
    def _read_secret(path: Path, *, field_name: str) -> str:
        try:
            if not path.is_absolute():
                raise ValueError(f"{field_name} must be an absolute path")
            if path.stat().st_size > 4096:
                raise ValueError(f"{field_name} exceeds the 4096-byte limit")
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"{field_name} cannot be read") from exc
        if not value:
            raise ValueError(f"{field_name} is empty")
        return value

    @model_validator(mode="after")
    def load_secret_files(self) -> "Settings":
        mappings = {
            "database_url": self.database_url_file,
            "minio_access_key": self.minio_access_key_file,
            "minio_secret_key": self.minio_secret_key_file,
            "demo_hospital_password": self.demo_hospital_password_file,
            "demo_model_password": self.demo_model_password_file,
            "demo_requester_password": self.demo_requester_password_file,
            "demo_operator_password": self.demo_operator_password_file,
            "demo_catalog_curator_password": self.demo_catalog_curator_password_file,
        }
        for field_name, path in mappings.items():
            if path is not None:
                setattr(
                    self,
                    field_name,
                    self._read_secret(path, field_name=f"{field_name}_file"),
                )
        if self.role_assistant_openai_api_key_file is not None:
            self.role_assistant_openai_api_key = SecretStr(
                self._read_secret(
                    self.role_assistant_openai_api_key_file,
                    field_name="role_assistant_openai_api_key_file",
                )
            )
        return self

    @model_validator(mode="after")
    def validate_role_assistant_openai(self) -> "Settings":
        model = self.role_assistant_openai_model.strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", model):
            raise ValueError("role_assistant_openai_model is invalid")
        self.role_assistant_openai_model = model

        base_url = self.role_assistant_openai_base_url.strip().rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(
                "role_assistant_openai_base_url must be an absolute HTTP(S) URL"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "role_assistant_openai_base_url must not include credentials, query, or fragment"
            )
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("role_assistant_openai_base_url has an invalid port") from exc
        if parsed.scheme == "http" and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError(
                "role_assistant_openai_base_url requires HTTPS or loopback HTTP"
            )
        if self.role_assistant_openai_enabled and self.role_assistant_provider == "deepseek":
            if parsed.scheme != "https" or parsed.hostname != "api.deepseek.com":
                raise ValueError(
                    "DeepSeek role assistant requires https://api.deepseek.com"
                )
            if parsed.path not in {"", "/"}:
                raise ValueError("DeepSeek Responses base URL must not include a path")
            if not model.startswith("deepseek-v4-"):
                raise ValueError(
                    "DeepSeek Responses requires a supported deepseek-v4 model"
                )
        self.role_assistant_openai_base_url = base_url
        return self

    @model_validator(mode="after")
    def reject_weak_demo_credentials_outside_local_modes(self) -> "Settings":
        weak_usernames = {
            username
            for username, password in self.demo_passwords.items()
            if password and (len(password) < 12 or password == username)
        }
        if self.allow_weak_local_demo_credentials and self.deployment_mode != "local":
            raise ValueError(
                "Weak demo credentials may be enabled only for the local deployment mode."
            )
        if weak_usernames:
            weak_local_public_only = (
                self.allow_weak_local_demo_credentials
                and self.deployment_mode == "local"
                and weak_usernames <= PUBLIC_DEMO_USERNAMES
                and all(
                    len(self.demo_passwords[username]) >= 3
                    for username in weak_usernames
                )
            )
            if not weak_local_public_only:
                raise ValueError(
                    "Weak demo credentials require the explicit local-only allowance "
                    "and are limited to the four public demo accounts."
                )
        too_short = {
            username
            for username, password in self.demo_passwords.items()
            if password and len(password) < self.password_min_length
        }
        if too_short:
            raise ValueError(
                "Configured demo passwords must satisfy password_min_length."
            )
        return self

    @model_validator(mode="after")
    def validate_public_deployment(self) -> "Settings":
        if not 900 <= self.session_lifetime_seconds <= 86400:
            raise ValueError("session_lifetime_seconds must be between 900 and 86400")
        minimum_password_length = (
            3
            if self.allow_weak_local_demo_credentials
            and self.deployment_mode == "local"
            else 12
        )
        if not minimum_password_length <= self.password_min_length <= 128:
            raise ValueError(
                f"password_min_length must be between {minimum_password_length} and 128"
            )
        if not 3 <= self.login_rate_limit_attempts <= 20:
            raise ValueError("login_rate_limit_attempts must be between 3 and 20")
        if not 60 <= self.login_rate_limit_window_seconds <= 3600:
            raise ValueError(
                "login_rate_limit_window_seconds must be between 60 and 3600"
            )
        if self.deployment_mode not in {"pre-icp", "public-alpha"}:
            return self
        parsed = urlsplit(self.public_origin)
        if not parsed.scheme or not parsed.hostname:
            raise ValueError("public_origin is required for deployed modes")
        if self.deployment_mode == "pre-icp":
            if parsed.scheme != "http" or parsed.hostname not in {
                "127.0.0.1",
                "localhost",
            }:
                raise ValueError("pre-icp public_origin must be loopback HTTP")
        elif parsed.scheme != "https":
            raise ValueError("public-alpha public_origin must use HTTPS")
        return self

    @model_validator(mode="after")
    def validate_external_catalog_transport(self) -> "Settings":
        for value in (self.external_catalog_base_url, self.external_model_catalog_base_url):
            if not value:
                continue
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("External catalog URL must be an absolute HTTP(S) URL.")
            if parsed.scheme == "http":
                local_hosts = {"127.0.0.1", "localhost", "host.docker.internal"}
                local_mode = self.deployment_mode in {"local", "lan-roadshow"}
                if (
                    not local_mode
                    or not self.allow_insecure_local_catalog
                    or parsed.hostname not in local_hosts
                ):
                    raise ValueError(
                        "HTTP external catalogs require an explicit local-only allowance."
                    )
        return self

    @model_validator(mode="after")
    def validate_web3_configuration(self) -> "Settings":
        if not 1 <= self.web3_required_confirmations <= 64:
            raise ValueError("web3_required_confirmations must be between 1 and 64")
        if not 60 <= self.web3_challenge_ttl_seconds <= 600:
            raise ValueError("web3_challenge_ttl_seconds must be between 60 and 600")
        if self.web3_chain_id <= 0:
            raise ValueError("web3_chain_id must be positive")
        if not 2 <= self.web3_settlement_token_decimals <= 18:
            raise ValueError("web3_settlement_token_decimals must be between 2 and 18")
        if not 600 <= self.web3_escrow_refund_seconds <= 30 * 24 * 60 * 60:
            raise ValueError(
                "web3_escrow_refund_seconds must be between 10 minutes and 30 days"
            )
        if not 0.5 <= self.web3_rpc_timeout_seconds <= 30:
            raise ValueError("web3_rpc_timeout_seconds must be between 0.5 and 30")
        if not self.web3_enabled:
            return self

        parsed_rpc = urlsplit(self.web3_rpc_url.strip())
        if parsed_rpc.scheme not in {"http", "https"} or not parsed_rpc.hostname:
            raise ValueError("web3_rpc_url must be an absolute HTTP(S) URL")
        loopback_hosts = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}
        if parsed_rpc.scheme == "http" and (
            parsed_rpc.hostname not in loopback_hosts
            or self.deployment_mode not in {"local", "lan-roadshow"}
        ):
            raise ValueError("Web3 HTTP RPC is allowed only for a local demo endpoint")

        siwe_uri = urlsplit(self.web3_siwe_uri.strip())
        if (
            siwe_uri.scheme not in {"http", "https"}
            or not siwe_uri.hostname
            or siwe_uri.netloc != self.web3_siwe_domain.strip()
        ):
            raise ValueError("SIWE domain must exactly match the configured SIWE URI authority")
        if siwe_uri.scheme == "http" and siwe_uri.hostname not in loopback_hosts:
            raise ValueError("SIWE requires HTTPS except on a loopback demo origin")

        space_scope_digest = self.web3_space_scope_digest.strip()
        if (
            re.fullmatch(r"0x[0-9a-fA-F]{64}", space_scope_digest) is None
            or int(space_scope_digest, 16) == 0
        ):
            raise ValueError("web3_space_scope_digest must be a non-zero bytes32")
        self.web3_space_scope_digest = space_scope_digest.lower()

        address_pattern = re.compile(r"0x[0-9a-fA-F]{40}")
        address_fields = (
            "web3_role_credential_address",
            "web3_agreement_registry_address",
            "web3_escrow_address",
            "web3_settlement_token_address",
            "web3_credential_issuer_address",
            "web3_execution_attestor_address",
            "web3_delivery_attestor_address",
        )
        for field_name in address_fields:
            value = getattr(self, field_name).strip()
            if address_pattern.fullmatch(value) is None:
                raise ValueError(f"{field_name} must be a deployed EVM address")
            setattr(self, field_name, value.lower())

        if self.deployment_mode not in {"local", "lan-roadshow"}:
            if not re.fullmatch(
                r"sha256:[0-9a-f]{64}", self.web3_security_review_digest.strip()
            ):
                raise ValueError(
                    "deployed Web3 mode requires an independent security-review digest"
                )
            if self.web3_chain_id == 31337:
                raise ValueError("the local Hardhat chain is forbidden outside demo modes")
        return self

    @property
    def demo_passwords(self) -> dict[str, str]:
        configured = {
            "hospital.demo": self.demo_hospital_password,
            "model.demo": self.demo_model_password,
            "requester.demo": self.demo_requester_password,
            "operator.demo": self.demo_operator_password,
            "catalog.curator.demo": self.demo_catalog_curator_password,
        }
        return {
            username: password or self.local_demo_password
            for username, password in configured.items()
        }

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [
            value.strip().rstrip("/")
            for value in self.cors_origins.split(",")
            if value.strip()
        ]
        if self.public_origin:
            origins.append(self.public_origin.strip().rstrip("/"))
        return list(dict.fromkeys(origins))

    @property
    def trusted_origin_list(self) -> list[str]:
        values = self.trusted_origins or self.cors_origins
        origins = [value.strip().rstrip("/") for value in values.split(",") if value.strip()]
        if self.public_origin:
            origins.append(self.public_origin.strip().rstrip("/"))
        return list(dict.fromkeys(origins))

    @property
    def cookie_secure(self) -> bool:
        return self.deployment_mode in {
            "remote-preview",
            "production-template",
            "public-alpha",
        }

    @property
    def allowed_host_list(self) -> list[str]:
        hosts = [
            value.strip() for value in self.allowed_hosts.split(",") if value.strip()
        ]
        if self.public_host:
            hosts.append(self.public_host)
        return list(dict.fromkeys(hosts))

    @property
    def docs_policy(self) -> Literal["public", "operator", "disabled"]:
        if self.deployment_mode == "local":
            return "public"
        if self.deployment_mode == "lan-roadshow":
            return "operator"
        return "disabled"

    @property
    def public_host(self) -> str:
        if not self.public_origin:
            return ""
        return urlsplit(self.public_origin).hostname or ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
