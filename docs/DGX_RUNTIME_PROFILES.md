# DGX Spark — three-model runtime profiles

**Target topology:** three resident MoE runtimes, one request slot per runtime, up to three independent Orchestrator tasks in parallel.

This document is a deployment design, not permission to mutate the host. Do not start/stop existing containers, download weights, or change services until the read-only preflight is reviewed.

## Selected models and measured GB10 starting points

| Runtime | Model | Weight footprint | GB10 speed reference |
|---|---|---:|---:|
| Fast reasoning | OpenAI GPT-OSS-20B MXFP4 | 12.8 GiB checkpoint | 96.2 tok/s on vLLM 0.31.0, 128K context |
| Coding | Qwen3-Coder-Next NVFP4 | about 43.22 GiB resident | 60.8 tok/s baseline; 72 tok/s with external EAGLE3 draft on vLLM 0.24.0 |
| Multimodal | Qwen3.6-35B-A3B NVFP4 | about 21–22 GiB | 78.1 tok/s without MTP; 102.3 tok/s with MTP on vLLM 0.31.0 |

Sources and qualifications:
- GPT-OSS is a 20.9B-total / 3.6B-active MoE with 32 experts, top-4 routing, native MXFP4 weights and 128K context. Its GB10 benchmark is a single-user run with four-user KV sizing; the reported 96.2 tok/s is not a three-model-concurrent measurement.
- Qwen3-Coder-Next is an 80B-total / roughly 3B-active MoE. The cited 72 tok/s result uses a 1-layer external EAGLE3 draft. The base decode result was about 60.8 tok/s. Its recipe reports 43.22 GiB resident weights and an 8 GiB KV cache budget for a single-user full-context configuration.
- Qwen3.6 is a multimodal 35B-total / 3B-active MoE. NVIDIA's vLLM recipe supports its NVFP4 checkpoint on GB10; the current GB10 recipe uses vLLM 0.28.0 or newer. vLLM 0.31.0 has a published 128K-context benchmark.

These are separate single-model measurements. They do not prove the three runtimes will fit or retain the same speeds when resident together.

## Runtime compatibility strategy

Use one isolated container/process per model because their best-known runtime baselines differ:

- **GPT-OSS-20B:** vLLM 0.31.0 baseline; OpenAI Harmony parser for tool-call mode if/when the Orchestrator implements tool calls.
- **Qwen3.6-35B-A3B:** vLLM 0.31.0 baseline; NVFP4 on GB10, FP8 KV cache, FlashInfer attention and Marlin MoE backend. NVIDIA's official NVFP4 recipe minimum is vLLM 0.28.0.
- **Qwen3-Coder-Next:** benchmark vLLM 0.24.0 / 0.25.1 versus the already-used cu130 nightly / 0.31 runtime. Community measurements found a decode regression in vLLM 0.26.0 for one NVFP4 checkpoint due to loss of the FlashInfer CUTLASS path; do not select an image solely because it is newest.

The actual installed image tags must be checked on the target host before writing the final Compose file. Do not assume a mutable `nightly` tag is reproducible.

## First-pass memory budget for a controlled trial

The following are **initial test caps**, not validated final allocations:

| Runtime | Weight estimate | Initial KV-cache cap | Initial max context | Max sequences |
|---|---:|---:|---:|---:|
| Coder | 43.22 GiB | 8 GiB | 65,536 | 1 |
| Qwen3.6 | 22 GiB | 4 GiB | 32,768 | 1 |
| GPT-OSS-20B | 12.8 GiB | 2 GiB | 65,536 | 1 |
| **Subtotal** | **78.02 GiB** | **14 GiB** | — | — |

The rough subtotal is 92.02 GiB before CUDA contexts, activations, graph/workspace allocations, container overhead, Open WebUI, Control Center, Code Intelligence, Astra Map and the OS. Approximately 29 GiB of the 121 GiB usable memory would remain in this arithmetic; actual runtime allocations must be measured.

Use vLLM's explicit `--kv-cache-memory-bytes` where supported. It gives a more precise KV budget than relying on `--gpu-memory-utilization` alone and takes precedence over that setting for KV sizing. The flag does not cap model weights or all runtime workspaces. The 8/4/2 GiB budgets must be adjusted only from measured startup and load-test data.

Start with one request per model (`max_concurrency: 1`, `--max-num-seqs 1`) and global Orchestrator parallelism 3. This means each runtime can serve one active inference while the other two serve their own requests; excess work queues rather than interrupting a model.

## Startup and validation sequence

1. Run `bash scripts/dgx_spark_model_preflight.sh`; inspect model directories, image tags, current ports, running containers and available memory.
2. Verify the GPT-OSS checkpoint exists locally. If it does not, stage it separately through the approved offline transfer process; do not download it as part of this preflight.
3. Verify each model's `config.json`, quantization metadata and tokenizer against the exact serving image.
4. Prepare three separate runtime profiles and ports only after those facts are known.
5. Start the smallest runtime first, then Coder and Qwen3.6, measuring `free -h`, `nvidia-smi`, per-process memory and health after each start. Keep every successfully started model resident during the test.
6. With all three loaded, test independent text calls and one concurrent call to each runtime. Then test the Orchestrator plan → Coder/Vision work → synthesis flow.
7. Record steady-state memory, peak memory, TTFT, decode throughput, queue delay, errors and thermal/power behavior.
8. Do not retire Flash until the three-model acceptance gates pass and the user explicitly approves its retirement.

## Read-only preflight

Run:

```bash
bash scripts/dgx_spark_model_preflight.sh
```

The script only reads host/model/container/port state and probes the existing local `/v1/models` endpoints. It does not change the machine.
