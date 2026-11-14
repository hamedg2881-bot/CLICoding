#!/usr/bin/env python3
"""Read-only preflight for pinned Docker sandbox images and the offline NuGet feed."""
from __future__ import annotations

import argparse
import importlib.util
import json
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



def inspect_locked_image(docker: str, entry: dict) -> tuple[bool, str]:
    local_tag = entry.get("local_tag")
    expected_id = entry.get("image_id")
    if not isinstance(local_tag, str) or not isinstance(expected_id, str):
        return False, "invalid image lock entry"
    result = subprocess.run(
        [
            docker, "image", "inspect", "--format",
            "{{.Id}}|{{.Os}}/{{.Architecture}}", local_tag,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return False, "missing local image tag"
    actual_id, separator, platform_value = result.stdout.strip().partition("|")
    if not separator or actual_id != expected_id:
        return False, f"image ID mismatch: {actual_id or 'unknown'}"
    if platform_value != "linux/arm64":
        return False, f"wrong platform: {platform_value or 'unknown'}"
    return True, f"{local_tag} {actual_id} {platform_value}"


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
    parser.add_argument("--image-lock", type=Path)
    parser.add_argument(
        "--allow-unlocked-images",
        action="store_true",
        help="legacy mode: inspect source references instead of requiring an exported image lock",
    )
    parser.add_argument("--skip-feed", action="store_true")
    args = parser.parse_args()
    if not Path(args.docker).is_file():
        print(f"Docker CLI not found: {args.docker}", file=sys.stderr)
        return 2

    runner = load_runner()
    required_references = {
        item["image"] for item in runner.RUNTIMES.values()
    } | {runner.DOTNET_WORKSPACE_IMAGE}
    lock_path = args.image_lock or Path(runner.IMAGE_LOCK_PATH)
    failures = 0
    if lock_path.is_file() and not lock_path.is_symlink():
        metadata = lock_path.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            print("MISSING  image lock must be root-owned and not group/world writable")
            failures += 1
        else:
            try:
                lock = json.loads(lock_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                lock = {}
                failures += 1
                print("MISSING  image lock JSON is invalid")
            if lock.get("format_version") != 1 or lock.get("platform") != "linux/arm64":
                failures += 1
                print("MISSING  image lock format/platform mismatch")
            entries = lock.get("images", [])
            if not isinstance(entries, list):
                entries = []
                failures += 1
                print("MISSING  image lock image inventory is invalid")
            locked_references = {
                item.get("source_reference")
                for item in entries if isinstance(item, dict)
            }
            missing = required_references - locked_references
            if missing:
                failures += len(missing)
                for reference in sorted(missing):
                    print(f"MISSING  image lock entry: {reference}")
            print("Locked sandbox images (read-only inspection):")
            for entry in entries:
                if not isinstance(entry, dict):
                    failures += 1
                    print("MISSING  invalid image lock entry")
                    continue
                ok, detail = inspect_locked_image(args.docker, entry)
                print(f"{'OK' if ok else 'MISSING'}  {entry.get('source_reference')}  {detail}")
                failures += not ok
    elif args.allow_unlocked_images:
        print("Legacy unlocked-image check (no root-owned image lock found):")
        for reference in sorted(required_references):
            ok, detail = inspect_image(args.docker, reference)
            print(f"{'OK' if ok else 'MISSING'}  {reference}  {detail}")
            failures += not ok
    else:
        print(f"MISSING  root-owned sandbox image lock not found: {lock_path}")
        failures += 1

    if not args.skip_feed:
        ok, detail = check_feed(args.nuget_feed)
        print(f"{'OK' if ok else 'MISSING'}  NuGet feed {args.nuget_feed}  {detail}")
        failures += not ok

    if failures:
        print(f"Sandbox runtime preflight failed: {failures} requirement(s) are not ready.")
        return 1
    print("Sandbox runtime preflight passed: locked ARM64 images and feed policy verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
