# CDS_AI image attachments

CDS_AI accepts conversation-scoped image attachments without requiring a multipart dependency or embedding large base64 payloads in client messages.

## Upload

Send the raw image bytes to:

```http
POST /api/v1/conversations/{conversation_id}/attachments
Content-Type: image/png
X-Filename: screenshot.png

<raw image bytes>
```

The server streams the request body into a bounded buffer and rejects bodies above `CLICODING_MAX_ATTACHMENT_BYTES` (default 10 MiB; maximum configurable value 25 MiB). It detects the actual image type from the file signature and accepts PNG, JPEG, GIF and WebP only. A declared non-generic Content-Type must match the detected type.

The response includes a conversation-scoped `attachment_id` and a `content_part` ready to include in a chat message:

```json
{
  "type": "attachment",
  "attachment_id": "00000000-0000-0000-0000-000000000000"
}
```

Use it in a normal OpenAI-compatible message:

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "Review this screenshot."},
    {"type": "attachment", "attachment_id": "00000000-0000-0000-0000-000000000000"}
  ]
}
```

The API resolves the reference to a verified data URI only when constructing the model request. Conversation history stores the attachment reference, not a duplicate base64 image. The reference is scoped to the conversation; another conversation cannot read it.

## Retrieve and delete

- `GET /api/v1/conversations/{conversation_id}/attachments/{attachment_id}`
- `DELETE /api/v1/conversations/{conversation_id}/attachments/{attachment_id}`

Both operations require conversation ownership. Retrieval verifies stored size and SHA-256 before returning bytes.

## Storage and security

- `CLICODING_ATTACHMENT_STORAGE_DIR` defaults to `./data/attachments`.
- Filenames are metadata only; on-disk names are random UUIDs.
- Files are created with restrictive permissions where supported; path resolution rejects traversal and symlink escapes.
- The server checks MIME signatures, configured size limits and SHA-256 integrity.
- Context summarization replaces old image payloads with a reference note; original conversation events and attachment files are not silently removed.

Automatic expiry/retention, storage backup/restore, content decoding validation and malware scanning remain release gates. Back up the attachment directory together with the SQLite database. Do not expose the attachment directory as a public static web root.
