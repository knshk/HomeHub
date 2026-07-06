#!/usr/bin/env bash
# Stop the gateway and Ollama. Bracket patterns avoid pkill self-matching.
set -uo pipefail
echo "stopping gateway..."; pkill -f "[u]vicorn app.main:app" && echo "  gateway stopped" || echo "  gateway not running"
echo "stopping ollama...";  pkill -f "[o]llama serve"        && echo "  ollama stopped"  || echo "  ollama not running"
