from __future__ import annotations

import secrets
from typing import Any

import keyring

KEY_SERVICE = "LEADLIFT_DB"
KEY_NAME = "database_encryption_key"
_INSECURE_BACKEND_MARKERS = ("Null", "Plaintext", "Fail", "Chainer", "Uncrypted")
_SECURE_BACKEND_MARKERS = ("WinVault", "Keyring", "SecretService", "KWallet", "Keychain", "MacOS")


class InsecureKeyBackendError(RuntimeError):
    """Raised when no secure key storage backend is available."""


def _backend_name(backend: Any) -> str:
    return type(backend).__name__


def is_secure_backend(backend: Any) -> bool:
    if backend is None:
        return False
    name = _backend_name(backend)
    if any(marker in name for marker in _INSECURE_BACKEND_MARKERS):
        return False
    return any(marker in name for marker in _SECURE_BACKEND_MARKERS)


def get_secure_backend():
    try:
        backend = keyring.get_keyring()
    except Exception as exc:
        raise InsecureKeyBackendError("Secure key storage is unavailable") from exc
    if not is_secure_backend(backend):
        raise InsecureKeyBackendError("Secure key storage backend is unavailable")
    return backend


def generate_db_key() -> str:
    return secrets.token_hex(32)


def get_db_key() -> str:
    backend = get_secure_backend()
    try:
        key = backend.get_password(KEY_SERVICE, KEY_NAME)
    except Exception as exc:
        raise RuntimeError("Database encryption key is unavailable") from exc
    if key is None:
        key = generate_db_key()
        try:
            backend.set_password(KEY_SERVICE, KEY_NAME, key)
        except Exception as exc:
            raise RuntimeError("Database encryption key could not be stored") from exc
    if not isinstance(key, str) or len(key) != 64:
        raise RuntimeError("Database encryption key is unavailable")
    return key
