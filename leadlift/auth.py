"""Authentication and authorization for LEADLIFT.

Provides password-based authentication using Argon2id password hashing and
credential storage via the system keyring (via the ``keyring`` library).
All sensitive operations require an authenticated session.

Design principles:
- Passwords are never stored in plaintext.
- Credentials are stored in the OS credential store via ``keyring``.
- Session state is used only for session tracking, not as an authorization
  boundary. Every protected operation re-validates the session.
- Fail-closed: if the credential store is unavailable, authentication fails
  rather than silently granting access.
"""

from __future__ import annotations

import secrets
from typing import Any, Optional

import argon2
from argon2.exceptions import VerifyMismatchError

KEY_SERVICE = "LEADLIFT_AUTH"
KEY_NAME = "admin_password_hash"


def hash_password(password: str) -> str:
    """Hash a password using Argon2id.

    Returns a standard Argon2 encoded hash string that can be stored
    directly in the credential store.
    """
    return argon2.PasswordHasher().hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against an Argon2id hash.

    Returns ``True`` if the password matches, ``False`` otherwise.
    Never raises on verification failure.
    """
    try:
        return argon2.PasswordHasher().verify(stored_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def get_credential_store() -> Any:
    """Return the keyring backend for credential storage.

    Raises:
        RuntimeError: If no secure credential backend is available.
    """
    import keyring

    try:
        backend = keyring.get_keyring()
    except Exception as exc:
        raise RuntimeError("Secure key storage is unavailable") from exc

    if backend is None:
        raise RuntimeError("Secure key storage backend is unavailable")

    name = type(backend).__name__
    insecure_markers = ("Null", "Plaintext", "Fail", "Chainer", "Uncrypted")
    secure_markers = ("WinVault", "Keyring", "SecretService", "KWallet", "Keychain", "MacOS")

    if any(marker in name for marker in insecure_markers):
        raise RuntimeError("Secure key storage backend is unavailable")
    if not any(marker in name for marker in secure_markers):
        raise RuntimeError("Secure key storage backend is unavailable")

    return backend


def get_stored_password_hash() -> Optional[str]:
    """Retrieve the stored password hash from the credential store.

    Returns ``None`` if no password has been set yet.
    """
    backend = get_credential_store()
    try:
        return backend.get_password(KEY_SERVICE, KEY_NAME)
    except Exception:
        return None


def store_password_hash(password_hash: str) -> None:
    """Store the password hash in the credential store."""
    backend = get_credential_store()
    try:
        backend.set_password(KEY_SERVICE, KEY_NAME, password_hash)
    except Exception as exc:
        raise RuntimeError("Could not store password hash") from exc


def authenticate(username: str, password: str) -> bool:
    """Authenticate a username and password combination.

    Returns ``True`` if the credentials are valid, ``False`` otherwise.
    Never raises on verification failure.
    """
    if not username or not password:
        return False

    stored_hash = get_stored_password_hash()
    if stored_hash is None:
        return False

    return verify_password(password, stored_hash)


def create_initial_user(username: str, password: str) -> None:
    """Create the initial administrative user.

    Stores the password hash in the credential store. Raises if a password
    hash already exists (to prevent accidental overwriting).
    """
    existing = get_stored_password_hash()
    if existing is not None:
        raise RuntimeError("Credential already exists")

    password_hash = hash_password(password)
    store_password_hash(password_hash)


def verify_user(username: str, password: str) -> bool:
    """Alias for :func:`authenticate` for API clarity."""
    return authenticate(username, password)


def is_authenticated() -> bool:
    """Check whether the current Streamlit session has an authenticated user."""
    import streamlit as st

    return bool(st.session_state.get("leadlift_authenticated", False))


def require_auth() -> None:
    """Require authentication for the current page.

    Raises ``PermissionError`` if the current session is not authenticated.
    Call this at the top of every protected page renderer.
    """
    import streamlit as st

    if not st.session_state.get("leadlift_authenticated", False):
        raise PermissionError("Authentication required")


def login(username: str, password: str) -> bool:
    """Authenticate a user and start an authenticated session.

    Returns ``True`` on success, ``False`` on invalid credentials.
    Never raises on verification failure.
    """
    import streamlit as st

    if authenticate(username, password):
        st.session_state["leadlift_authenticated"] = True
        st.session_state["leadlift_authenticated_user"] = username
        return True
    return False


def logout() -> None:
    """Log out the current user and clear the session."""
    import streamlit as st

    st.session_state.pop("leadlift_authenticated", None)
    st.session_state.pop("leadlift_authenticated_user", None)


def current_user() -> Optional[str]:
    """Return the username of the currently authenticated user, or None."""
    import streamlit as st

    return st.session_state.get("leadlift_authenticated_user")