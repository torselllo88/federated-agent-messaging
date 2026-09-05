# Federated Agent Messaging — stable command surface.
# Frozen in testbed-architecture.md §32.
#
#   make setup     initialize environment and provisioned identities
#   make verify    environment + federation transport/bootstrap readiness
#   make e0        the frozen E0 procedure
#   make e3-pilot  development E3 pilot
#   make e3        the development E3 campaign
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

.PHONY: help guard build tls config up wait provision hashes setup verify e0 e1 e2 e2-pilot e3-readiness e3-pilot e3 analyse spike test down clean logs

help:
	@echo "make setup    - build, generate TLS and configs, start both domains, provision accounts"
	@echo "make verify   - environment and federation transport/bootstrap readiness"
	@echo "make spike    - development compatibility spike (Synapse / nio / room v12)"
	@echo "make e0       - run the frozen E0 procedure (3 independent runs)"
	@echo "make e1       - run the frozen E1 procedure (3 independent federated runs)"
	@echo "make e2-pilot - development pilot: select the E2 sync timeline limit"
	@echo "make e2       - run the frozen E2 procedure (3 independent recovery runs)"
	@echo "make e3-readiness - live gap recovery under bounded-concurrency stress"
	@echo "make e3-pilot - development E3 pilot: benchmark mechanics and sync limit"
	@echo "make e3      - the development E3 campaign (120 paired benchmark runs)"
	@echo "make analyse  - digest verification, schema validation, E0-E3 summaries"
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

# E1 does not rerun E0.
e1: guard
	$(COMPOSE) run --rm toolbox python experiments/e1_federation.py

e2-pilot: guard
	$(COMPOSE) run --rm toolbox python scripts/e2_pilot.py

# E2 reruns neither E0 nor E1.
e2: guard
	$(COMPOSE) run --rm -e FAM_E2_TIMELINE_LIMIT toolbox python experiments/e2_recovery.py

# Transport readiness. Runs no other experiment and measures no performance.
e3-readiness: guard
	$(COMPOSE) run --rm -e FAM_READINESS_REQUESTS -e FAM_READINESS_CONCURRENCY \
		-e FAM_READINESS_TIMELINE_LIMIT toolbox python experiments/e3_readiness.py

# Development E3 pilot: benchmark mechanics, sync limit, stationarity.
# Not an E3 repetition and not publication evidence.
e3-pilot: guard
	$(COMPOSE) run --rm -e FAM_E3_TIMELINE_LIMIT -e FAM_E3_SYNC_TIMEOUT_MS \
		-e FAM_E3_PILOT_LATENCY_WARMUP -e FAM_E3_PILOT_LATENCY_MEASURED \
		-e FAM_E3_PILOT_WARMUP_S -e FAM_E3_PILOT_MEASUREMENT_S -e FAM_E3_PILOT_DRAIN_S \
		toolbox python scripts/e3_pilot.py

# The development E3 campaign. Runs no other experiment, and resumes a
# partially completed campaign instead of restarting it.
e3: guard
	$(COMPOSE) run --rm -e FAM_E3_SCHEDULE_SEED -e FAM_E3_TIMELINE_LIMIT \
		-e FAM_E3_SYNC_TIMEOUT_MS -e FAM_E3_BLOCKS -e FAM_E3_WORKLOADS \
		toolbox python experiments/e3_benchmark.py

analyse: guard
	$(COMPOSE) run --rm -e FAM_E3_BOOTSTRAP_REPLICATES -e FAM_E3_BOOTSTRAP_SEED \
		--no-deps toolbox python scripts/analyse.py

test: build
	$(COMPOSE) run --rm --no-deps -e FAM_RESULTS_DIR=/tmp/fam-test-results toolbox \
		python -m pytest tests -q

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs --tail=200 synapse-a synapse-b
