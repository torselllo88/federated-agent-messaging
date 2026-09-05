# federated-agent-messaging

Research repository for a publication investigating whether a mature federated messaging infrastructure with persistent replicated interaction spaces can serve directly as shared communication infrastructure for human and autonomous AI participants.

**Reference implementation:** Matrix / Synapse.
**Scope status:** FROZEN, v1.1 as of 2026-09-02 — see [`docs/research-scope.md`](docs/research-scope.md) and [`docs/CHANGELOG.md`](docs/CHANGELOG.md).

---

## Central hypothesis

> A mature federated messaging infrastructure with persistent replicated interaction spaces can serve as a shared multi-party communication substrate in which humans and autonomous AI agents participate as persistent communication actors across federation domains.

The hypothesis concerns **communication architecture**. It does not assume the substrate provides reasoning, agent execution, discovery, external identity, reputation, payment or economic settlement.

## What is frozen

| Frozen | Reference |
|---|---|
| Central hypothesis | [`research-scope.md` §5](docs/research-scope.md) |
| Core architectural requirements C1–C5 | §6 |
| Research questions RQ1–RQ3 (RQ4 secondary) | §9 |
| Experimental program E0–E4 | §12 |
| Explicit non-goals | §17 |
| Testbed technology baseline and topology | [`testbed-architecture.md` §4, §41](docs/testbed-architecture.md) |
| Architecture acceptance criteria A1–A13 | [`testbed-architecture.md` §39](docs/testbed-architecture.md) |
| Workloads, metrics, replication counts | [`experimental-protocol.md` §21–§23, §47](docs/experimental-protocol.md) |
| Statistical treatment and outlier policy | [`experimental-protocol.md` §29–§34](docs/experimental-protocol.md) |
| Stated limitations of the evidence | [`evidence-matrix.md` §8](docs/evidence-matrix.md) |

Implementation findings do not automatically expand scope. An interesting capability outside C1–C5 is logged as an observation, limitation, discussion point or follow-up candidate — see §19 and the observations log in [`docs/evidence-matrix.md`](docs/evidence-matrix.md).

## Repository layout

```
README.md
docs/
    research-scope.md          FROZEN v1.1 — hypothesis, C1-C5, RQ1-RQ4, E0-E4, non-goals
    testbed-architecture.md    FROZEN v1.1 — topology, components, instrumentation, A1-A13
    experimental-protocol.md   FROZEN v1.1 — procedures, workloads, metrics, analysis rules
    evidence-matrix.md         scaffolded — requirement → experiment → artifact traceability
    CHANGELOG.md               v1.0 → v1.1 methodology changes, with rationale
results/
    README.md                  external raw-data artifact record
testbed/                       empty — reproducible testbed implementation
experiments/                   empty — experiment definitions e0..e4
scripts/                       empty — bootstrap, verification and analysis
LICENSE
.gitignore
```

[`testbed-architecture.md` §31](docs/testbed-architecture.md) defines the layout the implementation should converge toward — `infrastructure/`, `src/fam/`, `results/` and a `docker-compose.yml` / `Makefile` at the root. The current tree is the pre-implementation subset; empty directories are not committed merely to match that diagram.

Raw experiment data is deliberately **not** in this tree. Formal runs write to `$FAM_RESULTS_DIR` outside the repository; what is tracked is run manifests (each carrying the SHA-256 of its raw file), result schemas, processed datasets and analysis code. The final raw dataset is archived separately and identified by digest.

## Current status

| Item | State |
|---|---|
| Research scope | frozen |
| Testbed architecture | frozen |
| Experimental protocol | frozen — pre-specified before data collection |
| Testbed implementation | not started — acceptance criteria A1–A13 defined |
| Experiments E0–E4 | not started — 129 formal runs specified, plus 3 E4 sessions |
| Evidence collected | none |
| Related-work review | not started — mandatory before submission (§16, §20) |

## Experimental program

Only E0–E4 are on the pre-submission critical path.

| ID | Experiment | Validates |
|---|---|---|
| **E0** | Same-domain functional baseline | C1, C2, instrumentation, baseline performance |
| **E1** | Federated persistent multi-party interaction | C4, C5 — primary feasibility experiment |
| **E2** | Autonomous runtime interruption and recovery | C3 |
| **E3** | Controlled federation overhead | RQ3 |
| **E4** | LLM-backed functional validation | D3 |

## Running the testbed

Implemented so far: repository bootstrap, two federation domains, federation
transport readiness, provisioned identities, the deterministic agent, dual
instrumentation, and E0. See
[`docs/tasks/task-01-bootstrap-and-e0.md`](docs/tasks/task-01-bootstrap-and-e0.md).

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
make e3         # the E3 development campaign, 120 paired benchmark runs
make analyse    # digest verification, schema validation, E0-E3 summaries
```

`make e3` is long-running and resumable: a completed run is never repeated and
never overwritten, so an interrupted campaign continues where it stopped. Rerun
the same command to resume. A change to any campaign parameter — schedule seed,
sync configuration, message size, timings, or the Synapse configuration hash —
produces a new campaign identifier rather than mixing two parameter sets into
one dataset.

On Windows, substitute `.\make.ps1 setup` and so on.

`FAM_RESULTS_DIR` must resolve **outside** this repository. Every
run-generated artifact — raw streams, agent telemetry, manifests, environment
output — is written there for the whole campaign, so the worktree stays clean
and `HEAD` stays on the protocol-lock commit
([`experimental-protocol.md` §37](docs/experimental-protocol.md)). The guard
refuses to run otherwise.

### Development runs are not evidence

Everything produced by the commands above carries `publication_data = false`.
It validates the implementation; it is not publication evidence, and it does
not update any counter or checkbox in
[`docs/evidence-matrix.md`](docs/evidence-matrix.md). Formal evidence is
collected only in the later protocol-locked campaign on the designated Linux
host.

## Claim discipline

Several individual elements of this architecture have substantial prior art — XMPP/SPADE-style multi-agent communication, classical shared-state and coordination systems, existing Matrix-based LLM assistants, and contemporary federated agent-messaging protocols. The scope document records these explicitly (§2, §20) and lists what the manuscript **shall not** claim novelty for (§16).

The intended differentiation is the systematic formulation and controlled empirical evaluation of persistent replicated interaction spaces as a shared communication substrate across human and autonomous participants belonging to federation domains. This remains a **target contribution**, not an unconditional first-of-kind claim. Phrases such as "the first" shall not appear unless independently justified by the final literature review.

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
