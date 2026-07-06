# =============================================================================
# qwen-stack Makefile
#
# Common operator tasks. The venv lives at ./.venv; all Python commands run
# through it so no global activation is required.
#
#   make install            full setup (venv + deps + db schema)
#   make venv               create the virtualenv only
#   make run                run the gateway in the foreground
#   make key NAME=laptop    create an API key via the admin API
#   make test               run the test suite
#   make clean              remove venv and Python caches
# =============================================================================

# Absolute project root (directory of this Makefile).
ROOT        := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
VENV        := $(ROOT)/.venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
UVICORN     := $(VENV)/bin/uvicorn
PYTEST      := $(VENV)/bin/pytest

# Gateway bind (kept in sync with the contract / .env defaults).
HOST        ?= 0.0.0.0
PORT        ?= 8080

# Admin endpoint used by the `key` target. Talk to the local loopback.
ADMIN_URL   ?= http://127.0.0.1:$(PORT)

.PHONY: help venv install run key test clean

help:
	@echo "Targets:"
	@echo "  make install            full setup (venv + deps + sqlite schema)"
	@echo "  make venv               create the virtualenv only"
	@echo "  make run                run the gateway (uvicorn) in the foreground"
	@echo "  make key NAME=laptop    create an API key (gateway must be running)"
	@echo "  make test               run the test suite"
	@echo "  make clean              remove venv and caches"

# Create the virtualenv (idempotent).
venv:
	@test -x "$(PY)" || python3 -m venv "$(VENV)"
	@echo "venv ready at $(VENV)"

# Full installation via the install script (venv, deps, data dir, schema).
install:
	@bash "$(ROOT)/install.sh"

# Run the gateway in the foreground.
run:
	"$(UVICORN)" app.main:app --host $(HOST) --port $(PORT)

# Create a new API key. Usage: make key NAME=laptop
# Requires the gateway to be running and ADMIN_TOKEN exported (or in .env).
key:
	@test -n "$(NAME)" || { echo "ERROR: NAME is required, e.g. make key NAME=laptop"; exit 1; }
	@TOKEN="$${ADMIN_TOKEN:-$$(grep -E '^ADMIN_TOKEN=' "$(ROOT)/.env" 2>/dev/null | tail -n1 | cut -d= -f2-)}"; \
	test -n "$$TOKEN" || { echo "ERROR: ADMIN_TOKEN not set (export it or put it in .env)"; exit 1; }; \
	curl -fsS "$(ADMIN_URL)/admin/keys" \
		-H "Authorization: Bearer $$TOKEN" \
		-H "Content-Type: application/json" \
		-d "{\"name\":\"$(NAME)\"}"; \
	echo

# Run tests (no-op-friendly if pytest or tests are absent).
test:
	@if [ -x "$(PYTEST)" ]; then \
		"$(PYTEST)" -q "$(ROOT)/tests"; \
	else \
		echo "pytest not installed; run: $(PIP) install pytest" >&2; exit 1; \
	fi

# Remove the venv and Python bytecode caches. Leaves data/ and .env intact.
clean:
	rm -rf "$(VENV)"
	find "$(ROOT)" -path "$(VENV)" -prune -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find "$(ROOT)" -type f -name '*.pyc' -delete 2>/dev/null || true
	@echo "cleaned venv and caches"
