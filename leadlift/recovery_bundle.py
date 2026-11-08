from __future__ import annotations

import os
import secrets
import struct
from pathlib import Path
from typing import Optional

import sqlcipher3.dbapi2 as sqlcipher

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from leadlift.key_management import KEY_NAME, KEY_SERVICE, get_db_key, is_secure_backend

MAGIC = b"LEADLIFT-RB\x00"
FORMAT_VERSION = 1
KDF_ARGON2ID = 1
CIPHER_AES_256_GCM = 1
MAX_BUNDLE_SIZE = 1024 * 1024
KEY_ID = "leadlift-db-key-v1"
METADATA = b"purpose=database-key-recovery;version=1"


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or not passphrase:
        raise ValueError("Recovery passphrase is required")
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=32,
        type=Type.ID,
    )


def export_recovery_bundle(destination: str, passphrase: str) -> None:
    db_key_hex = get_db_key()
    db_key = bytes.fromhex(db_key_hex)
    if len(db_key) != 32:
        raise RuntimeError("Invalid database key")
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    recovery_key = _derive_key(passphrase, salt)
    aad = MAGIC + struct.pack(">HBB", FORMAT_VERSION, KDF_ARGON2ID, CIPHER_AES_256_GCM) + KEY_ID.encode("utf-8") + METADATA
    aesgcm = AESGCM(recovery_key)
    ciphertext = aesgcm.encrypt(nonce, db_key, aad)
    # ciphertext includes 16-byte tag
    body = (
        MAGIC
        + struct.pack(">HBB", FORMAT_VERSION, KDF_ARGON2ID, CIPHER_AES_256_GCM)
        + salt
        + nonce
        + struct.pack(">H", len(KEY_ID.encode("utf-8"))) + KEY_ID.encode("utf-8")
        + struct.pack(">I", len(METADATA)) + METADATA
        + struct.pack(">I", len(ciphertext)) + ciphertext
    )
    dest_path = Path(destination)
    tmp_path = dest_path.with_name(dest_path.name + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    tmp_path.write_bytes(body)
    # verify before finalizing
    recovered = recover_db_key_from_bundle(str(tmp_path), passphrase, install=False)
    if recovered != db_key_hex:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("Recovery bundle verification failed")
    os.replace(tmp_path, dest_path)


def recover_db_key_from_bundle(path: str, passphrase: str, install: bool = True) -> str:
    data = Path(path).read_bytes()
    if len(data) > MAX_BUNDLE_SIZE or len(data) < 32:
        raise ValueError("Invalid recovery bundle")
    offset = 0
    if not data.startswith(MAGIC):
        raise ValueError("Invalid recovery bundle")
    offset += len(MAGIC)
    if offset + 4 > len(data):
        raise ValueError("Invalid recovery bundle")
    version, kdf_id, cipher_id = struct.unpack(">HBB", data[offset:offset + 4])
    offset += 4
    if version != FORMAT_VERSION or kdf_id != KDF_ARGON2ID or cipher_id != CIPHER_AES_256_GCM:
        raise ValueError("Unsupported recovery bundle")
    if offset + 16 + 12 > len(data):
        raise ValueError("Invalid recovery bundle")
    salt = data[offset:offset + 16]
    offset += 16
    nonce = data[offset:offset + 12]
    offset += 12
    if offset + 2 > len(data):
        raise ValueError("Invalid recovery bundle")
    key_id_len = struct.unpack(">H", data[offset:offset + 2])[0]
    offset += 2
    if key_id_len != len(KEY_ID.encode("utf-8")) or offset + key_id_len > len(data):
        raise ValueError("Invalid recovery bundle")
    key_id = data[offset:offset + key_id_len].decode("utf-8")
    offset += key_id_len
    if key_id != KEY_ID:
        raise ValueError("Invalid recovery bundle")
    if offset + 4 > len(data):
        raise ValueError("Invalid recovery bundle")
    metadata_len = struct.unpack(">I", data[offset:offset + 4])[0]
    offset += 4
    if metadata_len != len(METADATA) or offset + metadata_len > len(data):
        raise ValueError("Invalid recovery bundle")
    metadata = data[offset:offset + metadata_len]
    offset += metadata_len
    if metadata != METADATA:
        raise ValueError("Invalid recovery bundle")
    if offset + 4 > len(data):
        raise ValueError("Invalid recovery bundle")
    ciphertext_len = struct.unpack(">I", data[offset:offset + 4])[0]
    offset += 4
    if ciphertext_len < 16 or offset + ciphertext_len != len(data):
        raise ValueError("Invalid recovery bundle")
    ciphertext = data[offset:offset + ciphertext_len]
    aad = MAGIC + struct.pack(">HBB", version, kdf_id, cipher_id) + key_id.encode("utf-8") + metadata
    recovery_key = _derive_key(passphrase, salt)
    try:
        db_key = AESGCM(recovery_key).decrypt(nonce, ciphertext, aad)
    except Exception as exc:
        raise ValueError("Recovery bundle authentication failed") from exc
    if len(db_key) != 32:
        raise ValueError("Invalid recovered key")
    db_key_hex = db_key.hex()
    if install:
        from leadlift.key_management import get_secure_backend
        backend = get_secure_backend()
        backend.set_password(KEY_SERVICE, KEY_NAME, db_key_hex)
    return db_key_hex





def recover_database_key(
    bundle_path: str,
    passphrase: str,
    db_path: Optional[str] = None,
    overwrite: bool = False,
) -> str:
    """Recover and safely install a database key from a recovery bundle."""
    recovered_key = recover_db_key_from_bundle(bundle_path, passphrase, install=False)

    if db_path is not None:
        db = Path(db_path)
        if db.exists():
            try:
                conn = sqlcipher.connect(str(db))
                conn.execute("PRAGMA key = \"x'{}'\"".format(recovered_key))
                conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
                conn.close()
            except Exception as exc:
                raise RuntimeError("Recovered key does not open the target database") from exc

    from leadlift.key_management import get_secure_backend

    backend = get_secure_backend()
    try:
        existing = backend.get_password(KEY_SERVICE, KEY_NAME)
    except Exception as exc:
        raise RuntimeError("Existing database key could not be inspected") from exc

    if existing is not None and existing != recovered_key and not overwrite:
        raise RuntimeError("An existing database key differs; refusing to overwrite")

    if existing != recovered_key:
        try:
            backend.set_password(KEY_SERVICE, KEY_NAME, recovered_key)
        except Exception as exc:
            raise RuntimeError("Recovered database key could not be installed") from exc

    return recovered_key
