#!/usr/bin/env python3
"""Export the exact ARM64 Docker images used by the existing CLICoding sandbox."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "sandbox" / "clicoding_sandbox_runner.py"
TYPESCRIPT_DOCKERFILE = ROOT / "sandbox" / "images" / "typescript" / "Dockerfile"
TYPESCRIPT_CONTEXT = TYPESCRIPT_DOCKERFILE.parent


def load_runner():
    spec = importlib.util.spec_from_file_location("clicoding_sandbox_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load sandbox runner image inventory")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(command: list[str], timeout: int = 1800) -> str:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stderr[-4000:]}"
        )
    return result.stdout.strip()


def inspect_image(docker: str, reference: str) -> tuple[str, str, list[str]]:
    raw = run([
        docker, "image", "inspect", "--format",
        "{{.Id}}|{{.Os}}/{{.Architecture}}|{{json .RepoDigests}}",
        reference,
    ], timeout=30)
    parts = raw.split("|", 2)
    if len(parts) != 3:
        raise RuntimeError(f"unexpected docker inspect output for {reference}")
    image_id, platform_value, repo_digests_json = parts
    if platform_value != "linux/arm64":
        raise RuntimeError(f"{reference} is {platform_value}, expected linux/arm64")
    try:
        repo_digests = json.loads(repo_digests_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid RepoDigests output for {reference}") from exc
    if not isinstance(repo_digests, list):
        raise RuntimeError(f"invalid RepoDigests for {reference}")
    if "@sha256:" in reference:
        expected = reference.split("@", 1)[1]
        if not any(item.endswith("@" + expected) for item in repo_digests):
            raise RuntimeError(
                f"{reference} is not verified against its pinned repository digest; pull it by digest"
            )
    return image_id, platform_value, repo_digests


def context_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise RuntimeError("TypeScript image build context contains a symlink")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pull", action="store_true", help="pull all pinned upstream images for linux/arm64")
    parser.add_argument("--build-typescript", action="store_true", help="build the repository-pinned local TypeScript image")
    args = parser.parse_args()

    runner = load_runner()
    grouped: dict[str, list[str]] = {}
    for runtime_name, config in runner.RUNTIMES.items():
        grouped.setdefault(config["image"], []).append(runtime_name)
    grouped.setdefault(runner.DOTNET_WORKSPACE_IMAGE, []).append("workspace-dotnet")

    entries = []
    local_tags = []
    for reference, runtime_names in sorted(grouped.items()):
        if reference == "clicoding-typescript:7.0.2":
            if args.build_typescript:
                run([
                    args.docker, "buildx", "build", "--platform", "linux/arm64",
                    "--load", "--tag", reference,
                    "--file", str(TYPESCRIPT_DOCKERFILE),
                    str(TYPESCRIPT_CONTEXT),
                ], timeout=3600)
            version = run([
                args.docker, "run", "--rm", "--pull=never", "--network=none",
                reference, "tsc", "--version",
            ], timeout=60)
            if version.strip() != "Version 7.0.2":
                raise RuntimeError(f"unexpected TypeScript compiler version: {version}")
        elif args.pull:
            run([args.docker, "pull", "--platform", "linux/arm64", reference], timeout=3600)

        image_id, platform_value, repo_digests = inspect_image(args.docker, reference)
        tag_name = "-".join(sorted(runtime_names))
        local_tag = f"clicoding-sandbox/{tag_name}:arm64"
        run([args.docker, "tag", reference, local_tag], timeout=60)
        tagged_id, tagged_platform, _ = inspect_image(args.docker, local_tag)
        if tagged_id != image_id or tagged_platform != platform_value:
            raise RuntimeError(f"local export tag did not preserve image identity: {local_tag}")
        entries.append({
            "source_reference": reference,
            "runtime_names": sorted(runtime_names),
            "local_tag": local_tag,
            "image_id": image_id,
            "platform": platform_value,
            "source_repo_digests": repo_digests,
            "build_context_sha256": (
                context_sha256(TYPESCRIPT_CONTEXT)
                if reference == "clicoding-typescript:7.0.2" else None
            ),
        })
        local_tags.append(local_tag)
        print(f"READY {reference} -> {local_tag} ({image_id})")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    run([args.docker, "save", "--output", str(args.output), *local_tags], timeout=7200)
    archive_sha = sha256_file(args.output)
    archive_sidecar = Path(str(args.output) + ".sha256")
    archive_sidecar.write_text(
        f"{archive_sha}  {args.output.name}\n", encoding="utf-8"
    )

    lock = {
        "format_version": 1,
        "platform": "linux/arm64",
        "images": entries,
    }
    lock_path = args.output.with_name("sandbox-image-lock.json")
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    lock_sha = sha256_file(lock_path)
    Path(str(lock_path) + ".sha256").write_text(
        f"{lock_sha}  {lock_path.name}\n", encoding="utf-8"
    )
    print(f"IMAGE_ARCHIVE={args.output}")
    print(f"IMAGE_ARCHIVE_SHA256={archive_sha}")
    print(f"IMAGE_LOCK={lock_path}")
    print(f"IMAGE_LOCK_SHA256={lock_sha}")
    print(f"EXPORTED_IMAGES={len(entries)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"sandbox image export failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
