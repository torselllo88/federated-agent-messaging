# Evidence Matrix

**Status:** EMPTY — no evidence collected
**Depends on:** [`research-scope.md`](research-scope.md) (FROZEN, v1.0), [`testbed-architecture.md`](testbed-architecture.md) (FROZEN, v1.0), [`experimental-protocol.md`](experimental-protocol.md) (FROZEN, v1.0)

---

This document is the traceability ledger: every frozen requirement to the experiment that exercises it, to the concrete artifact that evidences it, to its current status.

The rows below are derived from the frozen documents. The **Evidence artifact** and **Status** columns are the working part and are empty by construction until the testbed produces output.

Two related tables live in the protocol and are not duplicated here: the experiment-to-claim mapping ([§42](experimental-protocol.md)) and the claim/required-evidence/failure-condition table ([§43](experimental-protocol.md)). This document tracks *what has actually been collected*; those tables define *what would count*.

## 1. Requirement → experiment → evidence

| Requirement | Primary RQ | Experiment | Acceptance criterion | Evidence artifact | Status |
|---|---|---|---|---|---|
| **C1** Persistent actor identity | RQ1 | E0, E2 | scope §6 C1; protocol §14, §16 — 3/3 runs pass | — | not collected |
| **C2** Non-privileged participation | RQ1 | E0 | scope §6 C2; protocol §14 — 3/3 runs pass | — | not collected |
| **C3** Durable asynchronous interaction | RQ2 | E2 | scope §6 C3; protocol §16 — `S_recovered == S_sent`, 100 unique requests, 0 duplicates, 3/3 runs | — | not collected |
| **C4** Persistent multi-party space | RQ2 | E1 | scope §6 C4; protocol §15 — 3/3 runs pass | — | not collected |
| **C5** Federated persistent space | RQ2 | E1 | scope §6 C5; protocol §15 — events accessible through both domains at quiescence, 3/3 runs | — | not collected |

## 2. Research question → evidence

| RQ | Question | Experiments | Evidence artifact | Status |
|---|---|---|---|---|
| **RQ1** | Persistent non-privileged agent participation | E0, E2 | — | not collected |
| **RQ2** | Federated persistent interaction | E1, E2 | — | not collected |
| **RQ3** | Federation overhead | E3 latency + throughput | — | not collected |
| **RQ4** | Messaging-primitive reuse boundary | architectural analysis (no mandatory experiment) | — | not started |

## 3. Planned formal runs

The frozen experiment set (protocol [§47](experimental-protocol.md)). Nothing has been executed; this is the target inventory against which collection progress is measured.

| Experiment | Formal runs | Per run | Raw output | Collected |
|---|---:|---|---|---:|
| E0 | 3 | 40 interactions | `results/raw/e0/` | 0 |
| E1 | 3 | 40 interactions, fresh room | `results/raw/e1/` | 0 |
| E2 | 3 | 100 offline requests, fresh room | `results/raw/e2/` | 0 |
| E3 latency | 20 (10 paired blocks) | 50 warm-up + 500 measured | `results/raw/e3/latency/` | 0 |
| E3 throughput | 60 (10 paired blocks × C ∈ {1, 8, 32}) | 10 s warm-up, 30 s measured, 10 s drain | `results/raw/e3/throughput/` | 0 |
| E4 | ≥1 session | ≥1 human ↔ LLM-agent interaction | `results/raw/e4/` | 0 |
| **Total** | **89 + E4** | | | **0** |

Only runs executed against a locked protocol version count (protocol [§3](experimental-protocol.md) Phase 4). Pilot output carries `publication_data = false` and is never merged here.

## 4. Minimum publication success conditions

The eight conditions from scope §18. The empirical core is complete only when all eight are met.

| # | Condition | Source experiment | Status |
|---|---|---|---|
| 1 | Autonomous participant maintains a stable communication identity across runtime restart | E0, E2 | ☐ |
| 2 | Normal agent participation uses ordinary messaging interfaces | E0 | ☐ |
| 3 | At least three mixed participants share a persistent interaction space | E1 | ☐ |
| 4 | The space spans at least two separately configured federation domains using native federation | E1 | ☐ |
| 5 | Relevant persistent interaction events are accessible through the participating domains after federation propagation | E1 | ☐ |
| 6 | An autonomous runtime can disappear, return, retrieve missed interaction, and resume processing | E2 | ☐ |
| 7 | Same-domain and federated interaction paths are quantitatively compared under a deterministic workload | E3 | ☐ |
| 8 | At least one functional scenario uses an LLM-backed execution layer | E4 | ☐ |

## 5. Secondary properties — optional, not on the critical path

Recorded so that incidental observations have a place to land without expanding scope (scope §19).

| Property | Type | Experiment | Observation | Status |
|---|---|---|---|---|
| **D1** Substrate-derived conversational context | design | none required | — | not evaluated |
| **D2** Typed semantic extensibility | design | none required — architecture §37 excludes custom events | — | not evaluated |
| **D3** Execution-layer independence | design | E3 (deterministic) / E4 (LLM) as control | — | not evaluated |
| **K1** Relevance determination | correctness | outside mandatory scope | — | not evaluated |
| **K2** Context isolation | correctness | outside mandatory scope, follows D1 | — | not evaluated |
| **K3** Semantic event conformance | correctness | outside mandatory scope, follows D2 | — | not evaluated |

## 6. Evidence provenance separation

Per scope §15, testbed evidence and industrial evidence are kept apart and are not directly compared unless environments and workload definitions make the comparison methodologically valid.

| Class | Source | Reproducible without Chums? | Entries |
|---|---|---|---|
| **Controlled testbed evidence** | This repository's testbed and experiments | yes | none yet |
| **Industrial reference evidence** | Chums Chat, where publication permission exists — architectural observations, deployment experience, sanitized measurements | no | none yet |

## 7. Excluded and failed runs

Protocol [§34](experimental-protocol.md) requires a documented reason for every excluded formal run, and [§35](experimental-protocol.md) forbids silently rerunning a valid experimental failure. Both are logged here.

| Date | Experiment | Run | Classification | Reason |
|---|---|---|---|---|
| — | — | — | — | — |

Classification is either `invalid run` (may be repeated) or `valid experimental failure` (stays in the dataset).

## 8. Out-of-scope observations log

Per scope §19 and architecture §40, an implementation finding outside C1–C5 is recorded here as an observation, limitation, discussion point or follow-up candidate. It does **not** expand the research scope.

| Date | Observation | Classification | Disposition |
|---|---|---|---|
| — | — | — | — |
