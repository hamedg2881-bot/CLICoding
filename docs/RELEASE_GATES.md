# CDS_AI release gates

Source completion and CI verification happen on GitHub first. DGX transfer is a separate release stage.

## Implemented source gates
- [x] Public assistant identity `CDS_AI`.
- [x] Conversation-first routing and persistent history.
- [x] Direct-chat SSE and disconnect cancellation.
- [x] Validated OpenAI function tool-call round-trip and incremental tool-call SSE.
- [x] Workstation reference tool host with workspace scope, approval, idempotency and fixed commands.
- [x] Per-user bearer-token ownership isolation.
- [x] Retry idempotency and conservative context admission.

## Remaining source gates
- [ ] User-visible stop/cancel for non-streaming, tool and task operations.
- [ ] Regenerate/edit/resubmit semantics and event lineage.
- [ ] Automatic context compaction, summary lineage and context inspection.
- [ ] Typed attachment storage, MIME validation, retention and access controls.
- [ ] Packaged IDE integration with approval and diff/apply UI.
- [ ] Browser/Playwright/screenshot workflow and visual validation.
- [ ] Rate limiting, token rotation/revocation, audit export and multi-process locking.
- [ ] Process sandboxing, secret redaction and cross-platform adapter tests.
- [ ] Specialist progress events; specialist responses are currently buffered then emitted as SSE.

## Live DGX gates
- [ ] Verify exact served aliases and ports at runtime.
- [ ] Validate chat, coding, vision, tool-call parser behavior and context limits on the exact deployed models.
- [ ] Measure concurrency, timeouts, cancellation and unified-memory headroom.
- [ ] Do not enable `tool_calling` before live model/runtime/parser verification.

## Client and offline release gates
- [ ] Open WebUI multi-turn, streaming, stop and multi-user acceptance.
- [ ] IDE workspace selection, approvals, diff review, build/test evidence and rollback.
- [ ] Public-internet-blocked clean workstation install.
- [ ] Versioned ZIP and SHA-256 manifest, backup/restore and rollback validation.
- [ ] Stage without restart; obtain explicit approval before service restart.

The current PR #12 CI run 43019014111 passed 117 tests with one Starlette/httpx TestClient deprecation warning. CI is source evidence only, not live DGX or IDE acceptance evidence.
