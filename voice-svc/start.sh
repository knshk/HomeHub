#!/usr/bin/env bash
# Start the local voice service (faster-whisper + kokoro-onnx) on 127.0.0.1:8100.
# Rootless, idempotent. The Home Hub proxies to it via VOICE_URL.
set -uo pipefail
# Privacy: disable third-party telemetry + run model libs offline (see privacy.env).
[ -f /home/kanishka/kk_works/LLMs/privacy.env ] && . /home/kanishka/kk_works/LLMs/privacy.env
ROOT="/home/kanishka/kk_works/LLMs/voice-svc"
cd "$ROOT"
mkdir -p logs
if curl -sf -m3 http://127.0.0.1:8100/healthz >/dev/null 2>&1; then
  echo "voice-svc: already running (127.0.0.1:8100)"
  exit 0
fi
echo "voice-svc: starting on 127.0.0.1:8100 (loads STT+TTS models, ~5s)"
setsid bash -c 'cd "'"$ROOT"'" && exec ./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8100 > logs/voice.log 2>&1' </dev/null &
disown 2>/dev/null || true
sleep 6
curl -sS -m5 http://127.0.0.1:8100/healthz && echo "  <- voice-svc up" || echo "  (not up yet — check logs/voice.log)"
