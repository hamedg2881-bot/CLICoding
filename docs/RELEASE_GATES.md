# CDS_AI release gates

**Product:** CDS_AI  
**Source repository:** https://github.com/hamedg2881-bot/CLICoding  
**Current integration candidate:** `feature/cds-ai-release-integration`  
**Deployment policy:** Source is completed and verified on GitHub first. DGX transfer and service restart are separate later stages.

## Implemented source capabilities
- [x] One public assistant identity: `CDS_AI`, with a shared policy applied to every model call.
- [x] Conversation-first direct chat; ordinary chat bypasses Planner.
- [x] Persistent conversation history and Open WebUI session/user mapping.
- [x] Direct-chat upstream SSE, streamed tool-call deltas and disconnect cleanup.
- [x] Specialist final-answer SSE with valid event framing.
- [x] OpenAI function tool-call round-trip, schema validation and pending-call correlation.
- [x] Workstation reference tool host with workspace scope, approval, idempotency and fixed commands.
- [x] Per-user bearer-token ownership isolation.
- [x] Exact retry idempotency for failed user/tool continuations.
- [x] Latest-turn regenerate and guarded edit/resubmit with history replacement.
- [x] Authenticated active-turn cancellation for direct and conversation API routes.
- [x] Versioned model-assisted context compaction for direct chat/tool turns; original events retained, latest three user turns preserved, summary invalidation on history rewrite.
- [x] Conservative context admission/output reservation; overflow is never silently truncated.

## Remaining source capabilities
- [x] Worker-specific specialist context compaction for planning, each specialist task, and final synthesis; mandatory task/request/result context is reserved before summarizing history, and canonical conversation events are not rewritten.
- [x] Specialist task lifecycle progress is emitted as structured `event: progress` SSE metadata alongside the final OpenAI-compatible completion stream; internal model reasoning and raw worker outputs are not exposed.
- [x] CI syntax-checks the packaged UI JavaScript and verifies the HTML asset is present in the built Python wheel.
- [x] Typed, conversation-scoped image attachment records, bounded binary upload, MIME sniffing, SHA-256 integrity checks, private storage and authenticated retrieval/deletion.
- [x] Offline SQLite and attachment backup/restore CLI with versioned manifest, integrity verification and retained pre-restore rollback paths.
- [ ] Automatic attachment retention/expiry, image decoding/content validation and malware scanning.
- [ ] Packaged IDE integration with workspace selection, approval, diff review/apply and Stop controls.
- [ ] Browser/Playwright/screenshot workflow and vision-based UI validation.
- [ ] Multi-call/parallel tool semantics, only after exact runtime capability verification.
- [ ] User-visible context inspection and summary correction controls.

## Security and operations
- [x] No arbitrary shell tool is advertised by the reference workstation tool host.
- [x] Workspace-relative paths, traversal/symlink rejection in covered tests.
- [x] Writes and build/test require explicit approval; tool idempotency ledger is durable.
- [x] Summary invalidation when edited/regenerated/deleted history intersects covered events.
- [ ] Close filesystem path-validation TOCTOU races with platform-appropriate safe APIs.
- [x] Recursively redact common credential-shaped values from workstation tool results before persistence/transmission.
- [ ] Broaden secret scanning for nonstandard credentials, connection strings and high-entropy values.
- [ ] OS-level process sandboxing and Windows/macOS/Linux adapter tests.
- [x] Durable per-principal API rate limits backed by SQLite transactions.
- [x] Administrator token issue/list/revoke API; only token hashes are stored and plaintext is returned once.
- [x] Paginated administrator-only audit export without request bodies or bearer-token values.
- [ ] Audit retention/export signing and alerting policy.
- [x] Serialize event-sequence allocation across SQLite connections with immediate write transactions.
- [x] Durable SQLite-backed cross-process conversation turn leases with atomic acquisition, periodic renewal, expiry recovery, and cancellation requests observed by the owning API process; direct streaming and buffered/specialist turn paths share the lease gate. The lease monitor fails closed on storage/renewal errors, and caller disconnects cancel the child turn.
- [x] Cross-process lease exclusion/recovery and two-app cancellation tests pass in CI; retain single-worker deployment until live supervisor acceptance is complete.
- [ ] Prove trusted Open WebUI headers are stripped/reinjected by the actual private proxy; otherwise use per-user tokens.

## Response quality acceptance

See [RESPONSE_QUALITY.md](RESPONSE_QUALITY.md) for the scoring rubric and evaluation protocol.

- [ ] Run a versioned Persian/English quality benchmark against exact DGX model aliases and serving parameters.
- [ ] Human-score factual reliability, completeness, context continuity, reasoning correctness, language/format and tool/action honesty.
- [ ] Verify long-context compaction preserves exact names, paths, versions, decisions and recent instructions.
- [ ] Verify partial specialist failures are clearly disclosed and never presented as complete success.
- [ ] Run coding/debugging/review cases with actual workstation build/test evidence.
- [ ] Run vision cases with real images and the exact multimodal runtime.
- [ ] Record latency, token usage and unified-memory headroom alongside quality scores.

## Live DGX runtime acceptance
- [ ] Verify exact served model aliases and ports at live `/v1/models` endpoints.
- [ ] Test GPT-OSS-20B chat/planning, the verified coding endpoint alias `qwen-coder` on port 8004, and Qwen3-VL-2B image understanding.
- [ ] Validate tool calling with the exact model/runtime/parser before enabling `tool_calling`.
- [ ] Measure context/output limits, timeout/cancellation, concurrency and unified-memory headroom.
- [ ] Verify routing, fallback and failure isolation without interrupting active requests.

The sample registry uses recorded ports 8003/8004/8005 and leaves all workers disabled. The coding model's served alias is `qwen-coder`; verify all aliases again during read-only preflight before enabling any worker.

## Client and offline release
- [ ] Open WebUI multi-turn, SSE, Stop, regenerate/edit and multi-user acceptance, including actual rendering or documented fallback for custom `x_clicoding.progress` SSE events.
- [ ] Real-model/DGX browser acceptance, broader mobile/keyboard/screen-reader accessibility, multi-user auth behavior, and error recovery against the production-like runtime.
- [ ] Packaged IDE workspace selection, approvals, diff review/apply and build/test evidence.
- [ ] Diagnose and repair a deliberately failing sample project; capture successful build/test exit codes.
- [ ] Browser screenshot, verified Vision handoff, visual correction and before/after evidence.
- [ ] Clean workstation installation with public internet blocked.
- [ ] Versioned ZIP and SHA-256 manifest, database/config backup, migration-on-copy and rollback validation.
- [ ] Stage without restart; obtain explicit approval before Orchestrator restart.
- [ ] Record exact commit, archive hash, deployed files, tests and rollback point.

CI is source-level evidence only. No hardware or client acceptance gate may be marked complete based solely on GitHub Actions.
