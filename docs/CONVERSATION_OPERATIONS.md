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

Send:

```http
X-CLICoding-Operation: edit
X-CLICoding-Event-ID: <target-user-event-id>
```

The target ID is available from the conversation event history. Only user events can be edited. CDS_AI builds the new model context from events before the target plus the replacement user content. It deletes the target and all later events, and appends the replacement turn, only after the new answer/tool call completes successfully. If the request fails or is cancelled, the original history remains intact. This is a deliberate history rewrite; clients should confirm the edit with the user.

## Client integration

The Python workstation client currently exposes ordinary chat/tool loops. It does not yet expose dedicated regenerate/edit methods. For Open WebUI requests carrying both configured chat/user identity headers, CDS_AI also detects regeneration when the submitted non-system transcript exactly matches the stored transcript with only the final assistant event removed. An ordinary repeated prompt that includes the previous assistant answer does not match and remains a new turn. Other OpenAI-compatible clients can send the operation header directly.
