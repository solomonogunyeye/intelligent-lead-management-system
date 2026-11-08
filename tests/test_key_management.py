from unittest.mock import patch
from leadlift.key_management import (
    is_secure_backend,
    get_secure_backend,
    generate_db_key,
    get_db_key,
    InsecureKeyBackendError,
)


class WinVaultKeyring:
    pass


def test_generate_db_key_length():
    key = generate_db_key()
    assert isinstance(key, str)
    assert len(key) == 64


def test_is_secure_backend_rejects_null():
    class NullBackend:
        pass
    assert not is_secure_backend(NullBackend())


def test_get_secure_backend_fails_closed():
    class BadBackend:
        pass
    with patch("leadlift.key_management.keyring.get_keyring", return_value=BadBackend()):
        try:
            get_secure_backend()
            assert False
        except InsecureKeyBackendError:
            pass


def test_get_db_key_retrieves_existing():
    backend = WinVaultKeyring()
    backend.get_password = lambda service, name: "a" * 64
    with patch("leadlift.key_management.keyring.get_keyring", return_value=backend), \
         patch.object(backend, "set_password", create=True) as set_mock:
        assert get_db_key() == "a" * 64
        set_mock.assert_not_called()


def test_get_db_key_generates_and_stores():
    backend = WinVaultKeyring()
    backend.get_password = lambda service, name: None
    with patch("leadlift.key_management.keyring.get_keyring", return_value=backend), \
         patch.object(backend, "set_password", create=True) as set_mock:
        key = get_db_key()
        assert len(key) == 64
        set_mock.assert_called_once()

