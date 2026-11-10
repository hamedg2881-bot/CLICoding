#!/usr/bin/env bash
set -Eeuo pipefail

# Start the local CLICoding model containers sequentially.
# A model is considered ready only when its OpenAI-compatible /v1/models
# endpoint responds successfully.

log() {
  printf '[clicoding-models] %s\n' "$*"
}

start_and_wait() {
  local name="$1"
  local base_url="$2"
  local timeout_seconds="${3:-900}"
  local running
  local elapsed=0

  if ! docker inspect "$name" >/dev/null 2>&1; then
    log "ERROR: container '$name' does not exist."
    return 1
  fi

  running="$(docker inspect --format '{{.State.Running}}' "$name")"
  if [[ "$running" != "true" ]]; then
    log "Starting $name ..."
    docker start "$name" >/dev/null
  else
    log "$name is already running; checking readiness."
  fi

  log "Waiting for $name at $base_url/v1/models (timeout: ${timeout_seconds}s)."
  while (( elapsed < timeout_seconds )); do
    if curl --fail --silent --show-error --max-time 4 \
      "$base_url/v1/models" >/dev/null 2>&1; then
      log "$name is READY."
      return 0
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done

  log "ERROR: $name did not become ready within ${timeout_seconds}s."
  log "Last container state:"
  docker inspect --format 'status={{.State.Status}} running={{.State.Running}} exit={{.State.ExitCode}}' "$name" || true
  log "Recent container logs:"
  docker logs --tail 60 "$name" 2>&1 || true
  return 1
}

command -v docker >/dev/null 2>&1 || {
  log "ERROR: docker command not found."
  exit 1
}
command -v curl >/dev/null 2>&1 || {
  log "ERROR: curl command not found."
  exit 1
}

log "Beginning sequential model startup."

start_and_wait "gpt-oss-vllm" "http://127.0.0.1:8003" 900
start_and_wait "qwen-coder-vllm-024-test" "http://127.0.0.1:8004" 900
start_and_wait "qwen3-vl-2b-test" "http://127.0.0.1:8005" 900

log "All configured model endpoints are READY."
