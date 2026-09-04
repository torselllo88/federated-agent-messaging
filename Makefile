# Federated Agent Messaging — stable command surface.
# Frozen in testbed-architecture.md §32.
#
#   make setup     initialize environment and provisioned identities
#   make verify    environment + federation transport/bootstrap readiness
#   make e0        the frozen E0 procedure
#   make analyse   analysis and validation over result artifacts
#
# Every Python process runs inside the toolbox image (Python 3.12 frozen), so
# the host needs Docker only.
#
# Two containers, deliberately unequal:
#   bootstrap  privileged setup and verification; sees Synapse data and TLS
#   toolbox    runner and agent; no Synapse data, no database credentials
#
# `verify` runs in bootstrap because config hashes and rate-limit confirmation
# are environment-verifier responsibilities. The runner never reads Synapse
# configuration, and that restriction is part of the C2 evidence.

SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c

COMPOSE := docker compose --profile tools
RUN_TOOLBOX := $(COMPOSE) run --rm --no-deps toolbox
RUN_BOOTSTRAP := $(COMPOSE) run --rm --no-deps bootstrap

export FAM_PROTOCOL_GIT_COMMIT := $(shell git rev-parse HEAD 2>/dev/null || echo unknown)

.PHONY: help guard build tls config up wait provision hashes setup verify e0 analyse spike test down clean logs

help:
	@echo "make setup    - build, generate TLS and configs, start both domains, provision accounts"
	@echo "make verify   - environment and federation transport/bootstrap readiness"
	@echo "make spike    - development compatibility spike (Synapse / nio / room v12)"
	@echo "make e0       - run the frozen E0 procedure (3 independent runs)"
	@echo "make analyse  - digest verification, schema validation, E0 summary"
	@echo "make test     - unit tests"
	@echo "make down     - stop containers"
	@echo "make clean    - stop containers and delete all volumes (destructive)"

guard:
	@if [ -z "$${FAM_RESULTS_DIR:-}" ]; then
		echo "FAM_RESULTS_DIR is not set." >&2
		echo "  export FAM_RESULTS_DIR=/path/outside/this/repository" >&2
		exit 1
	fi
	@mkdir -p "$$FAM_RESULTS_DIR"

build: guard
	$(COMPOSE) build

tls: build
	$(RUN_BOOTSTRAP) python scripts/bootstrap.py tls

config: tls
	$(RUN_BOOTSTRAP) python scripts/bootstrap.py config

up: config
	$(COMPOSE) up -d postgres-a postgres-b synapse-a synapse-b

wait: up
	$(COMPOSE) run --rm bootstrap python scripts/bootstrap.py wait

provision: wait
	$(COMPOSE) run --rm bootstrap python scripts/bootstrap.py provision

hashes: provision
	$(COMPOSE) run --rm bootstrap python scripts/collect_environment.py

setup: hashes
	@echo
	@echo "setup complete. next: make verify && make spike && make e0"

verify: guard
	$(COMPOSE) run --rm bootstrap python scripts/verify_environment.py

spike: guard
	$(COMPOSE) run --rm toolbox python scripts/spike_compatibility.py

e0: guard
	$(COMPOSE) run --rm toolbox python experiments/e0_baseline.py

analyse: guard
	$(RUN_TOOLBOX) python scripts/analyse.py

test: build
	$(COMPOSE) run --rm --no-deps -e FAM_RESULTS_DIR=/tmp/fam-test-results toolbox \
		python -m pytest tests -q

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs --tail=200 synapse-a synapse-b
