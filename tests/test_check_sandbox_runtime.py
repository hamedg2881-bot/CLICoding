import importlib.util
import subprocess
from pathlib import Path

_script = Path(__file__).parents[1] / "scripts" / "check_sandbox_runtime.py"
_spec = importlib.util.spec_from_file_location("check_sandbox_runtime", _script)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def test_runtime_image_inventory_is_loaded_from_existing_runner():
    runner = checker.load_runner()
    images = {item["image"] for item in runner.RUNTIMES.values()}
    assert runner.DOTNET_WORKSPACE_IMAGE in images
    assert len(images) >= 10


def test_image_inspection_requires_local_linux_arm64_digest(monkeypatch):
    monkeypatch.setattr(
        checker.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="linux/arm64 sha256:abc\n", stderr=""
        ),
    )
    ok, detail = checker.inspect_image("/usr/bin/docker", "example@sha256:abc")
    assert ok is True
    assert detail.startswith("linux/arm64 ")

    monkeypatch.setattr(
        checker.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="linux/amd64 sha256:def\n", stderr=""
        ),
    )
    ok, detail = checker.inspect_image("/usr/bin/docker", "example@sha256:def")
    assert ok is False
    assert "wrong platform" in detail


def test_feed_check_fails_closed_when_feed_is_missing_or_symlinked(tmp_path):
    missing = tmp_path / "missing-feed"
    ok, _ = checker.check_feed(missing)
    assert ok is False

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "feed-link"
    link.symlink_to(target, target_is_directory=True)
    ok, _ = checker.check_feed(link)
    assert ok is False
