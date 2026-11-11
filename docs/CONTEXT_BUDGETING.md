# CDS_AI context budgeting

**Status:** Conservative admission control implemented; automatic history compaction is not yet implemented.

## Worker configuration

Each worker may declare:

- `context_window_tokens`: total context window for the exact served model/runtime.
- `max_output_tokens`: maximum output reservation and default cap for that worker.

Defaults are deliberately conservative: 16384 context tokens and 2048 output tokens. Operators must replace these with verified runtime limits before enabling larger contexts. The registry rejects context windows below 1024, values above 1,000,000, and output reservations that are not smaller than the context window.

## Admission control

Before a direct completion is dispatched, CDS_AI estimates the full message history and reserves the requested output budget (or the worker's configured maximum). The estimate uses UTF-8 bytes divided by two plus per-message framing. Each image part reserves 4096 tokens rather than counting its base64 payload as text. Tool-call metadata is included.

This is intentionally an over-estimate for many text inputs and is not a model-specific tokenizer. A request that exceeds the configured budget is rejected with HTTP 413. The system does not silently truncate or discard old messages. Direct streaming requests are checked before the SSE response starts. Specialist model calls receive the same per-worker admission check.

Supported OpenAI generation options (temperature, max_tokens, top_p, presence_penalty, frequency_penalty, and bounded stop sequences) are forwarded to the upstream model. A requested `max_tokens` above the selected worker's output cap is rejected.

## Not yet implemented

- Tokenizer-specific counting for each served model.
- Automatic summarization/compaction with summary lineage and user-visible recovery.
- Selective retention of code blocks, file references, tool results, and user decisions during compaction.
- A context-inspection endpoint/UI showing estimated use and preserved history.
- Model-specific image token estimation.

Until these are implemented, the system prefers an explicit 413 over a silent loss of conversational context.


## Safe compaction implementation — 2026-11-11

Direct chat and tool-call turns attempt model-assisted compaction only after context admission detects overflow. The implementation:
- stores versioned summaries in `conversation_summaries` with the exact covered event sequence and model identifier;
- leaves original conversation events unchanged;
- retains the latest three user turns and their assistant/tool continuations;
- redacts common credential-shaped values before summary storage;
- rechecks the final context budget after compaction;
- rejects rather than truncates if the summary source itself cannot fit;
- invalidates summaries transactionally when edit, regenerate or history deletion rewrites covered events.

Current limitations: specialist multi-model orchestration does not yet use the compaction path; token estimates remain heuristic rather than model-specific; oversized summarization prefixes are rejected instead of chunked; user-visible context inspection is not yet packaged in clients.
