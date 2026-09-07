# Evidence Matrix

**Status:** COLLECTED — formal campaign complete under `protocol-v1.2-lock2`
**Depends on:** [`research-scope.md`](research-scope.md) (FROZEN, v1.2), [`testbed-architecture.md`](testbed-architecture.md) (FROZEN, v1.2), [`experimental-protocol.md`](experimental-protocol.md) (FROZEN, v1.2)

Campaign `fam-formal-b260ac4df1f524a5`, executed 2026-09-06 on `fam-formal-linode-48865d44`. Every artifact referenced below is committed under [`results/`](../results/) and named by its digest in [`results/environment/raw-archive-inventory.json`](../results/environment/raw-archive-inventory.json).

---

This document is the traceability ledger: every frozen requirement to the experiment that exercises it, to the concrete artifact that evidences it, to its current status.

The rows below are derived from the frozen documents. The **Evidence artifact** and **Status** columns are the working part and are empty by construction until the testbed produces output.

Two related tables live in the protocol and are not duplicated here: the experiment-to-claim mapping ([§42](experimental-protocol.md)) and the claim/required-evidence/failure-condition table ([§43](experimental-protocol.md)). This document tracks *what has actually been collected*; those tables define *what would count*.

Every run-generated artifact lives outside this repository for the duration of the formal campaign, under `$FAM_RESULTS_DIR` (architecture §22, protocol §37) — raw streams, manifests, E4 evidence and environment output alike. Archival copies of manifests, processed datasets and figures are imported in a post-experiment commit (protocol §3 Phase 5), each retaining its `protocol_git_commit`, raw-stream SHA-256 provenance and `analysis_code_commit`. Those imported copies are what the artifact column below will point at.

## 1. Requirement → experiment → evidence

| Requirement | Primary RQ | Experiment | Acceptance criterion | Evidence artifact | Status |
|---|---|---|---|---|---|
| **C1** Persistent actor identity | RQ1 | E0, E2 | scope §6 C1; protocol §14, §16 — 3/3 runs pass | `e0-…160451Z-01/02/03`, `e2-…160654Z-01/02/03` | **PASS** — E0 3/3 identity stable across full runtime termination; E2 3/3 same identity and checkpoint resumed |
| **C2** Non-privileged participation | RQ1 | E0 | scope §6 C2; protocol §14 — 3/3 runs pass | `e0-…160451Z-01/02/03` | **PASS** — E0 3/3, C2 supporting evidence complete in every run |
| **C3** Durable asynchronous interaction | RQ2 | E2 | scope §6 C3; protocol §16 — `S_recovered == S_sent`, 100 unique requests, 0 duplicates, 3/3 runs | `e2-…160654Z-01/02/03` | **PASS** — E2 3/3, `S_recovered == S_sent` at 100 events, 0 missing, 0 unexpected, 100 processed exactly once, 0 duplicate ACKs |
| **C4** Persistent multi-party space — structural | RQ2 | E1 | scope §6 C4 *Empirical support*; protocol §15 — 3/3 runs pass, programmatic standard accounts | `e1-…160546Z-01/02/03` | **PASS** — E1 3/3, three programmatic participants across two domains |
| **C4** Persistent multi-party space — completion | RQ2 | E4 | scope §6 C4 *Empirical support*; protocol §41 — three-party federated room containing an actual person, 3/3 sessions pass | `e4-…192750Z`, `…193022Z`, `…193231Z` | **PASS** — 3/3 sessions, an actual person on Domain A through Element Desktop 1.12.27 |
| **C5** Federated persistent space | RQ2 | E1 | scope §6 C5; protocol §15 — event-set **equality** and exact membership equality between the Domain-A and Domain-B views, 3/3 runs | `e1-…160546Z-01/02/03` | **PASS** — E1 3/3, event sets equal at 80 events on both domain views, membership equal, propagation confirmed in both directions |

C4 is the only requirement with split empirical support, and both halves are required. In E0–E3 every participant is a program and `HumanParticipant` denotes a role, not a person, so E1 establishes the structural case only; E4's three-party room is where "at least one human participant" holds literally. C4 is not satisfied until both rows are collected. The requirement itself was deliberately not weakened to "human-role participant".

## 2. Research question → evidence

| RQ | Question | Experiments | Evidence artifact | Status |
|---|---|---|---|---|
| **RQ1** | Persistent non-privileged agent participation | E0, E2 | `e0-…160451Z-01/02/03`, `e2-…160654Z-01/02/03` | **collected** — 6/6 runs pass |
| **RQ2** | Federated persistent interaction | E1, E2 | `e1-…160546Z-01/02/03`, `e2-…160654Z-01/02/03` | **collected** — 6/6 runs pass |
| **RQ3** | Federation overhead | E3 latency + throughput | `results/processed/e3-tables/`, `results/figures/` | **collected** — 120/120 runs valid; paired p50 ratio 1.7794 [1.7722, 1.7852] |
| **RQ4** | Messaging-primitive reuse boundary | architectural analysis (no mandatory experiment) | — | not started |

**What RQ3 does and does not establish.** E3 measures the observed end-to-end performance of the tested closed-loop interaction system under the frozen local and federated topologies. It is not maximum Matrix throughput, not maximum federation throughput, and not messaging-layer capacity independent of the agent runtime (protocol §17). The formal campaign reproduces the signature the frozen text anticipated: `C = 32` returned no more than `C = 8` — 12.008/s against 11.825/s locally, 9.192/s against 9.275/s federated — which is a service rate set by the sequential deterministic agent, not by the transport. The latency comparison carries no such caveat: at `max_in_flight = 1` there is no queue, and the federated path cost 1.7794x the local one at p50 across 20,000 measured interactions with a zero failure rate.

## 3. Formal runs collected

The frozen experiment set (protocol [§47](experimental-protocol.md)), executed in full. Every run is valid; none was discarded, repeated or excluded.

| Experiment | Formal runs | Per run | Raw output | Collected |
|---|---:|---|---|---:|
| E0 | 3 | 40 interactions | `raw/e0/` | **3** |
| E1 | 3 | 40 interactions (20 cross-domain + 20 same-domain), fresh room | `raw/e1/` | **3** |
| E2 | 3 | 100 offline sends, fresh room | `raw/e2/` | **3** |
| E3 latency | 40 (20 paired blocks) | 50 warm-up + 500 measured | `raw/e3/latency/` | **40** |
| E3 throughput | 80 (20 paired blocks × C ∈ {8, 32}) | 10 s warm-up (not drained), 60 s measured, 10 s drain | `raw/e3/throughput/` | **80** |
| E4 | 3 sessions | distinct three-party room per session, ≥3 human requests and ≥3 valid LLM responses each | `raw/e4/` | **3** |
| **Total** | **129 + 3 E4 sessions** | | | **129 + 3** |

Every run produces two streams joined by `run_id`: the runner interaction stream and the agent telemetry stream. E2's acceptance criteria are agent-side facts and cannot be evaluated from the runner stream alone.

Only runs executed against a locked protocol version count (protocol [§3](experimental-protocol.md) Phase 4). Pilot output carries `publication_data = false` and is never merged here; the pre-lock host acceptance ran against a separate result root and contributed nothing to the figures above.

82,399 interactions were initiated and 82,399 completed successfully: no timeout, no send error, no rate-limit response, no interaction delivered through gap recovery, and no run classified invalid. Every one of the 23 checks in the final integrity audit passed.

## 4. Minimum publication success conditions

The eight conditions from scope §18. The empirical core is complete only when all eight are met.

| # | Condition | Source experiment | Status |
|---|---|---|---|
| 1 | Autonomous participant maintains a stable communication identity across runtime restart | E0, E2 | ☑ `e0-…160451Z-01/02/03`, `e2-…160654Z-01/02/03` |
| 2 | Normal agent participation uses ordinary messaging interfaces | E0 | ☑ `e0-…160451Z-01/02/03` |
| 3 | At least three mixed participants share a persistent interaction space | E1 structural + E4 with an actual person — both required | ☑ `e1-…160546Z-01/02/03` + `e4-…192750Z`, `…193022Z`, `…193231Z` |
| 4 | The space spans at least two separately configured federation domains using native federation | E1 | ☑ `e1-…160546Z-01/02/03` |
| 5 | Relevant persistent interaction events are accessible through the participating domains after federation propagation | E1 | ☑ `e1-…160546Z-01/02/03` |
| 6 | An autonomous runtime can disappear, return, retrieve missed interaction, and resume processing | E2 | ☑ `e2-…160654Z-01/02/03` |
| 7 | Same-domain and federated interaction paths are quantitatively compared under a deterministic workload | E3 | ☑ 120 runs, `results/processed/e3-tables/` |
| 8 | At least one functional scenario uses an LLM-backed execution layer | E4 — 3/3 sessions | ☑ `e4-…192750Z`, `…193022Z`, `…193231Z` |

## 5. Secondary properties — optional, not on the critical path

Recorded so that incidental observations have a place to land without expanding scope (scope §19).

| Property | Type | Experiment | Observation | Status |
|---|---|---|---|---|
| **D1** Substrate-derived conversational context | design | none required | — | not evaluated |
| **D2** Typed semantic extensibility | design | none required — architecture §37 excludes custom events | — | not evaluated |
| **D3** Execution-layer independence | design | see note | — | not evaluated |
| **K1** Relevance determination | correctness | outside mandatory scope | — | not evaluated |
| **K2** Context isolation | correctness | outside mandatory scope, follows D1 | — | not evaluated |
| **K3** Semantic event conformance | correctness | outside mandatory scope, follows D2 | — | not evaluated |

**D3 note.** E4 is on the critical path; D3 is not. E4 evidences that this communication architecture can host an LLM-backed execution layer. It does not establish D3 as a general architectural property, and the manuscript must not treat the one as proof of the other (scope §7, protocol §41).

## 6. Evidence provenance separation

Per scope §15, testbed evidence and industrial evidence are kept apart and are not directly compared unless environments and workload definitions make the comparison methodologically valid.

| Class | Source | Reproducible without Chums? | Entries |
|---|---|---|---|
| **Controlled testbed evidence** | This repository's testbed and experiments | yes | none yet |
| **Industrial reference evidence** | Chums Chat, where publication permission exists — architectural observations, deployment experience, sanitized measurements | no | none yet |

## 7. Excluded and failed runs

Protocol [§35](experimental-protocol.md) is the single authoritative definition of formal-run validity: a closed list of nine named invalid-run classes, plus the rule that any failure produced by the correctly configured testbed under the tested workload is an experimental outcome rather than an invalid run. Both kinds are logged here.

| Date | Experiment | Run | Classification | §35 class | Reason |
|---|---|---|---|---|---|
| — | — | — | — | — | — |

Classification is either `invalid run`, which names one of the nine §35 class identifiers verbatim and may be repeated, or `valid experimental failure`, which names no class and stays in the dataset.

## 8. Stated limitations

Boundaries the frozen documents impose on what the collected evidence can support. Recorded here so the manuscript's limitations section is assembled from the design rather than reconstructed afterwards.

| Limitation | Source |
|---|---|
| One small-message workload class only (256-byte body); payload-size sensitivity not studied | architecture §17, protocol §7 |
| Cold restart with no retained transport checkpoint is not evaluated | architecture §21, protocol §16 |
| Human-role participants in E0–E3 are programmatic; only E4 involves an actual person, so C4's completion rests on three human sessions rather than on a measured quantity | architecture §11.1, protocol §41 |
| E1's same-domain request class (Human B) does not exercise a cross-domain request loop | architecture §10, protocol §15 |
| Both homeservers run in one controlled environment; results are not WAN or Internet federation estimates | scope §9 RQ3, §13, architecture §30 |
| All formal E0–E3 evidence comes from one Linux host; no cross-host or cross-platform generalization | architecture §33, protocol §39 |
| Throughput is an observed rate at tested concurrency, never a maximum or capacity claim | scope §9, protocol §19.2 |

## 9. Out-of-scope observations log

Per scope §19 and architecture §40, an implementation finding outside C1–C5 is recorded here as an observation, limitation, discussion point or follow-up candidate. It does **not** expand the research scope.

| Date | Observation | Classification | Disposition |
|---|---|---|---|
| — | — | — | — |
