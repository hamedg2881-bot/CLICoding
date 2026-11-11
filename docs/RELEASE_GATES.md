# CDS_AI release gates

**Product:** CDS_AI  
**Canonical source repository:** https://github.com/hamedg2881-bot/CLICoding  
**Policy:** Complete and verify source on GitHub first. Transfer and deployment are separate later stages.

## Implemented source features
- [x] Stable public assistant identity `CDS_AI`.
- [x] Conversation-first routing; ordinary chat bypasses Planner.
- [x] Persistent conversation history and Open WebUI chat/user mapping.
- [x] Direct-chat upstream SSE and disconnect cleanup.
- [x] Validated OpenAI function-tool round-trip and incremental tool-call SSE.
- [x] Workstation reference tool host with workspace scope, approval, idempotency and fixed commands.
- [x] Per-user bearer-token ownership isolation.
- [x] Retry idempotency for failed user/tool continuations.
- [x] Authenticated API cancellation for active conversation turns; incomplete assistant output is not persisted.
- [x] Context admission/output reservation; overflow is explicit, never silently truncated.

## Remaining source gates
- [ ] Client Stop controls and cancellation propagation to external workstation processes; current active-turn registry is process-local.
- [ ] Regenerate/edit/resubmit semantics and event lineage (being implemented in a parallel PR; not yet consolidated here).
- [x] Versioned model-assisted context summaries for direct chat/tool turns; original events remain intact, the latest three user turns are preserved, and common credential patterns are redacted.
- [ ] Specialist-orchestration compaction, summary invalidation on edit/regenerate, tokenizer-specific accounting and user-visible context inspection.
- [ ] Typed attachment records, bounded binary storage, MIME sniffing, retention and access checks.
- [ ] Packaged IDE integration with approval and diff/apply UX.
- [ ] Browser/Playwright/screenshot workflow and vision-based UI validation.
- [ ] Specialist progress events; specialist SSE currently emits a buffered final answer.
- [ ] Rate limits, token rotation/revocation, audit export and multi-process conversation locking.
- [ ] Cross-platform process sandboxing, secret redaction and path TOCTOU hardening.

## Live DGX runtime gates
- [ ] Verify exact served model aliases and ports at live `/v1/models` endpoints.
- [ ] Validate GPT-OSS-20B chat/planning, Qwen3-Coder-Next coding/review and Qwen3-VL-2B image understanding.
- [ ] Validate tool calling with the exact model/runtime/parser before enabling `tool_calling`.
- [ ] Measure context/output limits, timeout/cancellation, concurrency and unified-memory headroom.
- [ ] Verify routing and failure isolation without interrupting active requests.

The sample worker registry uses recorded ports 8003/8004/8005 but keeps every worker disabled. Verify served aliases before enabling.

## Client and offline release
- [ ] Open WebUI multi-turn, SSE, Stop and per-user isolation acceptance.
- [ ] Packaged IDE workspace selection, approvals, diff review/apply and build/test evidence.
- [ ] Diagnose and repair a deliberately failing sample project; capture real successful build/test exit codes.
- [ ] Browser screenshot, verified Vision handoff, visual correction and before/after evidence.
- [ ] Clean workstation installation with public internet blocked.
- [ ] Versioned ZIP and SHA-256 manifest, database/config backup, migration-on-copy and rollback validation.
- [ ] Stage without restart; obtain explicit approval before Orchestrator restart.
- [ ] Record exact commit, archive hash, deployed files, test results and rollback point.

## Current CI evidence
PR #12 commit `8761ea32ba7f42168f237d0973fb9637b303809d` passed run 43021678254: **127 passed, 1 warning**. The warning is a Starlette/httpx TestClient deprecation notice. This is source-only evidence, not live DGX or editor acceptance.
