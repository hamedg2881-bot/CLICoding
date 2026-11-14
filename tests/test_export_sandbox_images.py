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



def test_lock_only_prepares_images_without_docker_save(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    refs = {
        "python": "registry.example/python@sha256:" + "a" * 64,
        "typescript": "clicoding-typescript:7.0.2",
    }
    fake_runner = SimpleNamespace(
        RUNTIMES={name: {"image": image} for name, image in refs.items()},
        DOTNET_WORKSPACE_IMAGE="registry.example/dotnet@sha256:" + "c" * 64,
    )
    commands = []
    local_images = {}

    def fake_run(command, timeout=1800):
        commands.append(command)
        if command[:3] == ["docker", "run", "--rm"]:
            return "Version 7.0.2"
        if command[:2] == ["docker", "tag"]:
            local_images[command[3]] = command[2]
        return ""

    def fake_inspect(docker, reference):
        if reference in local_images:
            return local_images[reference], "linux/arm64", []
        digest = hashlib.sha256(reference.encode()).hexdigest()
        repo_digests = (
            [reference] if "@sha256:" in reference else []
        )
        return "sha256:" + digest, "linux/arm64", repo_digests

    monkeypatch.setattr(exporter, "load_runner", lambda: fake_runner)
    monkeypatch.setattr(exporter, "run", fake_run)
    monkeypatch.setattr(exporter, "inspect_image", fake_inspect)
    monkeypatch.setattr(
        sys, "argv",
        [
            "export_sandbox_images.py", "--pull", "--build-typescript",
            "--lock-only", "--output-dir", str(tmp_path),
        ],
    )

    assert exporter.main() == 0
    lock_path = tmp_path / "sandbox-image-lock.json"
    sidecar = tmp_path / "sandbox-image-lock.json.sha256"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert len(lock["images"]) == 3
    assert sidecar.is_file()
    assert not any(command[1:2] == ["save"] for command in commands)
    assert any(command[1:2] == ["pull"] for command in commands)
    assert any(command[1:2] == ["buildx"] for command in commands)
