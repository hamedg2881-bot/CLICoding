from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path, PurePosixPath
from typing import Any


class AttachmentError(ValueError):
    pass


_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
)


def sniff_image_type(data: bytes) -> tuple[str, str]:
    if not data:
        raise AttachmentError("attachment is empty")
    for signature, mime_type, extension in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime_type, extension
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise AttachmentError("only PNG, JPEG, GIF and WebP images are supported")


def safe_filename(value: str | None) -> str:
    candidate = (value or "image").replace("\\", "/").split("/")[-1]
    cleaned = "".join(
        character for character in candidate
        if character.isprintable() and character not in '/\\\"'
    ).strip()
    return (cleaned or "image")[:128]


def store_image(
    storage_root: str,
    conversation_id: str,
    filename: str | None,
    data: bytes,
) -> dict[str, Any]:
    try:
        normalized_conversation_id = str(uuid.UUID(conversation_id))
    except (ValueError, AttributeError) as exc:
        raise AttachmentError("invalid conversation identifier") from exc
    mime_type, extension = sniff_image_type(data)
    attachment_id = str(uuid.uuid4())
    root = Path(storage_root).expanduser().resolve()
    directory = root / normalized_conversation_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    storage_key = f"{normalized_conversation_id}/{attachment_id}.{extension}"
    path = directory / f"{attachment_id}.{extension}"
    if not path.resolve().parent.is_relative_to(root):
        raise AttachmentError("attachment storage path escaped its root")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise
    return {
        "id": attachment_id,
        "conversation_id": normalized_conversation_id,
        "filename": safe_filename(filename),
        "mime_type": mime_type,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "storage_key": storage_key,
    }


def resolve_storage_path(storage_root: str, storage_key: str) -> Path:
    root = Path(storage_root).expanduser().resolve()
    relative = PurePosixPath(storage_key)
    if (
        relative.is_absolute()
        or len(relative.parts) != 2
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise AttachmentError("invalid attachment storage key")
    try:
        uuid.UUID(relative.parts[0])
        uuid.UUID(relative.parts[1].rsplit(".", 1)[0])
    except ValueError as exc:
        raise AttachmentError("invalid attachment storage key") from exc
    path = (root / relative.parts[0] / relative.parts[1]).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise AttachmentError("attachment storage path is invalid")
    return path


def delete_storage_file(storage_root: str, storage_key: str) -> None:
    try:
        path = resolve_storage_path(storage_root, storage_key)
    except FileNotFoundError:
        return
    path.unlink(missing_ok=True)
