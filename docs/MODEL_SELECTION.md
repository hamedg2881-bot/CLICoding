# Model Selection Strategy — DGX Spark

Status: provisional architecture decision; final acceptance requires the local CLICoding benchmark suite.

Date: 2026-11-09

## Objective

Select the smallest practical model set that gives the best end-to-end results for offline .NET agentic development: repository understanding, multi-file edits, tool calling, build/test repair loops, and screenshot-based UI review.

## Current host evidence

From the user's DGX Spark report:
- NVIDIA GB10 / Blackwell, 128 GiB unified memory class, Ubuntu 24.04.5, CUDA 13.0.
- 121 GiB reported system memory; 106 GiB used and 15 GiB available at capture.
- Swap: 2.3 GiB used of 15 GiB.
- A vLLM EngineCore process held 97,383 MiB GPU/unified memory.
- Running container: `qwen38-flash` using `eugr/spark-vllm-b12x:nightly-20261015`.
- Local model directories: Qwen3.6-VL-35B-A3B-NVFP4-DGX-Spark-VisionSafe, Qwen3.8-27B-NVFP4, Qwen3.8-Flash-Next-FP4-Local, Qwen3-Coder-Next-NVFP4-GB10, qwen3-vl-8b-vllm.

## Recommended model policy

### Primary: Qwen3.8-Flash-Next-FP4-Local

Keep as the leading candidate and first model to benchmark for the main coding agent. Public single-DGX-Spark recipe evidence reports 125B total / 6B active parameters, vision input, tool calling, and agentic-coding speed measurements. The reported optimized recipe reaches 65.3 tokens/s on its agentic coding decode test and completes its 92-turn benchmark in 693 seconds versus 1,161 seconds for the compared stock-vLLM setup. These are recipe-specific results, not guarantees for the currently installed eugr image or the user's workload.

Do not assume it should remain resident continuously. The current capture shows only 15 GiB available system memory while its vLLM process holds about 97 GiB. Use lifecycle-controlled loading/unloading and protect a host-memory reserve. Do not force-unload an in-flight request.

### Keep as on-disk challengers during evaluation

- Qwen3.6-VL-35B-A3B-NVFP4: retain temporarily as the vision/UI challenger. It supports text, image, video, up to 262K context, and Blackwell. Keep only if screenshot interpretation, visual defect finding, or UI iteration tests beat Flash-Next materially.
- Qwen3.8-27B-NVFP4: retain temporarily as a smaller fallback/challenger. It supports text, image, video and up to 262K context on Blackwell. Keep only if it provides a useful quality/latency/memory tradeoff or materially better reliability.
- Qwen3-Coder-Next-NVFP4-GB10: retain temporarily as the coding-specialist challenger. Keep only if it beats Flash-Next on repository-level .NET tasks, tool discipline, patch correctness, and build/test repair.

### Candidate to remove from the active model set

- qwen3-vl-8b-vllm: do not make it a production model for high-quality coding/UI work unless it proves useful as a genuinely fast low-cost vision utility. It is likely redundant if Flash-Next or Qwen3.6-VL passes the visual suite. No deletion of weights is authorized by this decision.

### Additions

Do not download or add another model yet. First benchmark the five local candidates on the same tasks and runtime conditions. The current host has insufficient free memory to justify another large model being loaded alongside Flash-Next.

## Runtime policy

- Only one large model should be resident by default on this 128 GiB unified-memory host until measured safe headroom proves otherwise.
- Model files may remain on NVMe while inactive.
- Use queueing and safe lifecycle transitions; never interrupt an in-flight request to swap models.
- Preserve a conservative host reserve for OS, Docker, Open WebUI, Agent, browser/test tools, and build workloads.
- Do not delete model weights until the comparison is complete and the user approves cleanup.

## Required acceptance benchmark

Run identical prompts, context limits, sampling settings, and tool schemas for each candidate. Measure:
1. .NET solution discovery and architecture summary fidelity.
2. Multi-file feature implementation with explicit acceptance criteria.
3. Bug diagnosis and repair after real `dotnet build` / `dotnet test` output.
4. Tool calling: correct tool choice, valid arguments, no fabricated tool results, safe refusal/approval boundaries.
5. Long-context retrieval across a real repository.
6. Screenshot/UI critique and actionable CSS/Razor changes.
7. End-to-end completion rate, number of iterations, patch quality, latency, tokens/s, peak unified memory, and swap growth.

Select the primary by end-to-end task success and patch correctness, not by tokens/s or synthetic coding scores alone.

## External evidence

- NVIDIA Qwen3.8-Flash-Next FP4-Local DGX Spark recipe and its recipe-specific benchmark/multimodal/tool support: https://huggingface.co/diffbot/Qwen3.8-Flash-Next-FP4-Local-DGX-Spark
- NVIDIA Qwen3.8-27B-NVFP4 model card (modalities, context, Blackwell/vLLM support and published evaluations): https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4
- NVIDIA Qwen3.6-35B-A3B-NVFP4 model card (modalities, context and Blackwell support): https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4

## Current decision

Keep Qwen3.8-Flash-Next as the primary candidate; do not add models; do not delete any weights yet. Keep Qwen3.6-VL, Qwen3.8-27B, and Qwen3-Coder-Next on disk only until comparative tests determine whether any deserve a secondary role. Defer qwen3-vl-8b-vllm cleanup until the same test suite confirms it is redundant.


## Superseding architecture direction — concurrent resident models (2026-11-09)

The earlier single-primary / swap-on-demand recommendation is superseded by the user's explicit preference: the required models should remain resident and available simultaneously. Model switching is not the preferred normal operating mode.

The user also rejects Qwen3.8-27B Dense as a primary candidate because of its expected low decode speed. Do not recommend it as a preferred production model unless new local measurements overturn that constraint.

### Leading two-model configuration to validate

1. **Coding specialist: Qwen3-Coder-Next 80B-A3B NVFP4 (GB10 build).**
   - Community GB10 checkpoint reports about 43.22 GiB resident weights, 80B total / 3B active, 12 full-attention layers out of 48, FP8 KV cache, native 262K context, and tool-call parser support.
   - A separate GB10 measurement reports about 61 tok/s without speculative decoding; results depend on checkpoint/runtime.
   - References: https://huggingface.co/gdubicki/qwen3-coder-next and https://howtospark.com/recipes/qwen3-coder-next-nvfp4

2. **Vision + general model: Qwen3.6-35B-A3B NVFP4.**
   - Official vLLM recipe lists a 21 GB minimum VRAM profile for NVFP4, explicitly supports DGX Spark GB10, and recommends FP8 KV cache, max context 262K, max 8 sequences, and GPU-memory-utilization 0.5 for the Spark profile.
   - This model supplies image understanding that Nemotron 3.5 Lightning does not provide.
   - Reference: https://github.com/vllm-project/recipes/blob/main/models/Qwen/Qwen3.6-35B-A3B.yaml

### Competing candidate: Nemotron 3.5 Lightning 30B-A3B NVFP4

- NVIDIA lists about 30 GB unified memory before KV-cache headroom on DGX Spark.
- It is a text-only model, optimized for agentic workflows, coding and tool use; NVIDIA reports SWE-bench Verified 52.80 for the NVFP4 checkpoint.
- It is a strong alternative general/agentic worker, but it does not replace the Vision role. Pairing it with Qwen3-Coder-Next consumes more memory than the Qwen3.6 NVFP4 minimum and still leaves Vision uncovered.
- References: https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/modelcard and https://build.nvidia.com/playbooks/nim-llm/overview

### Preliminary memory arithmetic — not a fit guarantee

- Qwen3-Coder-Next: approximately 43.22 GiB resident model weights in the cited GB10 recipe; that recipe separately caps KV cache at 8 GiB for a single-user full-context configuration.
- Qwen3.6-35B-A3B: official recipe's minimum VRAM field is 21 GB (not a complete measured resident-memory figure).
- Combined model-weight / minimum-footprint starting point: roughly 64 GB-class, before both runtimes' allocations, KV caches, CUDA graphs, activations, concurrency, OS, Docker, Open WebUI, Gateway, and other existing services.
- Nemotron 3.5 Lightning requires roughly 30 GB on DGX Spark before KV cache headroom. It is text-only.
- The Qwen3.8 Flash-Next recipe in the current vLLM recipes lists a 130 GB minimum VRAM requirement for its default NVFP4 variant; this confirms it is not a sensible always-resident member of a multi-model set on a 128 GB Spark alongside other models. Custom community recipes are not equivalent to official supported memory profiles.
- Reference: https://github.com/vllm-project/recipes/blob/main/models/Qwen/Qwen3.8-Flash-Next.yaml

### Provisional recommendation

Validate **Qwen3-Coder-Next + Qwen3.6-35B-A3B** as the preferred concurrent pair. Do not download or remove anything yet. Nemotron 3.5 Lightning remains a challenger to compare against Qwen3.6 for text-only agentic/general work, but not a Vision substitute.

### Required next measurements

Before deployment, obtain a fresh read-only snapshot of total / available RAM and swap; resident processes and per-container memory; currently loaded model runtime and its memory use; exact on-disk sizes of candidate checkpoints; and existing services that must remain available.

Then test the pair with explicit per-model KV-cache budgets, conservative context/concurrency limits, and an OS/service reserve. Do not rely on each vLLM instance independently setting gpu-memory-utilization to 0.5: both instances share the same unified-memory pool, so independent percentages can overcommit the host.
