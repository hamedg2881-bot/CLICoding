# Three-model runtime topology

**Status:** Architecture/configuration baseline. The small/fast model identity and all served aliases/endpoints remain to be selected and verified by benchmark. This document does not authorize DGX changes.

## Goal

Replace the single Qwen3.8 Flash runtime as the central model with three specialist model runtimes behind one CLICoding Orchestrator endpoint. Users see one assistant and one continuous conversation; internal model handoffs are not exposed.

## Agreed model roles

| Runtime | Agreed model family / identity | Responsibility |
|---|---|---|
| Small/fast | Not selected yet; target class previously discussed was 4B–8B | Lightweight chat, structured planning proposal, and final synthesis |
| Coder | Qwen3-Coder-Next-NVFP4-GB10 | Source-code analysis, implementation-oriented reasoning, code review |
| Vision | Qwen3.6-VL-35B-A3B-NVFP4-DGX-Spark-VisionSafe | Screenshot/image understanding, UI inspection, visual findings |

Known DGX model directories:
- Coder: `/opt/ai/models/Qwen3-Coder-Next-NVFP4-GB10`
- Vision: `/opt/ai/models/Qwen3.6-VL-35B-A3B-NVFP4-DGX-Spark-VisionSafe`

The small/fast model's exact name and path have **not** been agreed. Do not infer them from the old Qwen3.8 Flash deployment or select one without benchmark evidence and user approval.

## Request flow

1. The Orchestrator receives the request and appends it to the canonical conversation history.
2. The small/fast runtime proposes a structured plan. The Orchestrator validates schema, task IDs, dependencies, cycles, and capability allow-lists; the model does not control dispatch directly.
3. The Orchestrator assigns coding tasks to Coder and image/screenshot tasks to Vision.
4. Independent tasks may execute in parallel within global and per-runtime limits. Dependent tasks wait for persisted prerequisite outputs.
5. Vision returns textual/structured findings. Coder receives those findings as context; it must not be sent raw image data unless the Coder runtime is independently verified as multimodal.
6. The small/fast runtime synthesizes successful task outputs into one user-facing answer. If synthesis is unavailable, the Orchestrator retains and returns the successful task outputs with an explicit limitation.
7. Conversation identity and history remain owned by the Orchestrator, not by any worker model.

## Resource and lifecycle policy

- Three physical model runtimes are the target topology; do not count logical capabilities as extra model instances.
- Start with per-model concurrency 1 and conservative global parallelism.
- Queue work when a required runtime is busy or unavailable; do not interrupt an active inference to make room.
- Model discovery/registration must not download, install, load, wake, sleep, or activate runtimes.
- Do not unload Flash until all three replacement runtimes have passed independent smoke tests and the end-to-end multi-model acceptance tests.
- No runtime lifecycle automation is implemented by this topology document. Lifecycle control must be a separately reviewed component with safe active-request accounting.

## Configuration contract

`config/workers.example.yaml` contains three disabled-by-default worker entries. The small/fast worker declares both `chat` and `planning`, so the same physical model can plan and synthesize. Coder and Vision each declare their specialist capability. Example URLs and served aliases are placeholders and must be replaced only after read-only endpoint discovery and independent verification.

## Exit gates before Flash retirement

- [ ] Exact small/fast model selected from benchmarked candidates.
- [ ] Actual OpenAI-compatible URL and served alias recorded for each of the three runtimes.
- [ ] Each runtime independently passes a harmless text request; Vision also passes a real image payload test.
- [ ] Planner returns valid structured plans for simple and complex multi-step requests.
- [ ] Coder and Vision handoff works; dependencies and partial failures are persisted correctly.
- [ ] Conversation continuity survives switching between all three runtimes.
- [ ] Resource measurements demonstrate safe headroom with the intended concurrency.
- [ ] Flash can be stopped only after explicit user approval and successful replacement acceptance.

