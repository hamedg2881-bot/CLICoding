# Three-model MoE runtime topology

**Status:** Model identities and roles selected. All three runtimes are intended to stay loaded concurrently. Endpoint aliases, runtime compatibility, actual resident memory, and end-to-end behavior remain deployment verification gates. This document does not itself authorize host mutation.

## Selected models

| Runtime | Selected model | Architecture | Responsibility |
|---|---|---|---|
| Fast reasoning | OpenAI GPT-OSS-20B MXFP4 | MoE; 20.9B total / 3.6B active | Lightweight reasoning, planning, chat, final synthesis, tool selection |
| Coding | Qwen3-Coder-Next NVFP4 | MoE; 80B total / 3B active | Repository/code analysis, coding, multi-file implementation reasoning, review |
| Multimodal general | Qwen3.6-35B-A3B NVFP4 | MoE; 35B total / 3B active | General reasoning, image understanding, screenshot/UI review, multimodal tasks |

The Qwen3.6 model is not a vision-only model: it owns both general chat and vision capabilities. GPT-OSS is text-only; image payloads must be routed to Qwen3.6.

## Runtime residency and concurrency

- All three physical model runtimes remain loaded simultaneously; model switching/unloading is not part of normal request routing.
- Each runtime starts with `max_concurrency: 1`. The Orchestrator can execute independent tasks in parallel, up to the global task limit and each worker's own semaphore.
- Initial target is up to three independent model tasks in parallel, with at most one active request per runtime. Global parallelism must be configured to 3 and validated under real workload.
- Do not assume that the sum of on-disk model sizes equals resident runtime memory. Measure weight allocation, KV cache, CUDA graphs, workspace, host/system memory and other active services while all three are loaded.
- Runtime-level memory/KV limits must be configured independently for each server process. Avoid assigning each server a percentage of total unified memory that causes aggregate overcommit.
- Queue requests when a worker is at its concurrency limit. Never unload or interrupt a model to admit another request.
- Flash remains available during migration. Retire it only after all three selected models are resident, independently tested, and pass the multi-model acceptance tests, followed by explicit user approval.

## Request flow

1. Orchestrator persists the user turn in the canonical conversation.
2. GPT-OSS proposes the smallest useful structured plan; local code validates schema, dependencies, cycles, and capability allow-lists.
3. The Orchestrator routes coding/review tasks to Qwen3-Coder-Next.
4. General text and image tasks route to Qwen3.6; raw image content must reach its multimodal endpoint, not a local file path.
5. Independent tasks may run concurrently across the three already-resident runtimes. Dependency outputs are supplied to downstream tasks.
6. GPT-OSS synthesizes successful outputs into one user-facing answer.
7. Conversation identity, event history, execution state, and model handoffs remain owned by the Orchestrator.

## Configuration contract

`config/workers.example.yaml` declares exactly three disabled-by-default worker records:
- `fast-reasoning`: `chat` + `planning`.
- `coding-primary`: `coding` + `review`.
- `multimodal-primary`: `chat` + `vision`.

The example endpoint ports and aliases are placeholders, not claims about live services. Replace them only after read-only discovery of the actual runtime endpoints and served aliases. Enabling a registry entry does not load or start a model.

## Current implementation gaps

- [x] OpenAI-compatible base64 image data-URI transport to Qwen3.6 is implemented and covered by API/orchestration tests; live runtime validation remains pending.
- [x] Configurable global execution parallelism defaults to 3 and is wired through `CLICODING_MAX_PARALLEL_TASKS`.
- [ ] Runtime readiness/health checks and clear unavailable-worker errors.
- [ ] Real DGX measurements with all three model processes resident simultaneously.
- [ ] Open WebUI conversation-ID mapping and streaming support.
- [x] Unit/integration tests cover the selected worker roles, three-way execution, and image-only handoff to the multimodal worker; real model endpoint tests remain pending.

## Flash retirement gates

- [ ] All three models are loaded at the same time and remain resident during concurrent requests.
- [ ] Each runtime passes independent text smoke tests; Qwen3.6 also passes a real image payload test.
- [ ] A mixed plan dispatches to all three workers and preserves dependencies/results.
- [ ] Resource headroom is measured under simultaneous residency and representative parallel inference.
- [ ] Conversation continuity and final synthesis are verified.
- [ ] User explicitly approves Flash retirement.
