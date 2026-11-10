# DGX Spark model auto-start

This deployment starts the three CLICoding model containers sequentially after Docker is ready. It waits for each OpenAI-compatible endpoint before starting the next model.

## Model order

1. `gpt-oss-vllm` — `http://127.0.0.1:8003/v1/models`
2. `qwen-coder-vllm-024-test` — `http://127.0.0.1:8004/v1/models`
3. `qwen3-vl-2b-test` — `http://127.0.0.1:8005/v1/models`

Each model gets up to 15 minutes to become ready. If a container is already running, the script does not restart it; it only checks readiness. If a model fails, startup stops and later models are not started.

## Install on DGX Spark

Copy these two files from the repository to the DGX, then run:

```bash
sudo install -o root -g root -m 0755 \
  clicoding-models-startup.sh \
  /usr/local/sbin/clicoding-models-startup

sudo install -o root -g root -m 0644 \
  clicoding-models.service \
  /etc/systemd/system/clicoding-models.service

sudo systemd-analyze verify /etc/systemd/system/clicoding-models.service
sudo systemctl daemon-reload
sudo systemctl enable clicoding-models.service
sudo systemctl start clicoding-models.service
```

Check progress and status:

```bash
sudo systemctl status clicoding-models.service --no-pager
sudo journalctl -u clicoding-models.service -f
```

The service is a one-shot startup coordinator. It does not stop models when the service is stopped, and it does not change Docker restart policies. Docker remains responsible for stopping containers during system shutdown.

Do not enable Docker restart policies on these three containers in parallel; that could start all models concurrently and create avoidable unified-memory pressure.
