# DGX Spark live model validation — 2026-11-10

**Host:** `spark-7ea3` (DGX Spark GB10)  
**Purpose:** Record the current three-runtime experiment without silently changing the selected target architecture.  
**Evidence source:** User-supplied command output and live API tests in the CLICoding project conversation.  
**Safety:** This record does not authorize stopping, restarting, deleting, downloading, or reconfiguring any model/container.

## Architecture status

The repository's selected target remains:
- GPT-OSS-20B MXFP4 — fast reasoning/planning, text-only.
- Qwen3-Coder-Next NVFP4 — coding and code review.
- Qwen3.6-35B-A3B NVFP4 — selected multimodal/general model.

For memory/quality evaluation, the user chose to test **Qwen3-VL-2B-Instruct (BF16)** as a small Vision candidate first. This is an experiment, not an approved replacement of Qwen3.6-35B-A3B in the target architecture.

## Live runtime inventory

| Role | Model alias | Host endpoint | Runtime | Context cap | Sequences |
|---|---|---|---|---:|---:|
| Fast reasoning | `gpt-oss-20b` | `http://127.0.0.1:8003/v1` | vLLM 0.28.0 | 8,192 | 1 |
| Coding | `qwen-coder` | `http://127.0.0.1:8004/v1` | vLLM 0.24.0 | 8,192 | 1 |
| Vision experiment | `qwen3-vl-2b` | `http://127.0.0.1:8005/v1` | vLLM 0.28.0 | 4,096 | 1 |

Model directories:
- GPT-OSS: `/opt/ai/models/gpt-oss-20b`
- Coder: `/opt/ai/models/Qwen3-Coder-Next-NVFP4-GB10`
- Vision experiment: `/opt/ai/models/Qwen3-VL-2B-Instruct`

## Verified behavior

- GPT-OSS text completion: HTTP 200; exact readiness response `GPT-OSS-20B-READY`.
- GPT-OSS Persian response: a separate Persian prompt returned a multi-point response; one earlier request was truncated at its token limit.
- Qwen3-Coder-Next: actual vLLM 0.24.0 load succeeded; all 10 checkpoint shards loaded; basic coding tasks returned HTTP 200.
- GPT-OSS and Coder: concurrent real requests both returned HTTP 200 in the same test.
- Qwen3-VL-2B: model architecture resolved in vLLM 0.28.0; weights loaded; engine/API startup completed; `/v1/models` returned `qwen3-vl-2b`; text-only chat returned HTTP 200.
- The Vision text smoke test requested Persian but answered in English. Persian instruction following therefore needs explicit evaluation.
- At the latest recorded snapshot, all three runtimes were resident simultaneously and the Vision container was running without OOM. Available RAM was about 17 GiB; swap use about 4.5 GiB. Reported allocations were approximately 28,800 MiB (GPT-OSS), 56,195 MiB (Coder), and 7,001 MiB (Vision). These are point-in-time observations, not a sustained-load safety guarantee.

## Current limitations / not yet verified

- **No real image inference has been run against Qwen3-VL-2B yet.**
- OCR accuracy for Persian and English, screenshot understanding, UI element identification, coordinate grounding, and error-dialog analysis remain unverified.
- No live Orchestrator-to-three-runtime end-to-end request has been run against these host endpoints.
- Open WebUI conversation-ID preservation and streaming remain unverified.
- Three-way concurrent inference with representative prompts and peak-memory/headroom measurements remains unverified.
- The 2B Vision candidate has not been accepted as the final multimodal model.

## Evaluation sequence

1. Keep all current model contexts and runtime settings unchanged while collecting Vision image-quality evidence.
2. Run a real screenshot through the Vision endpoint and evaluate exact OCR, language adherence, UI structure/controls, and error interpretation.
3. Confirm that image bytes are sent only to Vision and that any downstream Coder task receives structured textual findings, not the raw image.
4. Measure memory and latency under independent requests and a three-runtime concurrent request.
5. Only after evidence is collected, decide whether to retain the 2B candidate, test a larger Vision model, or revise context/KV budgets.
6. Update the target architecture only after an explicit model-selection decision.

## Context policy during this experiment

The current context caps (8K GPT-OSS, 8K Coder, 4K Vision) are intentionally conservative smoke-test settings, not final capacity targets. Do not increase them based only on advertised maximum context lengths. Any increase must be based on measured KV-cache allocation, available unified memory, swap behavior, and representative concurrent load.

No model files, containers, or images were deleted. Existing Flash assets/runtime were not part of this test and must remain untouched pending separate acceptance and explicit user approval.
