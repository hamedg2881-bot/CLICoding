#!/usr/bin/env bash
set -euo pipefail

# Read-only inventory for the three-model CLICoding deployment.
# This script does not start/stop containers, download models, or modify files.

MODEL_ROOT="${MODEL_ROOT:-/opt/ai/models}"
MODELS=(
  "Qwen3-Coder-Next-NVFP4-GB10"
  "Qwen3.6-VL-35B-A3B-NVFP4-DGX-Spark-VisionSafe"
  "gpt-oss-20b"
)

echo "===== HOST ====="
date -Is
hostname
uname -a
uname -m
python3 --version
echo

echo "===== MEMORY / STORAGE ====="
free -h
df -h / "$MODEL_ROOT" 2>/dev/null || df -h /
echo

echo "===== NVIDIA ====="
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi
else
  echo "nvidia-smi not found"
fi
echo

echo "===== MODEL DIRECTORIES ====="
for name in "${MODELS[@]}"; do
  path="$MODEL_ROOT/$name"
  echo "--- $name ---"
  if [[ ! -d "$path" ]]; then
    echo "MISSING: $path"
    continue
  fi
  du -sh "$path"
  if [[ -f "$path/config.json" ]]; then
    python3 - "$path/config.json" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
try:
    d = json.loads(p.read_text())
except Exception as exc:
    print(f"config.json parse error: {exc}")
    raise SystemExit(0)
for key in ("model_type", "architectures", "torch_dtype", "max_position_embeddings",
            "num_hidden_layers", "num_experts", "num_local_experts",
            "quantization_config", "text_config", "vision_config"):
    if key in d:
        value = d[key]
        if key in ("text_config", "vision_config") and isinstance(value, dict):
            value = {k: value[k] for k in ("model_type", "architectures",
                     "max_position_embeddings", "num_hidden_layers") if k in value}
        print(f"{key}: {value}")
PY
  else
    echo "config.json: MISSING"
  fi
done
echo

echo "===== DOCKER IMAGES ====="
if command -v docker >/dev/null 2>&1; then
  docker images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}' \
    | grep -Ei 'REPOSITORY|vllm|dynamo|qwen|gpt|spark' || true
  echo
  echo "===== RUNNING CONTAINERS ====="
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
else
  echo "docker not found"
fi
echo

echo "===== LISTENING PORTS ====="
ss -lntp 2>/dev/null | grep -E ':(3000|5090|8000|8001|8002|8100|8400|8500)\b' || true
echo

echo "===== MODEL API DISCOVERY ====="
for port in 8000 8001 8002; do
  echo "--- http://127.0.0.1:$port/v1/models ---"
  curl --max-time 3 -sS "http://127.0.0.1:$port/v1/models" || true
  echo
done

echo "===== END READ-ONLY PREFLIGHT ====="
