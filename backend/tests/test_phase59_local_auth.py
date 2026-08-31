from app.modules.identity.local_auth import _password_hash, session_digest, verify_password


def test_local_demo_password_uses_scrypt_and_never_stores_plaintext() -> None:
    password = "phase59-test-password"
    encoded = _password_hash(password)
    assert encoded.startswith("scrypt$")
    assert password not in encoded
    assert verify_password(password, encoded)
    assert not verify_password("wrong-password", encoded)


def test_local_demo_session_digest_is_one_way() -> None:
    secret = "opaque-session-secret"
    digest = session_digest(secret)
    assert digest.startswith("sha256:")
    assert secret not in digest
    assert digest == session_digest(secret)
