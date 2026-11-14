import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_script = Path(__file__).parents[1] / "scripts" / "export_sandbox_images.py"
_spec = importlib.util.spec_from_file_location("export_sandbox_images", _script)
exporter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exporter)


def test_export_inspection_requires_arm64_and_exact_source_digest(monkeypatch):
    digest = "a" * 64
    monkeypatch.setattr(
        exporter,
        "run",
        lambda command, timeout=1800: (
            f"sha256:{'b' * 64}|linux/arm64|"
            + json.dumps([f"registry.example/image@sha256:{digest}"])
        ),
    )
    image_id, platform_value, repo_digests = exporter.inspect_image(
        "docker", f"registry.example/image@sha256:{digest}"
    )
    assert image_id == f"sha256:{'b' * 64}"
    assert platform_value == "linux/arm64"
    assert repo_digests[0].endswith(digest)


def test_export_inspection_rejects_wrong_or_unpinned_image(monkeypatch):
    monkeypatch.setattr(
        exporter,
        "run",
        lambda command, timeout=1800: "sha256:abc|linux/amd64|[]",
    )
    with pytest.raises(RuntimeError, match="expected linux/arm64"):
        exporter.inspect_image("docker", "example/image:latest")

    monkeypatch.setattr(
        exporter,
        "run",
        lambda command, timeout=1800: "sha256:abc|linux/arm64|[]",
    )
    with pytest.raises(RuntimeError, match="not verified against its pinned"):
        exporter.inspect_image("docker", "example/image@sha256:" + "a" * 64)


def test_typescript_build_context_hash_is_stable_and_rejects_symlinks(tmp_path):
    context = tmp_path / "context"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    first = exporter.context_sha256(context)
    second = exporter.context_sha256(context)
    assert first == second
    assert len(first) == 64

    target = tmp_path / "target"
    target.write_text("outside", encoding="utf-8")
    (context / "link").symlink_to(target)
    with pytest.raises(RuntimeError, match="symlink"):
        exporter.context_sha256(context)


def test_archive_checksum_streaming(tmp_path):
    archive = tmp_path / "images.tar"
    archive.write_bytes(b"container archive")
    assert exporter.sha256_file(archive) == hashlib.sha256(b"container archive").hexdigest()



def test_lock_only_output_writes_inventory_without_docker_archive(tmp_path, monkeypatch):
    entries = [{
        "source_reference": "example/image@sha256:" + "a" * 64,
        "runtime_names": ["python"],
        "local_tag": "clicoding-sandbox/python:arm64",
        "image_id": "sha256:" + "b" * 64,
        "platform": "linux/arm64",
        "source_repo_digests": ["example/image@sha256:" + "a" * 64],
        "build_context_sha256": None,
    }]
    output_dir = tmp_path / "prepared"
    output_dir.mkdir()
    lock = {
        "format_version": 1,
        "platform": "linux/arm64",
        "images": entries,
    }
    lock_path = output_dir / "sandbox-image-lock.json"
    lock_path.write_text(json.dumps(lock, indent=2) + "\\n", encoding="utf-8")
    digest = exporter.sha256_file(lock_path)
    sidecar = output_dir / "sandbox-image-lock.json.sha256"
    sidecar.write_text(f"{digest}  {lock_path.name}\\n", encoding="utf-8")

    assert json.loads(lock_path.read_text(encoding="utf-8")) == lock
    assert sidecar.read_text(encoding="utf-8").startswith(digest)
    assert not (output_dir / "clicoding-sandbox-images-arm64.tar").exists()
