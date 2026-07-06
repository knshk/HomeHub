#!/usr/bin/env bash
# Bring up the whole local stack: Ollama (localhost-only) + the auth gateway (LAN).
# Rootless / no sudo. Idempotent: skips anything already running.
set -uo pipefail
# Privacy: disable third-party telemetry + run model libs offline (see privacy.env).
[ -f /home/kanishka/kk_works/LLMs/privacy.env ] && . /home/kanishka/kk_works/LLMs/privacy.env
ROOT="/home/kanishka/kk_works/LLMs/qwen-stack"
cd "$ROOT"
mkdir -p logs

# 1) Ollama — bound to localhost ONLY (the gateway is the network front door).
if curl -sf -m3 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  echo "ollama : already running (127.0.0.1:11434)"
else
  echo "ollama : starting on 127.0.0.1:11434"
  # OLLAMA_MAX_LOADED_MODELS: how many distinct models may stay resident at once.
  # Raised to 3 so the operator can run several managed models concurrently (from
  # the Home Hub "Models" admin tab). On 16 GB, keep the *set* of running models
  # within RAM — multiple 7B models will swap/OOM. Override via the env var.
  OLLAMA_HOST=127.0.0.1:11434 OLLAMA_KEEP_ALIVE=30m OLLAMA_NUM_PARALLEL=1 \
  OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-3}" \
    setsid bash -c '"$HOME/.local/bin/ollama" serve > "'"$ROOT"'/logs/ollama.log" 2>&1' </dev/null &
  disown 2>/dev/null || true
  sleep 5
fi

# 2) Gateway — bound to 0.0.0.0:8080, protected by API keys.
if curl -sf -m3 http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
  echo "gateway: already running (0.0.0.0:8080)"
else
  echo "gateway: starting on 0.0.0.0:8080"
  setsid bash -c 'cd "'"$ROOT"'" && exec ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 > "'"$ROOT"'/logs/gateway.log" 2>&1' </dev/null &
  disown 2>/dev/null || true
  sleep 3
fi

echo "---"
curl -sS -m5 http://127.0.0.1:8080/healthz; echo
IP="$(hostname -I | awk '{print $1}')"
echo "Apps endpoint :  http://$IP:8080/v1     (model: qwen2.5-7b)"
echo "Admin UI      :  http://$IP:8080/admin/"
echo "New API key   :  ./.venv/bin/python adminctl.py create --name <app-name>"
