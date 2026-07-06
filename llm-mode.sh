#!/usr/bin/env bash
# Switch this box to LLM mode:
#   - stop the FastSD CPU Image Studio (free RAM)
#   - start the LLM stack (Ollama + gateway + voice)
# The Home Hub (:8090) stays up throughout; chat/voice/photo come back online.
set -uo pipefail

kill_port() {
  local p=$1 pid
  pid=$(ss -ltnp 2>/dev/null | awk -v x=":$p\$" '$4 ~ x {print $NF}' \
        | grep -oE 'pid=[0-9]+' | grep -oE '[0-9]+' | head -1)
  [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "  stopped :$p (pid $pid)"
}

echo "Stopping Image Studio…"
kill_port 7860

echo "Starting LLM stack (Ollama + gateway)…"
bash /home/kanishka/kk_works/LLMs/qwen-stack/start-all.sh >/dev/null 2>&1 || true
echo "Starting voice service…"
bash /home/kanishka/kk_works/LLMs/voice-svc/start.sh >/dev/null 2>&1 || true

for i in $(seq 1 40); do curl -sf -m2 http://127.0.0.1:8080/healthz >/dev/null 2>&1 && break; sleep 1; done

echo "LLM mode ready."
for p in 11434 8080 8090 8100; do
  printf "  :%-5s " "$p"
  curl -sf -m4 "http://127.0.0.1:$p/$([ "$p" = 11434 ] && echo api/version || echo healthz)" >/dev/null 2>&1 && echo up || echo DOWN
done
echo "  Home Hub : http://llm.home"
echo "  Back to images later:  bash image-mode.sh"
