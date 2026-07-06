# =============================================================================
# Home LLM Hub - Makefile
#
# Thin, rootless wrappers around the install / run / stop scripts plus a couple
# of convenience targets. All targets are safe to re-run.
#
#   make install   - create venv, install deps, init sqlite schema
#   make venv       - just (re)create the virtualenv
#   make run        - run the hub in the FOREGROUND (Ctrl-C to stop)
#   make start      - start in the background (logs/hub.log)
#   make stop       - stop the background hub
#   make claim      - mint the first admin device via the API (see vars below)
#   make clean      - remove venv, logs, and __pycache__ (KEEPS data/ + .env)
# =============================================================================

PROJECT_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
VENV         := $(PROJECT_ROOT)/.venv
PY           := $(VENV)/bin/python
UVICORN      := $(VENV)/bin/uvicorn
ENV_FILE     := $(PROJECT_ROOT)/.env

# Read HUB_HOST/HUB_PORT/HUB_*TOKEN from .env if present (override on CLI).
-include $(ENV_FILE)
HUB_HOST ?= 0.0.0.0
HUB_PORT ?= 8090

# Inputs for `make claim` (override on the command line):
#   make claim USER=mom TOKEN=hubboot-xxxxx
# NOTE: USER is also a shell env var (your login name). If it came from the
# environment (not the make command line), ignore it and use our default so
# the behavior is predictable. An explicit `make claim USER=...` still wins.
ifeq ($(origin USER),environment)
USER := admin
endif
USER  ?= admin
TOKEN ?= $(HUB_BOOTSTRAP_TOKEN)
HOST  ?= 127.0.0.1

.DEFAULT_GOAL := help
.PHONY: help venv install run start stop claim clean

help:
	@echo "Home LLM Hub make targets:"
	@echo "  make install   - venv + deps + sqlite schema (idempotent)"
	@echo "  make venv      - (re)create the virtualenv only"
	@echo "  make run       - run hub in FOREGROUND on :$(HUB_PORT)"
	@echo "  make start     - start hub in BACKGROUND (logs/hub.log)"
	@echo "  make stop      - stop the background hub"
	@echo "  make claim USER=<name> TOKEN=<admin-or-bootstrap-token>"
	@echo "  make clean     - remove venv/logs/pycache (keeps data/ and .env)"

venv:
	@test -x "$(PY)" || python3 -m venv "$(VENV)"
	@"$(PY)" -m pip install --upgrade pip >/dev/null
	@echo "venv ready at $(VENV)"

install:
	@bash "$(PROJECT_ROOT)/install.sh"

run:
	@test -x "$(UVICORN)" || { echo "Run 'make install' first."; exit 1; }
	cd "$(PROJECT_ROOT)" && "$(UVICORN)" app.main:app --host "$(HUB_HOST)" --port "$(HUB_PORT)" --reload

start:
	@bash "$(PROJECT_ROOT)/start-all.sh"

stop:
	@bash "$(PROJECT_ROOT)/stop-all.sh"

# Claim the first admin device: registers a device (capturing its cookie) then
# claims admin. Requires the hub to be running and a valid admin/bootstrap
# TOKEN. Stores the device cookie in logs/admin-cookies.txt for reuse.
claim:
	@test -n "$(TOKEN)" || { echo "ERROR: set TOKEN=<HUB_ADMIN_TOKEN or HUB_BOOTSTRAP_TOKEN>"; exit 1; }
	@mkdir -p "$(PROJECT_ROOT)/logs"
	@echo "==> Registering device for user '$(USER)' on http://$(HOST):$(HUB_PORT)"
	@curl -fsS -c "$(PROJECT_ROOT)/logs/admin-cookies.txt" \
	    -H "Content-Type: application/json" -H "X-Hub-CSRF: 1" \
	    -X POST "http://$(HOST):$(HUB_PORT)/api/session/register" \
	    -d '{"username":"$(USER)"}' >/dev/null
	@echo "==> Claiming admin for user '$(USER)'"
	@curl -fsS -b "$(PROJECT_ROOT)/logs/admin-cookies.txt" -c "$(PROJECT_ROOT)/logs/admin-cookies.txt" \
	    -H "Content-Type: application/json" -H "X-Hub-CSRF: 1" \
	    -X POST "http://$(HOST):$(HUB_PORT)/api/session/claim" \
	    -d '{"username":"$(USER)","admin_token":"$(TOKEN)"}'
	@echo ""
	@echo "==> Device cookie saved to logs/admin-cookies.txt (this device is now admin)."

clean:
	@echo "==> Removing venv, logs, and __pycache__ (data/ and .env are kept)"
	@rm -rf "$(VENV)"
	@rm -rf "$(PROJECT_ROOT)/logs"/*
	@find "$(PROJECT_ROOT)" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "==> Clean done."
