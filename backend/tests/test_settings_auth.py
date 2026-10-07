from app.services.settings_auth_crypto import create_session, hash_password, verify_password, verify_session


def test_password_is_stored_as_scrypt_hash():
    encoded = hash_password("очень-длинный-пароль", salt=b"0123456789abcdef")
    assert "очень-длинный-пароль" not in encoded
    assert verify_password("очень-длинный-пароль", encoded)
    assert not verify_password("другой-пароль", encoded)


def test_admin_session_is_signed_and_expires():
    token = create_session("test-secret", now=100)
    assert verify_session(token, "test-secret", now=101)
    assert not verify_session(token, "another-secret", now=101)
    assert not verify_session(token, "test-secret", now=100 + 8 * 60 * 60 + 1)


def test_tampered_session_is_rejected():
    token = create_session("test-secret", now=100)
    assert not verify_session(token + "broken", "test-secret", now=101)
