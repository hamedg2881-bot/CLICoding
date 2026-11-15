#!/usr/bin/env python3
"""Verify an offline wheelhouse against its CI-generated manifest and target ABI."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path


def normalize_machine(value: str) -> str:
    value = value.lower()
    return "aarch64" if value in {"aarch64", "arm64"} else value


def verify(wheelhouse: Path, manifest_path: Path) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise ValueError("unsupported wheelhouse manifest version")
    if manifest.get("implementation") != "cpython":
        raise ValueError("wheelhouse must target CPython")
    expected_python = str(manifest.get("python", "")).split()[0]
    expected_parts = expected_python.split(".")
    if len(expected_parts) < 2:
        raise ValueError("manifest does not contain a valid Python version")
    current_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if ".".join(expected_parts[:2]) != current_python:
        raise ValueError(
            f"Python ABI mismatch: wheelhouse is for {expected_parts[0]}.{expected_parts[1]}, "
            f"runtime is {current_python}"
        )
    expected_machine = normalize_machine(str(manifest.get("machine", "")))
    current_machine = normalize_machine(platform.machine())
    if expected_machine != current_machine:
        raise ValueError(
            f"architecture mismatch: wheelhouse is for {expected_machine}, runtime is {current_machine}"
        )
    entries = manifest.get("wheels")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest has no wheel entries")
    expected_names = set()
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("invalid wheel manifest entry")
        name = item.get("file")
        if not isinstance(name, str) or not name.endswith(".whl") or Path(name).name != name:
            raise ValueError("manifest contains an unsafe wheel filename")
        if name in expected_names:
            raise ValueError("manifest contains duplicate wheel filenames")
        expected_names.add(name)
        wheel = wheelhouse / name
        if not wheel.is_file() or wheel.is_symlink():
            raise ValueError(f"wheel is missing or not a regular file: {name}")
        if wheel.stat().st_size != item.get("size"):
            raise ValueError(f"wheel size mismatch: {name}")
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if digest != item.get("sha256"):
            raise ValueError(f"wheel SHA-256 mismatch: {name}")
    actual_names = {item.name for item in wheelhouse.glob("*.whl") if item.is_file()}
    if actual_names != expected_names:
        raise ValueError("wheelhouse contents do not exactly match the manifest")
    return len(expected_names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        count = verify(args.wheelhouse, args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"wheelhouse verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Verified {count} ARM64 wheels for CPython "
        f"{sys.version_info.major}.{sys.version_info.minor}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
