# CDS_AI context budgeting

**Status:** Conservative admission control and model-assisted compaction are implemented. Token counting remains heuristic until exact model/runtime tokenizers are integrated.

## Worker configuration

Each worker declares:
- `context_window_tokens`: total context window for the exact served model/runtime.
- `max_output_tokens`: maximum output reservation and default cap for that worker.

Defaults are deliberately conservative: 16384 context tokens and 2048 output tokens. Operators must replace these with verified runtime limits before enabling larger contexts. The registry rejects context windows below 1024, values above 1,000,000, and output reservations that are not smaller than the context window.

## Admission control

Before a direct completion is dispatched, CDS_AI estimates the full message history and reserves the requested output budget (or the worker's configured maximum). The estimate uses UTF-8 bytes divided by two plus per-message framing. Each image part reserves 4096 tokens rather than counting its base64 payload as text. Tool-call metadata is included.

This is a conservative heuristic, not a model-specific tokenizer. A request that exceeds the configured budget is rejected with HTTP 413. The system does not silently truncate or discard old messages. Direct streaming requests are checked before the SSE response starts. Specialist model calls receive the same per-worker admission check.

Supported OpenAI generation options (temperature, max_tokens, top_p, presence_penalty, frequency_penalty, and bounded stop sequences) are forwarded to the upstream model. A requested `max_tokens` above the selected worker's output cap is rejected.

## Direct-chat compaction

Direct chat and tool-call turns attempt model-assisted compaction only after context admission detects overflow:
- Versioned summaries are stored with the exact covered event sequence and model identifier.
- Original conversation events remain unchanged.
- The latest three user turns and their assistant/tool continuations are retained.
- Common credential-shaped values are redacted before summary storage.
- The final context budget is checked again after compaction.
- Summaries are invalidated transactionally when edit, regenerate or history deletion rewrites covered events.
- The system rejects rather than silently truncating if the summary source cannot safely fit.

## Specialist compaction

Planner, each specialist task worker, and final synthesis use worker-specific context compaction:
- Each worker's own context and output limits are respected.
- The original request, plan goal, acceptance criteria, task objective, expected output and dependency results are reserved outside the summarizable conversation history.
- Oversized history is split into bounded UTF-8 chunks and summarized in at most three compaction rounds.
- Summaries are redacted and rechecked against the receiving worker's budget.
- Canonical conversation events are never rewritten by specialist compaction.

## Response-integrity behavior

A specialist execution can be partial even when some tasks succeed. Final synthesis receives the status of every non-successful task and is instructed to disclose incomplete work. CDS_AI also appends a deterministic incomplete-work section after synthesis, so a model cannot silently present a partial execution as fully completed. An empty synthesis response falls back to the verified specialist outputs.

## Remaining limitations

- Tokenizer-specific counting for each served model/runtime.
- Model-specific image token estimation rather than a fixed reserve.
- Evaluation of summary fidelity on real multilingual conversations and long code/repository contexts.
- User-visible context inspection and summary correction controls.

Until model-specific tokenizers are available, the system uses conservative estimates and explicit errors rather than silently dropping context.
