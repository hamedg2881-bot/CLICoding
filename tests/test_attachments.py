from pathlib import Path

import pytest

from clicoding_orchestrator.attachments import (
    AttachmentError,
    delete_storage_file,
    resolve_storage_path,
    safe_filename,
    sniff_image_type,
    store_image,
)


def test_image_signature_detection_and_safe_filename():
    png = b"\x89PNG\r\n\x1a\n" + b"data"
    assert sniff_image_type(png) == ("image/png", "png")
    assert safe_filename("../folder\\secret.png") == "secret.png"
    with pytest.raises(AttachmentError):
        sniff_image_type(b"<svg></svg>")


def test_attachment_storage_is_conversation_scoped_and_deletable(tmp_path):
    import uuid

    conversation_id = str(uuid.uuid4())
    data = b"\xff\xd8\xff" + b"jpeg-data"
    record = store_image(str(tmp_path), conversation_id, "../../photo.jpg", data)

    path = resolve_storage_path(str(tmp_path), record["storage_key"])
    assert path.read_bytes() == data
    assert record["filename"] == "photo.jpg"
    assert record["mime_type"] == "image/jpeg"
    assert record["sha256"]

    with pytest.raises(AttachmentError):
        resolve_storage_path(str(tmp_path), "../outside.txt")

    delete_storage_file(str(tmp_path), record["storage_key"])
    assert not path.exists()
