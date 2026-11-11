# CDS_AI conversation operations

## Append (default)

Every OpenAI-compatible chat request is an append operation unless an explicit operation header is supplied.

## Retry after failure or Stop

If an upstream request fails or an SSE client disconnects, the user/tool input event remains in the durable history, while a partial assistant event is not written. An exact retry of the same final input (same role, stored content, and metadata) reuses that unfinished event rather than duplicating it.

For streamed direct chat, client cancellation propagates to the upstream HTTP stream and releases the worker semaphore. The same cancellation behavior applies while the stream is being consumed by the client.

## Regenerate

Send:

```http
X-CLICoding-Operation: regenerate
```

Regeneration is accepted only when:
- the latest persisted event is an assistant event;
- the immediately preceding event is the same user/tool input supplied in the request, including stored content and structured tool-result metadata.

The service temporarily excludes that final assistant event from the model context, runs the same input again, and deletes/replaces the old tail event only after the new answer or tool call has completed successfully. If regeneration fails or is cancelled, the previous assistant answer remains intact. Earlier history is not rewritten. A mismatch or missing completed assistant turn returns HTTP 409 for buffered requests; streamed requests return an SSE error event.

This works for direct answers and tool-call turns. For a previous tool-call turn, send the same user input and tool definitions again. For a completed tool-result continuation, send the exact same tool result.

## Edit/resubmit

Editing an earlier user message and truncating all later turns is not yet implemented. It requires a target event ID and a user-visible confirmation because it rewrites conversation history. Do not simulate it by changing an old message in place.

## Client integration

The Python workstation client currently exposes ordinary chat/tool loops. It does not yet expose a dedicated regenerate method. OpenAI-compatible clients can send the operation header directly. A packaged Open WebUI filter/IDE UI integration for this header remains an acceptance task.
