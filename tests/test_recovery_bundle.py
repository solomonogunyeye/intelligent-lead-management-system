import os
import tempfile
from leadlift.recovery_bundle import export_recovery_bundle, recover_db_key_from_bundle


def test_recovery_bundle_round_trip():
    path = tempfile.mktemp(suffix=".rb")
    export_recovery_bundle(path, "passphrase")
    key = recover_db_key_from_bundle(path, "passphrase", install=False)
    assert len(key) == 64
    os.remove(path)


def test_wrong_passphrase_rejected():
    path = tempfile.mktemp(suffix=".rb")
    export_recovery_bundle(path, "passphrase")
    try:
        recover_db_key_from_bundle(path, "wrong", install=False)
        assert False
    except ValueError:
        pass
    finally:
        os.remove(path)
