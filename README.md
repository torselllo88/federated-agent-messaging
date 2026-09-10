# federated-agent-messaging

Research repository for a publication investigating whether a mature federated messaging infrastructure with persistent replicated interaction spaces can serve directly as shared communication infrastructure for human and autonomous AI participants.

**Reference implementation:** Matrix / Synapse.
**Scope status:** FROZEN, v1.2 — see [`docs/research-scope.md`](docs/research-scope.md) and [`docs/CHANGELOG.md`](docs/CHANGELOG.md).
**Campaign status:** the formal campaign is complete, executed under the Git tag `protocol-v1.2-lock2`. Results are in [`results/`](results/README.md); the evidence matrix is [`docs/evidence-matrix.md`](docs/evidence-matrix.md).

---

## Central hypothesis

> A mature federated messaging infrastructure with persistent replicated interaction spaces can serve as a shared multi-party communication substrate in which humans and autonomous AI agents participate as persistent communication actors across federation domains.

The hypothesis concerns **communication architecture**. It does not assume the substrate provides reasoning, agent execution, discovery, external identity, reputation, payment or economic settlement.

## What is frozen

| Frozen | Reference |
|---|---|
| Central hypothesis | [`research-scope.md` §5](docs/research-scope.md) |
| Core architectural requirements C1–C5 | §6 |
| Research questions RQ1–RQ3 (RQ4 secondary, architectural analysis) | §9 |
| Experimental program E0–E4 | §12 |
| Explicit non-goals | §17 |
| Testbed technology baseline | [`testbed-architecture.md` §4](docs/testbed-architecture.md) |
| Experimental topologies | [`testbed-architecture.md` §24](docs/testbed-architecture.md) |
| Architecture freeze | [`testbed-architecture.md` §41](docs/testbed-architecture.md) |
| Architecture acceptance criteria A1–A13 | [`testbed-architecture.md` §39](docs/testbed-architecture.md) |
| Workloads, metrics, replication counts | [`experimental-protocol.md` §21–§23, §47](docs/experimental-protocol.md) |
| Statistical treatment and outlier policy | [`experimental-protocol.md` §29–§34](docs/experimental-protocol.md) |
| Stated limitations of the evidence | [`evidence-matrix.md` §8](docs/evidence-matrix.md) |

Implementation findings do not automatically expand scope. An interesting capability outside C1–C5 is logged as an observation, limitation, discussion point or follow-up candidate — see §19 and the observations log in [`docs/evidence-matrix.md`](docs/evidence-matrix.md).

## Repository layout

```
README.md
Makefile  make.ps1               frozen command surface; make.ps1 is the Windows shim
docker-compose.yml               two domains, two databases, bootstrap and toolbox images
docs/
    research-scope.md            FROZEN v1.2 — hypothesis, C1-C5, RQ1-RQ4, E0-E4, non-goals
    testbed-architecture.md      FROZEN v1.2 — topology, components, instrumentation, A1-A13
    experimental-protocol.md     FROZEN v1.2 — procedures, workloads, metrics, analysis rules
    evidence-matrix.md           COLLECTED — requirement → experiment → artifact traceability
    CHANGELOG.md                 v1.0 → v1.1 → v1.2 methodology changes, with rationale
    e4-human-client-setup.md     connecting a standard Matrix client for E4
    tasks/                       implementation task specifications
infrastructure/                  Synapse configuration template and container definitions
src/fam/                         the implementation: agent, participants, benchmark,
                                 instrumentation, analysis, executors, protocol lock
experiments/                     e0..e4, one entry point per experiment
scripts/                         bootstrap, verification, pilots, lock, freeze, analysis,
                                 figures, audit
tests/                           unit and regression tests
results/
    README.md                    what is here, the raw-archive record, reproduction path
    protocol-lock.json           the formal lock the campaign executed under
    schemas/                     versioned raw-record and manifest schemas
    manifests/                   132 formal run manifests, each naming its raw digests
    processed/                   analysis outputs and the E3 tables
    figures/                     3 SVG figures, generated from the tables
    environment/                 lock inputs, collection freeze, archive inventory
LICENSE
.gitignore
```

The layout follows [`testbed-architecture.md` §31](docs/testbed-architecture.md). `testbed/` is a placeholder retained from the pre-implementation tree; the implementation lives in `src/fam/`.

Raw experiment data is deliberately **not** in this tree. Formal runs write to `$FAM_RESULTS_DIR` outside the repository; what is tracked is run manifests (each carrying the SHA-256 of its raw file), result schemas, processed datasets and analysis code. The final raw dataset is archived separately and identified by digest.

## Current status

| Item | State |
|---|---|
| Research scope | frozen, v1.2 |
| Testbed architecture | frozen, v1.2 |
| Experimental protocol | frozen, v1.2 — pre-specified before data collection |
| Testbed implementation | complete |
| Protocol lock | `protocol-v1.2-lock2`, created before collection and tagged |
| Experiments E0–E4 | complete — 129 automated runs and 3 E4 sessions, all valid |
| Evidence collected | 132 manifests, 12 processed artifacts, 3 figures |
| Raw archive | 433 files, 216.8 MB, held outside this repository |
| Related-work review | complete |

Campaign `fam-formal-b260ac4df1f524a5`. No run was discarded, repeated or excluded, and no invalidity class was needed.

## Results

Reported numbers live in [`results/`](results/README.md) and nowhere else in
this repository, so there is one place to change when the analysis is rerun:

| What | Where |
|---|---|
| Headline comparison, raw-archive record, reproduction path | [`results/README.md`](results/README.md) |
| Latency percentiles, paired comparison, per-run throughput, stationarity | [`results/processed/e3-tables/`](results/processed/e3-tables) |
| Full analysis output with provenance | [`results/processed/`](results/processed) |
| Figures | [`results/figures/`](results/figures) |
| Which frozen requirement each result satisfies | [`docs/evidence-matrix.md`](docs/evidence-matrix.md) |

`make analyse` regenerates the tables from the raw archive and `make figures`
redraws the figures from the tables. No number in a figure is written by hand.

## Experimental program

Only E0–E4 are on the pre-submission critical path.

C4 is the one requirement with split support and both halves are required: E1 establishes the multi-party federated structure with programmatic participants, and E4 supplies the actual human. Neither satisfies C4 alone. E4 evidences that this architecture can host an LLM-backed execution layer; it does **not** establish D3 as a general property (scope §22).

| ID | Experiment | Validates |
|---|---|---|
| **E0** | Same-domain functional baseline | C1, C2, instrumentation, baseline performance |
| **E1** | Federated persistent multi-party interaction | C4 structural half, C5 — primary feasibility experiment |
| **E2** | Autonomous runtime interruption and recovery | C3 |
| **E3** | Controlled federation overhead | RQ3 |
| **E4** | LLM-backed functional validation | C4 human half; LLM-runtime compatibility |

## Running the testbed

The whole programme is implemented: two federation domains, provisioned
identities, the deterministic and LLM-backed agents, dual instrumentation,
E0–E4, the protocol lock, the collection freeze, analysis, figures and the
final integrity audit.

Only [`docs/tasks/task-01-bootstrap-and-e0.md`](docs/tasks/task-01-bootstrap-and-e0.md)
is committed of the implementation task specifications; the later tasks were
not. What governs the experiments is the frozen document set, not the task
specifications.

### Host prerequisites

| Requirement | Why | Notes |
|---|---|---|
| Docker Engine with Compose V2 | Everything runs in containers | Docker Desktop must be **running**, not merely installed |
| GNU `make` *(optional on Windows)* | Frozen command surface | Windows hosts can use `.\make.ps1` with the same targets |

Python 3.12 is frozen but is supplied by the toolbox image, so no host Python
installation is required.

Nothing is installed on the host by these commands. If a prerequisite is
missing, the tooling reports it rather than installing it.

### Reproducibility path

```bash
export FAM_RESULTS_DIR=/path/outside/this/repository

make setup      # TLS, configs, both domains, accounts, environment manifest
make verify     # transport and bootstrap readiness, config hashes, rate limits
make spike      # compatibility spike: Synapse + nio + room version 12
make e0         # three independent E0 runs
make e1         # three independent federated E1 runs
make e2         # three independent E2 recovery runs
make e3-pilot   # E3 pilot: benchmark mechanics, sync limit, stationarity
make e3         # the E3 campaign, 120 paired benchmark runs
make analyse    # digest verification, schema validation, E0-E3 summaries
make figures    # SVG figures, drawn from the processed tables
```

Diagnostics and pilots that are never publication evidence:

```bash
make test           # unit and regression tests
make e2-pilot       # select the E2 sync timeline limit
make e3-readiness   # live gap recovery under bounded-concurrency stress
make inventory      # machine-readable testbed state, an input to the lock
```

E4 needs an actual person at a standard Matrix client and an LLM credential,
so it has its own flow:

```bash
make e4-prepare  # readiness and the human client connection details
make e4          # ONE human-driven session; run it three times
make e4-validate # check the recorded sessions
```

See [E4 human client setup](docs/e4-human-client-setup.md) for the trust-store
and homeserver steps the human client needs.

`make e3` is long-running and resumable: a completed run is never repeated and
never overwritten, so an interrupted campaign continues where it stopped. Rerun
the same command to resume. A change to any campaign parameter — schedule seed,
sync configuration, message size, timings, or the Synapse configuration hash —
produces a new campaign identifier rather than mixing two parameter sets into
one dataset.

On Windows, substitute `.\make.ps1 setup` and so on.

### The formal campaign

A formal run is gated on the protocol lock. These are the targets that create
it, hold the campaign to it, and close it afterwards:

```bash
make lock           # generate the lock: commit, images, config hashes,
                    # frozen parameters, seeds, the whole E3 schedule
make lock-validate  # check the generated lock before committing it
make lock-check     # the precondition for a formal run: lock, commit and tag agree
make freeze         # close collection: completion gate, inventory, archive digest
make audit          # impossible states, schedule, provenance, secrets
```

`FAM_PUBLICATION_DATA=true` marks a run as formal. The gate then refuses to
start unless the worktree is clean, the tag is on `HEAD`, the host matches the
lock, every frozen parameter matches the lock, and no environment override that
could move one is set. A mismatch stops the run before any data is written
rather than producing data under an unrecorded configuration.

`FAM_RESULTS_DIR` must resolve **outside** this repository. Every
run-generated artifact — raw streams, agent telemetry, manifests, environment
output — is written there for the whole campaign, so the worktree stays clean
and `HEAD` stays on the protocol-lock commit
([`experimental-protocol.md` §37](docs/experimental-protocol.md)). The guard
refuses to run otherwise.

### Development runs are not evidence

A run carries `publication_data = false` unless it is executed as part of the
formal campaign. Development output validates the implementation; it is not
publication evidence, and it does not update any counter or checkbox in
[`docs/evidence-matrix.md`](docs/evidence-matrix.md).

The two kinds never share a result root or a campaign identifier. The formal
campaign ran against its own root on the designated Linux host, and the
pre-lock host acceptance — full test suite, `make verify`, `make e3-pilot`,
`make e4-prepare` — ran against a separate one and contributed nothing to the
reported figures. Every manifest under [`results/manifests/`](results/manifests)
carries `publication_data = true`.

## Claim discipline

Several individual elements of this architecture have substantial prior art — XMPP/SPADE-style multi-agent communication, classical shared-state and coordination systems, existing Matrix-based LLM assistants, and contemporary federated agent-messaging protocols. The scope document records these explicitly (§2, §20) and lists what the manuscript **shall not** claim novelty for (§16).

The intended differentiation is the systematic formulation and controlled empirical evaluation of persistent replicated interaction spaces as a shared communication substrate across human and autonomous participants belonging to federation domains. This remains a **target contribution**, not an unconditional first-of-kind claim. Phrases such as "the first" shall not appear unless independently justified by the final literature review.

The literature review is complete and the rule held: the manuscript claims the *combination* has not been evaluated jointly, qualified as "to the best of our knowledge", and claims novelty for none of the individual ingredients.

## Industrial reference boundary

Chums Chat is the industrial implementation that motivated the research abstraction. It is **not** part of the reproducible artifact.

This repository shall contain only public specifications, public APIs, open-source infrastructure and independently written research code. No proprietary Chums code, and no code derived from proprietary implementation details, may be committed here. Industrial evidence, where publication permission exists, is kept separate from controlled testbed evidence and is not directly compared with it unless the environments and workload definitions make that comparison methodologically valid (§15).

**The paper must remain reproducible without access to Chums.**

## Reproducibility intent

The testbed models administrative independence between federation domains using separate homeserver instances, separate server identities, separate credentials and native federation — all within controlled research infrastructure. The publication states this distinction explicitly: what is demonstrated is protocol and deployment separation, not organizational independence proven through separately owned real-world infrastructure (§13).

Where both homeservers run inside the same controlled infrastructure, performance results are not presented as estimates of real-world Internet or geographically distributed federation latency (§9 RQ3).

## Licence

Apache License 2.0 — see [`LICENSE`](LICENSE).

Chosen as a single permissive licence covering both research code and documentation, with an explicit patent grant appropriate to an industrially adjacent project. If the target venue requires a different arrangement for the prose — CC-BY-4.0 is common — that is a one-file change plus a note here.
