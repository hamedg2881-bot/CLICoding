#!/usr/bin/env python3
"""Read-only preflight for pinned Docker sandbox images and the offline NuGet feed."""
from __future__ import annotations

import argparse
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "sandbox" / "clicoding_sandbox_runner.py"
DEFAULT_FEED = Path("/var/cache/clicoding-sandbox/nuget-feed")


def load_runner():
    spec = importlib.util.spec_from_file_location("clicoding_sandbox_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load sandbox runner image inventory")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_image(docker: str, image: str) -> tuple[bool, str]:
    result = subprocess.run(
        [
            docker, "image", "inspect", "--format",
            "{{.Os}}/{{.Architecture}} {{.Id}}", image,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return False, "missing (not pulled; --pull=never is enforced)"
    value = result.stdout.strip()
    if not value.startswith("linux/arm64 "):
        return False, f"wrong platform: {value or 'unknown'}"
    return True, value


def check_feed(path: Path) -> tuple[bool, str]:
    if not path.is_dir() or path.is_symlink():
        return False, "missing or not a real directory"
    metadata = path.stat()
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        return False, "must be root-owned and not group/world writable"
    for item in path.rglob("*"):
        item_metadata = item.lstat()
        if item.is_symlink() or item_metadata.st_uid != 0 or item_metadata.st_mode & 0o022:
            return False, f"unsafe feed entry: {item.relative_to(path)}"
        if not (item.is_dir() or item.is_file()):
            return False, f"unsupported special file in feed: {item.relative_to(path)}"
    return True, "root-owned, no symlinks, not group/world writable"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="/usr/bin/docker")
    parser.add_argument("--nuget-feed", type=Path, default=DEFAULT_FEED)
    parser.add_argument("--skip-feed", action="store_true")
    args = parser.parse_args()
    if not Path(args.docker).is_file():
        print(f"Docker CLI not found: {args.docker}", file=sys.stderr)
        return 2

    runner = load_runner()
    images = sorted({item["image"] for item in runner.RUNTIMES.values()} | {
        runner.DOTNET_WORKSPACE_IMAGE
    })
    failures = 0
    print("Pinned sandbox images (read-only inspection; no pulls):")
    for image in images:
        ok, detail = inspect_image(args.docker, image)
        print(f"{'OK' if ok else 'MISSING'}  {image}  {detail}")
        failures += not ok

    if not args.skip_feed:
        ok, detail = check_feed(args.nuget_feed)
        print(f"{'OK' if ok else 'MISSING'}  NuGet feed {args.nuget_feed}  {detail}")
        failures += not ok

    if failures:
        print(f"Sandbox runtime preflight failed: {failures} requirement(s) are not ready.")
        return 1
    print(f"Sandbox runtime preflight passed: {len(images)} pinned images and feed policy verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
