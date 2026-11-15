import hashlib
import importlib.util
import json
import platform
import sys
from pathlib import Path

import pytest

_script = Path(__file__).parents[1] / "scripts" / "verify_wheelhouse.py"
_spec = importlib.util.spec_from_file_location("verify_wheelhouse", _script)
verifier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verifier)


def make_manifest(tmp_path):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / "example-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-content")
    manifest = {
        "format_version": 1,
        "python": sys.version,
        "implementation": sys.implementation.name,
        "machine": platform.machine(),
        "wheels": [
            {
                "file": wheel.name,
                "size": wheel.stat().st_size,
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            }
        ],
    }
    manifest_path = tmp_path / "wheelhouse-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return wheelhouse, manifest_path, manifest, wheel


def test_wheelhouse_verifier_accepts_exact_manifest(tmp_path):
    wheelhouse, manifest, _, _ = make_manifest(tmp_path)
    assert verifier.verify(wheelhouse, manifest) == 1


def test_wheelhouse_verifier_rejects_modified_wheel(tmp_path):
    wheelhouse, manifest, _, wheel = make_manifest(tmp_path)
    wheel.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="size mismatch|SHA-256 mismatch"):
        verifier.verify(wheelhouse, manifest)


def test_wheelhouse_verifier_rejects_unlisted_extra_wheel(tmp_path):
    wheelhouse, manifest, _, _ = make_manifest(tmp_path)
    (wheelhouse / "extra-1.0-py3-none-any.whl").write_bytes(b"extra")
    with pytest.raises(ValueError, match="exactly match"):
        verifier.verify(wheelhouse, manifest)


def test_wheelhouse_verifier_rejects_python_abi_and_architecture_mismatch(tmp_path):
    wheelhouse, manifest_path, manifest, _ = make_manifest(tmp_path)
    manifest["python"] = "3.99.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Python ABI mismatch"):
        verifier.verify(wheelhouse, manifest_path)

    manifest["python"] = sys.version
    manifest["machine"] = "not-the-host-architecture"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="architecture mismatch"):
        verifier.verify(wheelhouse, manifest_path)
