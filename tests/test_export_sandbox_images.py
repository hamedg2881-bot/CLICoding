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
