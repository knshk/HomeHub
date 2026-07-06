#!/usr/bin/env bash
# Switch this box to IMAGE mode:
#   - stop the LLM stack (Ollama + gateway + voice) to free RAM
#   - start the FastSD CPU Image Studio (gradio) on :7860, LAN-bound
# The Home Hub (:8090) stays up the whole time; its chat/voice/photo tabs show
# an "offline" state, and the Images tab shows the studio.
set -uo pipefail
# Privacy: disable third-party telemetry + run model libs offline (see privacy.env).
[ -f /home/kanishka/kk_works/LLMs/privacy.env ] && . /home/kanishka/kk_works/LLMs/privacy.env
FS="/home/kanishka/kk_works/fastsdcpu"

kill_port() {
  local p=$1 pid
  pid=$(ss -ltnp 2>/dev/null | awk -v x=":$p\$" '$4 ~ x {print $NF}' \
        | grep -oE 'pid=[0-9]+' | grep -oE '[0-9]+' | head -1)
  [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "  stopped :$p (pid $pid)"
}

echo "Stopping LLM stack (freeing RAM)…"
kill_port 8100   # voice
kill_port 8080   # gateway
kill_port 11434  # ollama

if curl -sf -m2 http://127.0.0.1:7860/ >/dev/null 2>&1; then
  echo "Image Studio already running."
else
  echo "Starting Image Studio (FastSD CPU) on :7860…"
  GRADIO_SERVER_NAME=0.0.0.0 GRADIO_SERVER_PORT=7860 \
    setsid bash -c "source '$FS/env/bin/activate' && exec nice -n 10 python '$FS/src/app.py' -w >> '$FS/webui.log' 2>&1" \
    </dev/null >/dev/null 2>&1 &
  disown 2>/dev/null || true
  for i in $(seq 1 60); do curl -sf -m2 http://127.0.0.1:7860/ >/dev/null 2>&1 && break; sleep 2; done
fi

LAN=$(hostname -I | awk '{print $1}')
echo "Image mode ready."
echo "  Home Hub : http://llm.home   (open the Images tab)"
echo "  Studio   : http://${LAN}:7860"
echo "  Back to chat/voice later:  bash llm-mode.sh"
