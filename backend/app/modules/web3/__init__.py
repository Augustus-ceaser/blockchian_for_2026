"""Web3 authentication primitives."""

from app.modules.web3.siwe import (
    SiweMessage,
    SiweValidationError,
    build_siwe_message,
    generate_nonce,
    nonce_digest,
    parse_siwe_message,
    recover_eoa_address,
    validate_siwe_message,
    verify_siwe_eoa,
)

__all__ = [
    "SiweMessage",
    "SiweValidationError",
    "build_siwe_message",
    "generate_nonce",
    "nonce_digest",
    "parse_siwe_message",
    "recover_eoa_address",
    "validate_siwe_message",
    "verify_siwe_eoa",
]
