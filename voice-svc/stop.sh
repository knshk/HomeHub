#!/usr/bin/env bash
# Stop the voice service (port-specific bracket pattern avoids self-match).
set -uo pipefail
pkill -f "[-]-port 8100" && echo "voice-svc: stopped" || echo "voice-svc: not running"
