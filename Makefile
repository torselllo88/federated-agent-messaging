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

.PHONY: help guard build tls config up wait provision hashes setup verify e0 e1 e2 e2-pilot e3-readiness e3-pilot e3 e4-prepare e4-ca e4 e4-validate inventory lock lock-validate lock-check freeze figures analyse spike test down clean logs

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
	@echo "make e4-prepare - check E4 readiness and print human-client details"
	@echo "make e4-ca    - print the research CA for the human client trust store"
	@echo "make e4       - run ONE human-driven E4 session (interactive)"
	@echo "make e4-validate - validate the recorded E4 sessions"
	@echo "make inventory - testbed configuration inventory, an input to the lock"
	@echo "make lock     - generate the formal protocol lock (once, before collection)"
	@echo "make lock-validate - check a lock before it is committed and tagged"
	@echo "make lock-check - full precondition check: lock, commit and tag agree"
	@echo "make freeze   - close the collection phase: gate, inventory, archive digest"
	@echo "make figures  - publication figures as SVG from the processed tables"
	@echo "make analyse  - digest verification, schema validation, E0-E3 summaries"
	@echo "make test     - unit tests"
	@echo "make down     - stop containers"
	@echo "make clean    - stop containers and delete all volumes (destructive)"

TAG ?= protocol-v1.2
SUPERSEDES ?=
SUPERSEDES_REASON ?=

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

# FAM_E4_CS_TLS_PORT is propagated here because it is rendered into
# `public_baseurl`: a client is told this address after login and uses it for
# everything afterwards, so it must match the port compose actually publishes.
config: tls
	$(COMPOSE) run --rm --no-deps -e FAM_E4_CS_TLS_PORT bootstrap python scripts/bootstrap.py config

up: config
	$(COMPOSE) up -d postgres-a postgres-b synapse-a synapse-b

wait: up
	$(COMPOSE) run --rm bootstrap python scripts/bootstrap.py wait

provision: wait
	$(COMPOSE) run --rm bootstrap python scripts/bootstrap.py provision

# Resolved here rather than in a container: only the host can ask Docker what
# it actually pulled. Both the environment manifest and the inventory read the
# result, so it is produced before either.
image-digests: guard
	mkdir -p "$$FAM_RESULTS_DIR/environment"
	{
	  echo "{"
	  first=1
	  for image in $$($(COMPOSE) config --images | sort -u); do
	    digest=$$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}' "$$image" 2>/dev/null || echo unresolved)
	    if [ $$first -eq 0 ]; then echo ","; fi
	    printf '  "%s": "%s"' "$$image" "$$digest"
	    first=0
	  done
	  echo
	  echo "}"
	} > "$$FAM_RESULTS_DIR/environment/image-digests.json"

hashes: provision image-digests
	$(COMPOSE) run --rm bootstrap python scripts/collect_environment.py

setup: hashes
	@echo
	@echo "setup complete. next: make verify && make spike && make e0"

# Preconditions every formal run is gated on (Task 07 §23). Read here, on the
# host that can see the repository, and forwarded by name -- never given a
# compose default, because the gate distinguishes an empty worktree status
# from an absent one, and a default would turn "nobody looked" into "clean".
RUN_ENV := -e FAM_WORKTREE_STATUS -e FAM_EXECUTION_HOST -e FAM_PUBLICATION_DATA

verify: guard
	$(COMPOSE) run --rm -e FAM_E4_CS_TLS_PORT bootstrap python scripts/verify_environment.py

spike: guard
	$(COMPOSE) run --rm toolbox python scripts/spike_compatibility.py

e0: guard
	export FAM_WORKTREE_STATUS="$$(git status --porcelain)"
	$(COMPOSE) run --rm $(RUN_ENV) toolbox python experiments/e0_baseline.py

# E1 does not rerun E0.
e1: guard
	export FAM_WORKTREE_STATUS="$$(git status --porcelain)"
	$(COMPOSE) run --rm $(RUN_ENV) toolbox python experiments/e1_federation.py

e2-pilot: guard
	$(COMPOSE) run --rm toolbox python scripts/e2_pilot.py

# E2 reruns neither E0 nor E1.
e2: guard
	export FAM_WORKTREE_STATUS="$$(git status --porcelain)"
	$(COMPOSE) run --rm $(RUN_ENV) -e FAM_E2_TIMELINE_LIMIT toolbox python experiments/e2_recovery.py

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

# The E3 campaign. Runs no other experiment, and resumes a partially
# completed campaign instead of restarting it.
e3: guard
	export FAM_WORKTREE_STATUS="$$(git status --porcelain)"
	$(COMPOSE) run --rm $(RUN_ENV) -e FAM_E3_SCHEDULE_SEED -e FAM_E3_TIMELINE_LIMIT \
		-e FAM_E3_SYNC_TIMEOUT_MS -e FAM_E3_BLOCKS -e FAM_E3_WORKLOADS \
		toolbox python experiments/e3_benchmark.py

# --- E4: human-driven, cannot be automated ---------------------------------
#
# e4-prepare checks readiness and prints the connection details the human
# needs. e4 runs ONE session and waits for a real person. e4-validate checks
# the recorded sessions independently of the runner.

e4-prepare: guard
	$(COMPOSE) run --rm -e FAM_LLM_PROVIDER -e FAM_LLM_MODEL -e FAM_LLM_API_KEY \
		-e FAM_LLM_BASE_URL -e FAM_E4_CS_TLS_PORT bootstrap python scripts/e4_prepare.py

# Exports the research CA so it can be imported into the human client's trust
# store. Nothing modifies a system trust store automatically.
e4-ca:
	@$(COMPOSE) run --rm --no-deps -T bootstrap cat /tls/ca.crt

# Interactive by design: the session waits for a person. Do not add -T.
e4: guard
	export FAM_WORKTREE_STATUS="$$(git status --porcelain)"
	$(COMPOSE) run --rm $(RUN_ENV) -e FAM_LLM_PROVIDER -e FAM_LLM_MODEL -e FAM_LLM_API_KEY \
		-e FAM_LLM_BASE_URL -e FAM_LLM_MAX_TOKENS -e FAM_LLM_SYSTEM_PROMPT \
		-e FAM_E4_SESSION_ID -e FAM_E4_CLIENT_NAME -e FAM_E4_CLIENT_VERSION \
		-e FAM_E4_CLIENT_HOST -e FAM_E4_JOIN_TIMEOUT -e FAM_E4_TIMEOUT \
		-e FAM_E4_CONFIRM_VISIBLE \
		toolbox python experiments/e4_human_llm.py

e4-validate: guard
	$(COMPOSE) run --rm --no-deps toolbox python scripts/e4_validate.py

# Machine-readable state of the testbed, as an input to the protocol lock.
inventory: guard image-digests
	$(COMPOSE) run --rm -e FAM_EXECUTION_HOST -e FAM_HOST_VIRTUALIZATION -e FAM_HOST_DISTRIBUTION -e FAM_LLM_PROVIDER -e FAM_LLM_MODEL -e FAM_E4_CLIENT_NAME -e FAM_E4_CLIENT_VERSION -e FAM_E4_CLIENT_HOST bootstrap python scripts/testbed_inventory.py

# The formal protocol lock. Generated once, after the final configuration
# freeze and before the first formal run. It is written to $FAM_RESULTS_DIR
# and copied into the worktree by hand: no container writes into the tree.
#
# The container has no .git, so `git status` inside it returns nothing — which
# is indistinguishable from a clean tree. The Git facts are therefore read here,
# on the host that can actually see the repository, and passed in.
lock: guard
	export FAM_WORKTREE_STATUS="$$(git status --porcelain)"
	$(COMPOSE) run --rm --no-deps -e FAM_WORKTREE_STATUS -e FAM_PROTOCOL_GIT_COMMIT -e FAM_LLM_PROVIDER -e FAM_LLM_MODEL -e FAM_LLM_BASE_URL -e FAM_LLM_MAX_TOKENS -e FAM_LLM_SYSTEM_PROMPT -e FAM_E4_CLIENT_NAME -e FAM_E4_CLIENT_VERSION -e FAM_E4_CLIENT_HOST toolbox python scripts/protocol_lock.py generate --tag $(TAG) --supersedes-tag "$(SUPERSEDES)" --supersedes-reason "$(SUPERSEDES_REASON)" --supersedes-commit "$$(git rev-list -n 1 $(SUPERSEDES) 2>/dev/null || true)"

# Validates the freshly generated artifact, which lives in the results root and
# is not in the worktree yet -- the whole point being to check it before
# committing it. `lock-check` validates the committed one afterwards.
lock-validate: guard
	export FAM_WORKTREE_STATUS="$$(git status --porcelain)"
	export FAM_GIT_TAGS_AT_HEAD="$$(git tag --points-at HEAD)"
	$(COMPOSE) run --rm --no-deps -e FAM_WORKTREE_STATUS -e FAM_GIT_TAGS_AT_HEAD -e FAM_PROTOCOL_GIT_COMMIT toolbox python scripts/protocol_lock.py validate --path /results/environment/protocol-lock.json

# The precondition for a formal run: lock, commit and tag all agree.
#
# A lock cannot name the commit that carries it: the artifact has to exist
# before it can be committed, so it names the implementation commit and the tag
# lands on the commit that adds it. FAM_IMPLEMENTATION_DIFF carries the paths
# that differ between the two, so the validator can insist that the difference
# is the lock file and nothing else -- which is what makes "the tag reproduces
# the implementation the lock describes" a checked claim rather than a promise.
lock-check: guard
	export FAM_WORKTREE_STATUS="$$(git status --porcelain)"
	export FAM_GIT_TAGS_AT_HEAD="$$(git tag --points-at HEAD)"
	locked=$$(python3 -c "import json;print(json.load(open('results/protocol-lock.json'))['implementation']['git_commit'])")
	export FAM_IMPLEMENTATION_DIFF="$$(git diff --name-only $$locked HEAD)"
	$(COMPOSE) run --rm --no-deps -e FAM_WORKTREE_STATUS -e FAM_GIT_TAGS_AT_HEAD -e FAM_PROTOCOL_GIT_COMMIT -e FAM_IMPLEMENTATION_DIFF toolbox python scripts/protocol_lock.py validate --require-tag

# Closes the formal collection phase: completion gate, raw inventory, archive
# digest. Read-only over the evidence (Task 07 §42, §43, §44).
freeze: guard
	$(COMPOSE) run --rm --no-deps toolbox python scripts/freeze_collection.py

# Figures, drawn from the processed tables. raw -> processed -> here -> SVG.
figures: guard
	$(COMPOSE) run --rm --no-deps toolbox python scripts/figures.py

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
