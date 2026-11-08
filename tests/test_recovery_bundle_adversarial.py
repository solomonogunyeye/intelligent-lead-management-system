import os
import struct
import tempfile

import pytest

from leadlift import recovery_bundle as rb
from leadlift.recovery_bundle import (
    MAGIC,
    FORMAT_VERSION,
    MAX_BUNDLE_SIZE,
    export_recovery_bundle,
    recover_db_key_from_bundle,
)


def _patch_key(monkeypatch):
    monkeypatch.setattr(rb, "get_db_key", lambda: "a" * 64)


def test_valid_round_trip(monkeypatch, tmp_path):
    _patch_key(monkeypatch)
    path = str(tmp_path / "bundle.rb")
    export_recovery_bundle(path, "passphrase")
    assert recover_db_key_from_bundle(path, "passphrase", install=False) == "a" * 64


def test_wrong_passphrase(monkeypatch, tmp_path):
    _patch_key(monkeypatch)
    path = str(tmp_path / "bundle.rb")
    export_recovery_bundle(path, "passphrase")
    with pytest.raises(ValueError):
        recover_db_key_from_bundle(path, "wrong", install=False)


def test_tampered_magic(monkeypatch, tmp_path):
    _patch_key(monkeypatch)
    path = str(tmp_path / "bundle.rb")
    export_recovery_bundle(path, "passphrase")
    data = bytearray(open(path, "rb").read())
    data[0] ^= 1
    open(path, "wb").write(data)
    with pytest.raises(ValueError):
        recover_db_key_from_bundle(path, "passphrase", install=False)


def test_truncated_bundle(monkeypatch, tmp_path):
    _patch_key(monkeypatch)
    path = str(tmp_path / "bundle.rb")
    export_recovery_bundle(path, "passphrase")
    data = open(path, "rb").read()
    open(path, "wb").write(data[:-5])
    with pytest.raises(ValueError):
        recover_db_key_from_bundle(path, "passphrase", install=False)


def test_oversized_bundle(tmp_path):
    path = str(tmp_path / "bundle.rb")
    with open(path, "wb") as f:
        f.write(b"x" * (rb.MAX_BUNDLE_SIZE + 1))
    with pytest.raises(ValueError):
        recover_db_key_from_bundle(path, "passphrase", install=False)


def test_empty_bundle(tmp_path):
    path = str(tmp_path / "bundle.rb")
    open(path, "wb").write(b"")
    with pytest.raises(ValueError):
        recover_db_key_from_bundle(path, "passphrase", install=False)


def test_export_preserves_existing_bundle(monkeypatch, tmp_path):
    _patch_key(monkeypatch)
    path = str(tmp_path / "bundle.rb")
    export_recovery_bundle(path, "passphrase")
    original = open(path, "rb").read()
    # force verification failure by corrupting after export? Instead simulate failed replace
    import os
    real_replace = os.replace
    def fail_replace(src, dst):
        raise OSError("replace failed")
    monkeypatch.setattr(rb.os, "replace", fail_replace)
    try:
        export_recovery_bundle(path, "passphrase")
    except OSError:
        pass
    assert open(path, "rb").read() == original


def test_credential_store_failure(monkeypatch, tmp_path):
    _patch_key(monkeypatch)
    path = str(tmp_path / "bundle.rb")
    export_recovery_bundle(path, "passphrase")
    import leadlift.key_management as km
    class BadBackend:
        pass
    monkeypatch.setattr(km, "get_secure_backend", lambda: BadBackend())
    with pytest.raises(Exception):
        recover_db_key_from_bundle(path, "passphrase", install=True)
