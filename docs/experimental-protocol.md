# Experimental Protocol v1.1 FINAL

**Project:** Federated Agent Messaging
**Repository:** `federated-agent-messaging`
**Date:** 2026-09-02
**Status:** PRE-SPECIFIED EXPERIMENTAL PROTOCOL
**Amends:** v1.0 — see [`CHANGELOG.md`](CHANGELOG.md)
**Depends on:**

- [`research-scope.md`](research-scope.md) (FROZEN, v1.1)
- [`testbed-architecture.md`](testbed-architecture.md) (FROZEN, v1.1)

**Feeds:** [`evidence-matrix.md`](evidence-matrix.md)
**Purpose:** Define the execution procedures, controlled variables, workloads, measurements, acceptance criteria, and analysis rules for experiments E0–E4.

---

## 1. Purpose

This document defines how the frozen testbed architecture is experimentally evaluated.

The protocol is specified before formal publication data collection in order to reduce retrospective adjustment of:

- workloads;
- metrics;
- acceptance criteria;
- statistical treatment;
- run-selection rules.

The mandatory experimental program consists of:

- E0 — Same-Domain Functional Baseline
- E1 — Federated Persistent Multi-Party Interaction
- E2 — Autonomous Runtime Interruption and Recovery
- E3 — Controlled Federation Overhead
- E4 — Human + LLM Functional Validation

E0–E2 establish architectural feasibility.

E3 provides quantitative characterization.

E4 validates compatibility with an actual LLM-backed execution layer.

---

## 2. Experimental Scope

The study is an architectural feasibility and systems-characterization study.

It is not intended to:

- benchmark Matrix generally;
- determine global Synapse scalability;
- identify maximum Matrix capacity;
- compare Matrix against other protocols;
- evaluate LLM quality;
- reproduce public-Internet or geographically distributed federation.

The experimental objective is:

> **Determine whether the frozen architecture can be instantiated correctly and characterize the additional communication cost introduced by the tested federated path relative to an equivalent same-domain path.**

---

## 3. Experimental Phases

Experimental work **SHALL** proceed through four phases.

### Phase 1 — Development

Implementation and debugging.

Outputs from this phase **SHALL NOT** be used as publication results.

### Phase 2 — Pilot

The completed testbed is exercised to:

- verify instrumentation;
- validate timeout behavior;
- verify synchronization behavior;
- verify rate-limit configuration;
- confirm benchmark workloads complete;
- detect implementation defects;
- validate result schemas;
- determine whether the proposed concurrency levels are technically meaningful.

Pilot outputs **SHALL** be marked:

```text
publication_data = false
```

Pilot observations **SHALL NOT** be merged into publication datasets.

### Phase 3 — Protocol Lock

Before formal publication data collection:

- source code **SHALL** be committed;
- Git working tree **SHALL** be clean;
- infrastructure configuration **SHALL** be frozen;
- Synapse configuration **SHALL** be frozen;
- result schema **SHALL** be frozen;
- benchmark parameters **SHALL** be frozen;
- experiment execution order **SHALL** be generated;
- the execution schedule and random seed **SHALL** be stored;
- configuration hashes **SHALL** be generated.

The frozen state **SHALL** be tagged, for example:

```text
protocol-v1.0
```

A machine-readable manifest **SHALL** be generated:

```text
protocol-lock.json
```

### Phase 4 — Publication Runs

Only runs performed against the locked protocol version qualify as publication data.

The protocol-version increment and rerun requirement applies to changes capable of affecting experimental execution or the interpretation of collected measurements:

- testbed / runtime code;
- agent or runner behaviour;
- infrastructure configuration;
- workload configuration;
- instrumentation producing primary raw observations;
- frozen protocol parameters.

Such a change requires:

1. protocol-version increment;
2. documented rationale;
3. rerunning all affected publication experiments.

It does **NOT** apply to post-collection work that leaves the frozen experiment and analysis specification unchanged:

- implementation of analysis scripts;
- figure and table generation;
- import of manifests and results into the tracked repository;
- non-normative documentation — `README.md`, `results/README.md`, `docs/CHANGELOG.md`, explanatory comments, and publication prose that does not redefine the frozen experiment;
- archival metadata.

Writing the analysis implementation after the lock is expected, not an exception. Post-collection work is still version-controlled and provenance-linked to the raw dataset (§40, Phase 5).

#### The documentation exception is narrow

It covers non-normative documentation only. It does **NOT** cover the frozen normative specifications:

- `research-scope.md`;
- `testbed-architecture.md`;
- `experimental-protocol.md`;
- protocol-lock schemas and frozen experiment configuration.

The frozen parameters live inside documentation files, so "it is only a documentation change" is not a classification. A post-lock edit to normative content **SHALL** be classified by what it changes:

| What the edit changes | Increment |
|---|---|
| execution-affecting specification | `protocol_version` |
| frozen analysis methodology | `analysis_spec_version` |
| both categories | both |

If post-collection analysis reveals that frozen metric definitions, the statistical procedure, inclusion rules or any other element of the analysis methodology must change, that is a **methodological revision**. It increments `analysis_spec_version` and **SHALL** be explicitly versioned and disclosed, never absorbed into the analysis implementation.

#### Two independent version counters

`protocol_version` and `analysis_spec_version` are **independent**.

Ownership is **semantic, never positional**. It follows what a rule does, not which section or which document contains it. Section placement may change without changing ownership, so no rule is assigned to a counter here by section number.

**`protocol_version`** governs any frozen specification that can affect how primary raw observations are generated, recorded or classified during formal execution:

- topology and participant configuration;
- formal-host requirements;
- workloads and concurrency;
- message size and message-generation rules;
- warm-up, measurement, drain, quiescence and timeout parameters;
- runtime and synchronization behaviour;
- rate-limit configuration requirements;
- the E2 recovery procedure;
- experiment validity and precondition rules;
- the raw interaction-outcome taxonomy;
- instrumentation behaviour;
- the raw-result schema and the fields produced during execution.

A post-lock change to any of these increments `protocol_version` and requires rerunning the affected formal experiments.

**`analysis_spec_version`** governs how immutable raw observations are transformed into reported analytical results:

- metric formulas;
- failure-rate calculation from recorded outcomes;
- the throughput estimator;
- percentile definitions;
- aggregation rules;
- paired-comparison definitions;
- the bootstrap and resampling procedure;
- confidence-interval construction;
- post-collection inclusion and exclusion rules that do not redefine run validity;
- reported differences and ratios.

A post-lock change to any of these increments `analysis_spec_version`. It does **not** require rerunning formal experiments, unless the change reveals that the necessary raw observations were never collected.

Classification of the definitions currently in force:

| Definition | Counter |
|---|---|
| Raw interaction-outcome taxonomy | `protocol_version` |
| Failure-rate formula computed from those outcomes | `analysis_spec_version` |
| Formal-host requirement | `protocol_version` |
| Run-validity rules | `protocol_version` |
| Statistical treatment and metric aggregation | `analysis_spec_version` |

The outcome taxonomy and the failure-rate formula sit in adjacent sections of this document and belong to different counters. That is exactly why ownership is defined semantically.

Worked examples:

- changing the bootstrap confidence-interval procedure without changing execution increments **only** `analysis_spec_version`;
- changing E3 concurrency levels increments **only** `protocol_version`, unless it also forces a change to the analysis methodology;
- a change affecting both raw-data generation and analysis methodology increments **both**.

Incrementing both counters whenever either changes is not a safe default. It erases the distinction and makes it impossible to tell later whether the experiment or its interpretation was revised.

### Phase 5 — Post-Experiment Import

`HEAD` **SHALL** remain on the protocol-lock commit for the entire formal campaign.

Only after all formal runs are complete are archival copies of manifests, processed datasets and figures imported into the tracked `results/` directories, in a separate post-experiment commit.

Every imported artifact **SHALL** retain:

- `protocol_git_commit` — the lock commit under which the data was produced;
- SHA-256 provenance of the raw streams it derives from;
- `analysis_spec_version` — the frozen analysis specification it was produced under;
- `analysis_code_commit` — the commit of the analysis-code implementation that produced it.

The importing commit is none of these four.

---

## 4. General Preconditions

Before every formal experiment:

1. both Synapse homeservers **SHALL** report healthy status;
2. both PostgreSQL instances **SHALL** be operational;
3. federation transport and bootstrap readiness **SHALL** pass environment verification, within the bounds of §4.1 and no further;
4. required accounts **SHALL** have been provisioned, expected credentials **SHALL** be available, and bootstrap provisioning **SHALL** have completed successfully;
5. no unrelated workload **SHALL** run against either homeserver;
6. Git working tree **SHALL** be clean — every run-generated artifact is written outside the working tree (§37), so this holds for the whole campaign rather than only the first run;
7. `FAM_RESULTS_DIR` **SHALL** be set and resolve to a path outside the repository;
8. the checked-out commit **SHALL** match the protocol lock;
9. environment metadata **SHALL** be recorded;
10. Synapse configuration hashes **SHALL** match the frozen configuration;
11. the environment verifier **SHALL** confirm client-side rate limiting is non-binding for the planned workload, and the runner **SHALL** receive the frozen limit values as manifest data rather than reading server configuration;
12. the testbed and automated experiment infrastructure **SHALL** execute on the dedicated Linux formal-run host recorded in the protocol lock; in E4 the actual human's external client is exempt (§39).

Room version and encryption state are **NOT** preconditions. Rooms are created during experiment setup and cannot be inspected before they exist; they are asserted at creation instead (§4.2).

A failed formal execution precondition makes a run invalid **only** when the failure maps to a class in the authoritative invalid-run taxonomy (§35). Failure of behaviour that is itself under experimental evaluation remains an experimental outcome.

Ordinary participant authentication is the clearest case. It is not a blanket validity precondition, because authenticating through a standard non-privileged client interface is precisely what C2 asserts and E0 measures. Precondition 4 therefore requires only that accounts were provisioned and credentials are available. If a correctly provisioned participant using the expected credentials then fails to authenticate through the ordinary Matrix interface during behaviour under evaluation, that is a finding classified by the experiment, not an automatic invalidation.

### 4.1 Federation readiness verification

Environment verification **MAY** establish only federation transport and bootstrap readiness:

- both homeservers are reachable;
- required DNS and name resolution works;
- the federation TCP/TLS path can be established;
- normal Matrix federation identity and signing-key discovery succeeds.

It **MUST NOT** verify:

- federated room join;
- cross-domain room membership propagation;
- persistent event propagation;
- cross-domain history visibility;
- any other behaviour directly evaluated by C5 or E1.

The line is deliberate. Room-level federated propagation is what E1 exists to test; pre-testing it would let a genuine C5 failure be filed as an environment problem instead of a finding — the same trap the authentication precondition avoided.

Failure of transport or bootstrap readiness is an invalid execution-environment condition. Failure of room-level federation behaviour once the experiment has begun is an experimental outcome (§35).

### 4.2 Room assertions at creation

After each fresh experiment room is created, and before experimental interaction begins, the setup procedure **SHALL** assert:

- the Matrix room version is exactly 12;
- room encryption is disabled;
- any other frozen room configuration matches the protocol.

A newly created room that does not match the frozen room configuration because of a setup or configuration error makes the run invalid under `frozen_configuration_error` (§35).

A previous run's room **SHALL NOT** be used to satisfy these checks.

---

## 5. Synapse Rate-Limit Policy

Client-side message throttling **SHALL NOT** become the dominant limiting factor in E2 or E3.

The default Synapse client message rate limits are therefore unsuitable for the experimental workloads.

Both homeservers **SHALL** use identical explicitly configured client-message rate limits set sufficiently above the maximum planned experiment load.

The frozen configuration **SHALL** document at minimum:

```text
rc_message
rc_joins
rc_invites
rc_room_creation
rc_login
```

and any other client rate limit capable of affecting the experiment.

`rc_room_creation` and `rc_joins` are named explicitly because every formal run uses a fresh room. Under a fresh-room-per-run design they become binding well before `rc_message` does.

A fresh room per formal run is mandatory (§18, §41), so `rc_room_creation`, `rc_joins`, `rc_invites` and the related client-side setup limits **SHALL** be configured non-binding for the planned formal campaign, and the environment verifier **SHALL** confirm this alongside `rc_message`.

Formal E3 runs **SHALL** verify that no request failed because of client-side:

```text
M_LIMIT_EXCEEDED
```

unless such behavior is itself caused by an unexpected configuration defect.

Verification of the configuration itself belongs to the bootstrap / environment verifier, which has configuration access. The runner checks only that its planned offered load stays inside the frozen limit values it was given, and observes any `M_LIMIT_EXCEEDED` responses it receives. The runner never reads Synapse configuration; that restriction is itself part of the C2 evidence.

Federation-specific Synapse controls, including:

```text
rc_federation
```

**SHALL** remain identical on both homeservers and **SHALL** be treated as part of the tested Synapse federation implementation unless the pilot demonstrates that they prevent the experiment from operating in the intended workload range.

The manuscript **SHALL** distinguish:

```text
client-side experimental throttling
```

from:

```text
native federation behavior
```

---

## 6. Experimental Roles

### Human A

Primary human-role sender:

```text
@human-a:hs-a.test
```

### Human B

Secondary human-role participant, and the ordinary-client observer of Domain B:

```text
@human-b:hs-b.test
```

Human B supplies the Domain-B view that E1 requires. There is no separate observer account, and C5 evidence is not taken from the agent's self-report.

### Local Agent

Autonomous participant on Domain A.

Used for E0 and the local E3 topology.

### Federated Agent

Autonomous participant on Domain B.

Used for E1, E2, federated E3, and E4.

### Experiment Runner

Controls automated human-role participants and records measurements.

The runner **SHALL** have ordinary Matrix participant credentials only.

---

## 7. Controlled Message Semantics

E0–E3 use deterministic messages.

Request:

```text
FAM/1 REQUEST <experiment> <run> <sequence>
```

Response:

```text
FAM/1 ACK <experiment> <run> <sequence>
```

The deterministic agent **SHALL** respond only to valid `FAM/1 REQUEST` events.

It **SHALL** ignore:

- its own responses;
- malformed experimental messages;
- unrelated ordinary chat messages;
- already processed logical requests.

This prevents response loops and duplicate application-level execution.

### Payload size

For E3, request and response bodies **SHALL** each be exactly 256 bytes when UTF-8 encoded.

Construction:

```text
<ASCII-only correlation prefix><padding to 256 bytes>
```

The padding character is fixed — `x` — and the prefix is ASCII-only, so encoded byte length equals character length and the padding count is unambiguous.

The runner and the executor **SHALL** each assert the exact encoded body length before sending. A body of any other length is a defect, not a tolerance.

The 256-byte requirement applies to the `m.room.message` body only, not to the complete Matrix event or federation PDU.

E0–E2 **MAY** use the unpadded envelope, as they produce no latency or throughput result.

RQ3 conclusions are bounded to this single small-message workload class, which **SHALL** be stated as a limitation.

---

## 8. Matrix Synchronization Configuration

E0–E3 **SHALL** use the same Matrix client synchronization strategy unless an experiment explicitly requires restart/recovery behavior.

The runner and agent **SHALL** use:

- continuous long-poll synchronization;
- identical sync timeout configuration across local and federated conditions;
- equivalent Matrix filters;
- no artificial polling delay between completed sync requests;
- equivalent matrix-nio synchronization settings.

The frozen synchronization configuration **SHALL** be recorded in:

```text
protocol-lock.json
```

Differences in polling behavior **SHALL NOT** be allowed to become a hidden difference between the local and federated benchmark paths.

---

## 9. Logical Interaction Definition

One logical experimental interaction consists of:

```text
runner sends REQUEST
        ↓
Matrix communication path
        ↓
agent receives REQUEST
        ↓
deterministic executor
        ↓
agent sends ACK
        ↓
Matrix communication path
        ↓
runner receives matching ACK
```

A logical interaction is identified by:

```text
experiment_id
run_id
sequence_id
```

and associated Matrix transaction/event identifiers.

---

## 10. Timing Definition

Primary E3 latency is:

```text
T0 = runner begins request-send operation
T3 = runner receives matching ACK

RTT = T3 - T0
```

Both timestamps **SHALL** use the same high-resolution monotonic clock.

T3 **SHALL** be stamped at the very start of the runner callback for the matching ACK: after the `/sync` response is parsed, before any application-level processing of the event. Because `/sync` delivers batches, ACKs arriving in one batch may share a nearly identical T3; the rule above makes that deterministic and identical across topologies rather than implementation-dependent.

Agent-local timestamps **MAY** be collected diagnostically:

```text
T1 = request becomes available to agent
T2 = response-send operation begins
```

but **SHALL NOT** be used as primary cross-process latency measurements unless clock comparability has been independently established.

The following **SHALL NOT** be used as primary latency clocks:

- Matrix `origin_server_ts`;
- wall-clock differences between containers;
- server timestamps from different processes.

---

## 11. Interaction Outcomes

Every initiated formal interaction **SHALL** terminate in exactly one outcome category:

```text
success
timeout
send_error
malformed_response
duplicate_response
unexpected_response
runner_error
```

The default logical-interaction timeout is:

```text
10 seconds
```

The timeout **MAY** be revised during pilot execution only if normal low-load interactions cannot reliably complete within this bound.

Any change after protocol lock requires a new protocol version.

### Offline sends

E2 deliberately sends requests while the agent runtime is stopped. These are **NOT** logical interactions under §9 and **SHALL** be recorded as a separate class:

```text
offline_send
```

An `offline_send` has no response deadline while the runtime is down. Its deadline begins when the restart and recovery phase begins, after which it resolves into one of the ordinary outcome categories above.

Without this carve-out the 10-second timeout would classify all 100 E2 offline requests as `timeout`, and E2 would report total failure by construction.

The outcome taxonomy above classifies raw observations during execution. It is governed by `protocol_version` (§3 Phase 4), unlike the failure-rate formula that consumes it.

This taxonomy classifies individual interactions. Run-level validity is a separate matter, defined once in §35.

---

## 12. Failure Rate

For a workload:

```text
failure_rate =
unsuccessful logical interactions
/
initiated logical interactions
```

`offline_send` records are excluded from this denominator until their deadline begins (§11).

HTTP or Matrix client retransmission **SHALL NOT** create a new logical interaction.

A logical request producing multiple distinct ACK events **SHALL** be classified as:

```text
duplicate_response
```

and therefore unsuccessful.

This formula transforms recorded outcomes into a reported metric. It is governed by `analysis_spec_version` (§3 Phase 4), unlike the outcome taxonomy it consumes.

---

## 13. Event Identity and Deduplication

Scientific Matrix-event identity **SHALL** use:

```text
event_id
```

Application-level logical identity **SHALL** use:

```text
experiment_id + run_id + sequence_id
```

Matrix transaction identifiers provide client-send idempotency but do not replace scientific correlation.

All successfully sent experimental events **SHALL** have their returned Matrix `event_id` recorded.

---

## 14. E0 — Same-Domain Functional Baseline

### Objective

Validate persistent non-privileged autonomous participation before federation is introduced.

E0 primarily supports:

- C1;
- C2;
- testbed readiness.

### Topology

```text
Human A
   ↓
Synapse A
   ↑
Local Agent
```

### Independent repetitions

E0 **SHALL** be executed:

```text
3 independent runs
```

Each run **SHALL** use a fresh Matrix room.

### Procedure per run

1. create a fresh room version 12 room on Domain A;
2. join Human A;
3. join Local Agent;
4. send 20 sequential deterministic requests;
5. verify 20 matching ACKs;
6. record the agent Matrix identity;
7. stop the agent runtime;
8. restart the same runtime;
9. authenticate using the same Matrix identity;
10. confirm room membership remains present;
11. send 20 additional requests;
12. verify 20 matching ACKs.

No administrator operation may occur between initial functional interaction and post-restart validation.

### Acceptance criteria

An E0 run passes when:

- all 40 logical requests receive exactly one matching ACK;
- agent identity remains unchanged;
- room membership survives runtime restart;
- normal operation uses no administrator credentials;
- no custom server component is required.

E0 passes overall when:

```text
3 / 3 valid runs pass
```

A valid failed run **SHALL** remain a failed observation.

Latency values from E0 **SHALL NOT** be used for RQ3.

---

## 15. E1 — Federated Persistent Multi-Party Interaction

### Objective

Demonstrate that a persistent mixed-participant interaction space spans the federation boundary.

E1 supports:

- the structural portion of C4, using programmatically controlled standard Matrix user accounts;
- C5;
- RQ2.

C4 is not fully evidenced by E1. E4 completes it with an actual human in the same three-party topology (scope §6 C4, *Empirical support*).

### Topology

```text
@human-a:hs-a.test
@human-b:hs-b.test
@agent:hs-b.test
```

Human A supplies the Domain-A view; Human B supplies an independent ordinary-client Domain-B view.

Two request classes follow, and **SHALL** be recorded and reported separately:

| Class | Sender | Path |
|---|---|---|
| Cross-domain | Human A (hs-a) | hs-a ⇄ hs-b — federated request/ACK loop |
| Same-domain | Human B (hs-b) | within hs-b; federation carries propagation to Human A only |

Reporting them as one pooled figure would let a federated failure hide behind same-domain successes.

### Independent repetitions

E1 **SHALL** be executed:

```text
3 independent fresh-room runs
```

### Procedure per run

1. Human A creates a new room version 12 room on Domain A;
2. Human B joins;
3. Federated Agent joins from Domain B;
4. verify expected membership through ordinary clients;
5. Human A sends 20 deterministic requests;
6. record the `event_id` of every successful request send;
7. verify 20 corresponding ACKs;
8. Human B sends 20 deterministic requests;
9. record the `event_id` of every successful request send;
10. verify 20 corresponding ACKs;
11. construct the expected request/response event set;
12. retrieve the room's experiment-related event set and membership state through Human A on Domain A;
13. retrieve the same through Human B on Domain B, using ordinary client interfaces only;
14. verify **equality** of the two experiment-related event sets, not merely that the expected events are present in each;
15. verify **exact** equality of expected membership state between the two views;
16. verify that Human A's requests appear in the Domain-B view and Human B's requests appear in the Domain-A view, evidencing both directions of federated propagation;
17. after all expected events are observed, require an additional 2-second quiet interval.

### Quiescence definition

Quiescence is reached only after:

1. every expected experimental request/ACK event has been observed;
2. required membership state has been confirmed;
3. no experimental interaction remains outstanding;
4. an additional 2-second quiet interval has elapsed.

The quiet interval is not itself evidence of Matrix convergence.

Set equality rather than inclusion is what makes the comparison capable of detecting divergence. An inclusion check can only confirm that what was expected arrived; it cannot observe an extra, missing or differing event on one side.

### Acceptance criteria

An E1 run passes when:

- all 40 requests receive exactly one matching ACK, reported separately for the 20 cross-domain and the 20 same-domain requests;
- the experiment-related event sets retrieved through Domain A and Domain B are equal;
- expected membership state is exactly equal between the two views;
- both directions of federated propagation are evidenced;
- no agent-specific bridge is used.

E1 passes overall when:

```text
3 / 3 valid runs pass
```

---

## 16. E2 — Autonomous Runtime Interruption and Recovery

### Objective

Demonstrate separation between persistent interaction state and transient autonomous runtime state.

E2 primarily supports C3.

### Topology

The federated multi-party topology from E1 is used.

### Independent repetitions

E2 **SHALL** be executed:

```text
3 independent fresh-room runs
```

Each run **SHALL** test recovery of:

```text
100 offline requests
```

### Procedure per run

1. establish a fresh federated room;
2. verify the agent is synchronized;
3. persist the agent's current synchronization checkpoint;
4. stop only the agent runtime;
5. confirm both homeservers remain operational;
6. Human A sends exactly 100 deterministic requests while the agent runtime is offline, recorded as `offline_send` (§11);
7. each successful send **SHALL** return and record a Matrix `event_id`;
8. confirm that all 100 request sends succeeded before restarting the agent;
9. define:

   ```text
   S_sent = set of the 100 request event_ids
   ```

10. restart the same agent runtime;
11. perform incremental synchronization;
12. detect whether a limited timeline/history gap exists;
13. where required, recover missing room history through standard Matrix history pagination;
14. deduplicate by `event_id`;
15. reconstruct:

    ```text
    S_recovered
    ```

16. verify:

    ```text
    S_recovered == S_sent
    ```

17. process every logical request exactly once;
18. send one correlated ACK for every request;
19. wait until all expected ACKs complete or timeout.

### Gap-recovery path

The synchronization filter **SHALL** be configured during E2 such that the history-recovery mechanism is intentionally exercised.

The exact timeline limit **SHALL** be selected during pilot execution and frozen before formal E2 runs.

The formal implementation **SHALL** support:

```text
incremental sync
        ↓
limited timeline / gap detection
        ↓
history pagination
        ↓
event-set reconstruction
        ↓
event_id deduplication
        ↓
logical request processing
```

### Required observations

Observations come from two streams joined by `run_id` (architecture §22). The runner cannot see most of what E2 asserts, so the agent telemetry stream is mandatory rather than optional.

| Source | Observations |
|---|---|
| Runner | agent identity before and after restart; room membership; `S_sent`; generated request count; ACK count; runtime restart timestamp; final recovery-completion timestamp |
| Agent telemetry | `S_recovered`; recovered unique request count; missing-event set; unexpected-event set; duplicate-processing count; whether history pagination was invoked |

For each run record:

- agent identity before restart;
- agent identity after restart;
- room membership;
- `S_sent`;
- `S_recovered`;
- generated request count;
- recovered unique request count;
- missing-event set;
- unexpected-event set;
- duplicate-processing count;
- ACK count;
- whether history pagination was invoked;
- runtime restart timestamp;
- final recovery-completion timestamp.

### Recovery time

Descriptive recovery time is:

```text
recovery_time =
time final expected ACK is received
-
time agent runtime restart begins
```

The response deadline for the 100 `offline_send` records begins at the same instant this measurement does (§11).

Recovery time is not a primary RQ3 metric.

### Acceptance criteria

A run passes only when:

```text
|S_sent| = 100
S_recovered = S_sent

100 unique logical requests processed
100 matching ACKs produced
0 missing requests
0 unexpected recovered test requests
0 duplicate logical processing
```

and:

- the same Matrix identity is used after restart;
- room membership survives;
- recovery uses only ordinary client/history APIs;
- no database or homeserver-internal inspection is required.

Correctness is event-set equality plus exactly-once processing. No ordering property is asserted, and none is required: Matrix room history is an event graph, not a globally ordered queue.

Cold restart — recovery with no retained transport checkpoint — is outside E2 and **SHALL** be stated as a limitation.

E2 passes overall when:

```text
3 / 3 valid runs pass
```

---

## 17. E3 — Controlled Federation Overhead

### Objective

Quantitatively characterize the additional communication cost of the tested federated interaction path.

RQ3 compares:

```text
same-domain
```

against:

```text
federated
```

under equivalent deterministic workloads.

The experiment does not attempt to determine maximum possible Matrix capacity.

---

## 18. E3 Benchmark Topologies

### Local

```text
Benchmark Human @ hs-a
        ↓
Synapse A
        ↓
Benchmark Agent @ hs-a
```

### Federated

```text
Benchmark Human @ hs-a
        ↓
Synapse A
        ⇅
Synapse B
        ↓
Benchmark Agent @ hs-b
```

Both benchmark rooms **SHALL** have:

- two participants;
- room version 12;
- encryption disabled;
- equivalent state configuration;
- equivalent synchronization configuration;
- no unrelated experiment history before workload execution.

A fresh benchmark room **SHALL** be created for each formal run. Each formal local or federated E3 run therefore uses a newly created room, independently checked against the frozen room configuration (§4.2) before workload execution.

Room creation is excluded from measurements.

---

## 19. E3 Primary Metrics

### 19.1 Interaction RTT

Measured as:

```text
T3 - T0
```

Reported as:

- p50;
- p95;
- p99.

### 19.2 Observed throughput

Throughput is a **steady-state completion rate**:

> **the number of logical interactions completing successfully inside the measurement window, divided by the window duration, irrespective of whether the request was initiated before or inside the window.**

Reported in:

```text
completed interactions / second
```

Counting departures rather than matched arrival-and-departure pairs is what removes a one-sided censoring bias. An estimator that counts only interactions both started and finished inside the window discards up to `C` in-flight interactions at the trailing edge, and discards proportionally more from the slower topology — biasing the ratio against federation, by an amount that grows as absolute throughput falls. Under a closed-loop workload at steady state, the leading edge (in flight at window start, completing inside) compensates the trailing edge (started inside, completing after).

This is explicitly:

```text
observed throughput at tested concurrency
```

and **SHALL NOT** be described as maximum or achievable system throughput.

### 19.3 Failure rate

Defined in Section 12.

### 19.4 Federation overhead

Latency:

```text
absolute overhead =
federated latency - local latency
```

```text
relative overhead =
federated latency / local latency
```

Throughput:

```text
throughput ratio =
federated throughput / local throughput
```

Failure-rate difference:

```text
federated failure rate - local failure rate
```

Absolute and relative results **SHOULD** both be reported where meaningful.

---

## 20. E3 Secondary Metrics

Where reliable and inexpensive, record:

- Synapse CPU utilization;
- PostgreSQL CPU utilization;
- container memory;
- network traffic.

These are supporting observations only.

Failure to collect them does not invalidate RQ3.

---

## 21. E3 Workload A — Low-Load Latency

The primary latency workload allows:

```text
maximum in-flight interactions = 1
```

A new request is initiated only after the previous request:

- succeeds; or
- times out.

### Per-run workload

Each run contains:

```text
50 warm-up interactions
500 measured interactions
```

Warm-up interactions **SHALL NOT** enter publication statistics.

After warm-up:

1. all warm-up interactions **SHALL** complete or timeout;
2. the runner **SHALL** ensure no warm-up request remains in-flight;
3. measured interaction collection begins.

### Replication

For each topology:

```text
20 formal runs
```

are performed in 20 paired local/federated blocks.

Planned measured observations:

```text
10000 measured interactions per topology
```

before accounting for failures.

Twenty blocks rather than ten: the cluster bootstrap in §32 resamples blocks, and ten clusters produce wide intervals with poor coverage. Each latency run takes on the order of a minute, so the additional statistical footing costs almost nothing.

---

## 22. E3 Workload B — Concurrent Throughput

Throughput is evaluated at:

```text
C = 8
C = 32
```

where `C` is the maximum number of outstanding logical interactions.

`C = 1` is **not** a throughput condition. At one outstanding interaction the completion rate is the reciprocal of mean RTT, which Workload A already measures over ten thousand observations per topology. It is retained as a derived consistency check in analysis (§31), not as forty additional runs.

Whenever an interaction completes or times out, another request **SHALL** be issued to maintain the target concurrency. Replenishment is not optional: if concurrency is allowed to fall below `C`, the offered load is undefined and the measurement is uninterpretable.

### Continuous closed-loop execution

The run is one continuous closed-loop workload. There is **no drain between warm-up and measurement**, and the concurrency level is never allowed to fall.

```text
t = 0              closed loop starts, concurrency held at C
t = 10 s           window_start_ns     window opens, loop unaffected
t = 70 s           window_end_ns       loop stops initiating
t = 70 s .. 80 s   drain               outcome accounting only
```

Warm-up and measurement are separated by *completion timestamp*, not by interaction identity. An interaction initiated during warm-up that completes inside the window **counts** — that is precisely what makes the estimator unbiased.

### Warm-up

```text
10 seconds
```

Warm-up establishes steady state. It is **not** drained. At `window_start_ns` the loop is already running at concurrency `C`.

### Measurement window

Duration:

```text
60 seconds
```

Primary numerator:

```text
primary throughput =
logical interactions completing successfully in [window_start_ns, window_end_ns)
/
60 seconds
```

Initiation time is irrelevant to this count.

### Behaviour at the end of the measurement window

At `window_end_ns` the runner **SHALL** stop initiating new requests.

Interactions still outstanding continue during a:

```text
10-second drain period
```

subject to the ordinary per-interaction timeout.

A completion during the drain period:

- does **NOT** count toward the throughput numerator;
- **DOES** count for final interaction-outcome and failure-rate accounting.

### Canonical measurement-window field names

The measurement window is referred to by exactly two normative names, used identically in raw schemas, run manifests, protocol-lock and schema definitions, analysis code and normative protocol text:

```text
window_start_ns
window_end_ns
```

No parallel names — `measurement start`, `measurement end`, `measurement_start`, `measurement_end` — are used anywhere as data references. Prose may still speak of "the end of the measurement window"; a normative data reference **SHALL** say `window_end_ns`.

### Required record fields

Because the window is defined by completion time, every throughput record **SHALL** carry:

```text
initiated_monotonic_ns
completed_monotonic_ns      where applicable
window_start_ns
window_end_ns
phase                       warmup | window | drain
raw interaction outcome
correlation and event identifiers
```

`initiated_monotonic_ns` is T0 and `completed_monotonic_ns` is T3 from §10. No second clock is introduced.

Raw formal observations carry primary execution facts and the frozen execution metadata needed to reconstruct a classification. They never carry the classification itself.

`counted_in_window` is therefore **NOT** a raw field. It is computed during analysis under the frozen `analysis_spec_version`. For the current estimator the analytical implementation derives it as:

```text
success
&& completed_monotonic_ns >= window_start_ns
&& completed_monotonic_ns <  window_end_ns
```

Persisting that boolean as authoritative raw evidence would bind immutable observations to one revision of the analysis specification: a later estimator revision would make already-written raw data wrong, and §34 forbids correcting raw data in place. Keeping it derived is what makes the two version counters independent in practice rather than only on paper.

Without the fields above the analysis cannot reconstruct the numerator.

### Steady-state check

The estimator assumes steady state at `window_start_ns`. Each run **SHALL** report the completion rate of the first and second halves of the measurement window. A material difference indicates warm-up was too short, and **SHALL** be resolved during pilot execution before protocol lock.

### Replication

For every:

```text
topology × concurrency
```

combination:

```text
20 formal runs
```

**SHALL** be executed.

Total formal throughput runs:

```text
2 topologies × 2 concurrency levels × 20 runs = 80 runs
```

---

## 23. Throughput Pilot Rule

The planned concurrency levels are:

```text
8, 32
```

During pilot execution only, they **SHALL** be checked for technical meaningfulness.

If `C=32` triggers an infrastructure pathology that prevents interpretable measurement rather than ordinary load response, the concurrency set **MAY** be revised before protocol lock.

Any change **SHALL**:

- occur before publication data collection;
- be documented;
- increment the draft protocol revision;
- not be based on selecting whichever level produces preferable federation results.

After protocol lock, concurrency levels **SHALL** remain fixed.

---

## 24. E3 Pairing and Execution Order

Formal E3 runs **SHALL** use paired blocks.

Each block contains:

```text
one local run
one federated run
```

The local/federated order inside each block **SHALL** be randomized or counterbalanced.

Example:

```text
Block 01: Local → Federated
Block 02: Federated → Local
Block 03: Federated → Local
Block 04: Local → Federated
```

Twenty paired blocks are executed per workload (§21, §22).

The complete formal run order **SHALL** be generated before publication data collection.

The following **SHALL** be stored:

- generated schedule;
- random seed;
- protocol version.

This applies separately to:

- latency workload blocks;
- each throughput-concurrency workload.

---

## 25. Inter-Run Quiescence

After each formal E3 run:

1. no new requests are generated;
2. all remaining interactions complete or timeout;
3. the experiment runner verifies no formal interaction is outstanding;
4. the environment remains idle for at least:

```text
5 seconds
```

before the next run.

A longer interval may be selected during pilot execution if measurable cross-run interference remains.

It **SHALL** then be frozen before publication data collection.

---

## 26. Environment Consistency

During all E3 runs:

- Synapse A remains running;
- Synapse B remains running;
- PostgreSQL A remains running;
- PostgreSQL B remains running;
- Docker topology remains unchanged;
- synchronization settings remain unchanged;
- client message rate limits remain unchanged;
- federation controls remain unchanged;
- host hardware remains unchanged;
- no unrelated benchmark executes concurrently;
- deterministic executor source code remains identical.

Domain B **SHALL** remain running during local benchmark runs.

---

## 27. Benchmark Symmetry

The same implementation **SHALL** be used for local and federated conditions:

- experiment runner;
- agent source code;
- deterministic executor;
- synchronization loop;
- message format;
- timeout;
- result schema;
- room version;
- Matrix SDK version.

Only the following may differ:

```text
agent identity
agent homeserver
benchmark room
communication topology
```

---

## 28. Rate-Limit Validation During E3

Before each publication benchmark batch, the environment verifier **SHALL** confirm that the frozen client-side rate-limit configuration exceeds the planned offered load, and record the result in the environment manifest.

The runner **SHALL** compare its planned offered load against the frozen limit values it receives as manifest data. It does not read Synapse configuration — it holds neither administrator credentials nor server filesystem access, and that restriction is part of the C2 evidence.

Any occurrence of:

```text
M_LIMIT_EXCEEDED
```

**SHALL** be recorded explicitly.

If `M_LIMIT_EXCEEDED` occurs because the intended non-binding experimental limit was configured incorrectly, the run is invalid.

If it occurs despite the locked configuration because the tested system genuinely invokes another documented constraint, the outcome **SHALL** remain part of the experimental result.

---

## 29. Statistical Analysis Principles

The study is primarily descriptive and comparative.

Null-hypothesis significance testing is not required.

Primary reporting focuses on:

- effect magnitude;
- latency distributions;
- run-level variability;
- failure rates;
- uncertainty intervals.

Individual messages **SHALL NOT** be treated as independent experimental replicates.

The formal replication unit is the:

```text
experimental run / paired run block
```

---

## 30. Latency Analysis

For every topology report:

- initiated interactions;
- successful interactions;
- failures by category;
- p50 RTT;
- p95 RTT;
- p99 RTT.

For local-vs-federated comparison report:

- absolute percentile delta;
- percentile ratio;
- paired uncertainty interval.

The primary latency dataset excludes warm-up interactions.

Successful high-latency interactions **SHALL** remain in the dataset.

---

## 31. Throughput Analysis

For each run compute:

```text
throughput =
successful interactions completing inside the 60-second measurement window
/
60
```

For each concurrency/topology report:

- median run throughput;
- distribution across runs;
- failure rate;
- first-half versus second-half completion rate (steady-state check, §22);
- number of requests still outstanding at `window_end_ns`;
- number completing during drain;
- number ultimately timing out.

### Derived C=1 consistency check

`C = 1` is not executed as a throughput condition. Analysis **SHALL** instead compare, per topology:

```text
predicted C=1 throughput = 1 / mean RTT      (from Workload A)
```

against the observed trend at `C = 8` and `C = 32`. A gross inconsistency indicates an instrumentation or workload defect rather than a system property.

For paired local/federated blocks compute:

- throughput difference;
- throughput ratio.

---

## 32. Bootstrap Confidence Intervals

Where confidence intervals are reported, use:

```text
95% bootstrap confidence intervals
```

with paired experimental blocks as the primary resampling unit.

### Latency bootstrap

For each bootstrap sample:

1. resample paired run blocks with replacement;
2. preserve local/federated pairing;
3. pool measured interactions within selected local runs;
4. pool measured interactions within corresponding selected federated runs;
5. compute the required percentile for each topology;
6. compute delta and/or ratio.

This preserves cluster structure at the run level rather than treating messages as independent replicates.

### Throughput bootstrap

For each concurrency:

1. resample paired run blocks with replacement;
2. compute paired local/federated run throughput;
3. estimate throughput difference and/or ratio.

---

## 33. Tail Latency

p99 is calculated only from successful measured publication interactions.

The total number of successful observations **SHALL** always accompany p99 reporting.

If failure rates become non-trivial, p99 **SHALL NOT** be discussed without the corresponding failure rate.

No latency winsorization or tail trimming is permitted.

---

## 34. Outlier Policy

Successful interactions **SHALL NOT** be removed because their latency is unusually high.

No statistical outlier trimming is permitted for primary results.

An entire run may be excluded only if independently classified as invalid under the canonical taxonomy in §35. This section maintains no exclusion list of its own.

Every excluded formal run **SHALL** have a documented reason naming its §35 class.

---

## 35. Formal Run Validity

This section is the single authoritative definition of formal-run validity. Outlier handling (§34), exclusion reporting, manifests (§38) and protocol-lock semantics (§46) reference this taxonomy and **SHALL NOT** maintain lists of their own.

### Invalid run

A formal run **MAY** be classified as invalid only under one of the following classes. Each has a machine-readable identifier, used verbatim in the run manifest. The list is authoritative and closed at protocol lock.

| Class | Covers |
|---|---|
| `protocol_lock_mismatch` | mismatch of an **execution-time locked identity**: checked-out protocol commit or tag, execution `protocol_version`, frozen raw-result schema version, other explicitly execution-locked artifact identities. Runtime configuration hashes are **not** covered here, and neither is `analysis_spec_version` |
| `frozen_configuration_error` | actual experiment or testbed **configuration content** that does not match the frozen configuration: Synapse configuration hash mismatch; experiment configuration hash mismatch; synchronization configuration mismatch; a room created by setup that does not match the frozen room configuration (§4.2); incorrect frozen rate-limit values |
| `execution_precondition_violation` | **operational prerequisites** outside the frozen testbed and configuration content: dirty Git worktree; missing or invalid `$FAM_RESULTS_DIR`; `$FAM_RESULTS_DIR` located inside the tracked worktree; execution on the wrong designated formal host (§39); other operational prerequisites that represent neither tested-system behaviour nor frozen configuration content |
| `instrumentation_or_output_failure` | corrupted, incomplete or unusable primary instrumentation or result output |
| `runner_implementation_failure` | experiment-runner implementation failure unrelated to the behaviour being tested |
| `infrastructure_failure` | host, container-runtime or infrastructure failure unrelated to the tested workload |
| `environment_corruption` | corruption of the controlled environment |
| `external_interference` | accidental unrelated workload or external interference that invalidates the controlled condition |
| `external_dependency_or_client_environment_failure` | failure of an external dependency, or of an external human-client environment, required to perform a validation but outside the behaviour being evaluated: LLM provider outage or API unavailability; human workstation connectivity failure; standard Matrix client crash; missing or incorrect trust-store setup on the E4 human workstation |

Invalid runs **MAY** be repeated. Every excluded formal run **SHALL** carry a documented reason naming its class identifier, recorded in the run manifest and in the evidence matrix.

`analysis_spec_version` is deliberately absent from `protocol_lock_mismatch`. A methodological revision after data collection is legitimate (§3 Phase 4) and **SHALL NOT** retroactively invalidate collected raw data. A run is never invalid merely because the analysis specification was revised after it executed.

Every invalid run receives **exactly one** authoritative class. The first three are mutually exclusive by construction — artifact *identity*, frozen configuration *content*, and *operational* prerequisites are disjoint categories — so no precedence rule is needed and none exists. A configuration-hash mismatch is `frozen_configuration_error`, never `protocol_lock_mismatch`.

The designated-host requirement appears once here, under `execution_precondition_violation`, and is defined once in §39. No other class covers it.

### Experimental failure

> **Any failure produced by the correctly configured testbed under the tested workload is an experimental outcome, not an invalid-run condition.**

Examples that **SHALL** remain in the dataset:

- timeout;
- lost request or ACK;
- federation delivery failure;
- duplicate logical processing;
- recovery failure;
- a rate or federation constraint reached under the correctly frozen configuration;
- a tested-system crash caused by the workload.

Valid failures **SHALL NOT** be silently rerun or discarded. Rerunning until a failure disappears is the specific outcome this taxonomy exists to prevent.

### Where the boundary falls

The distinction is not what failed but whether the failure lies inside the behaviour under evaluation:

| Situation | Classification |
|---|---|
| External provider unavailable before or instead of agent execution | invalid — `external_dependency_or_client_environment_failure` |
| Provider available, but the LLM-backed agent integration fails | experimental failure |
| Human workstation not configured to trust the research CA | invalid — `external_dependency_or_client_environment_failure` |
| Correctly configured human client cannot communicate through the tested federated architecture | experimental failure |
| A provisioned participant cannot authenticate through the ordinary Matrix interface | experimental failure — this is C2 and E0 evidence |

This section classifies runs; it does not define the underlying requirements. Run-validity rules are governed by `protocol_version` (§3 Phase 4).

---

## 36. Environment Restart Policy

Routine environment restarts between formal E3 runs are not required.

The default benchmark models continuously running services.

If the environment must be restarted because of a non-experimental infrastructure problem:

- the restart **SHALL** be recorded;
- affected runs **SHALL** be classified;
- only invalid runs may be repeated automatically.

A genuine tested-system failure is not converted into an invalid run merely by restarting the environment afterward.

---

## 37. Raw Data Structure

For the entire formal campaign, **every** run-generated artifact is written **outside the tracked working tree**, under the path given by:

```text
FAM_RESULTS_DIR
```

That includes raw interaction streams, agent telemetry, per-run manifests, E4 evidence artifacts and environment outputs. Nothing is written into the working tree while formal data collection is in progress.

This is what lets the clean-worktree and protocol-lock preconditions (§4) hold together: the tree stays clean and `HEAD` stays on the protocol-lock commit for the whole campaign, instead of drifting run by run. Writing per-run manifests or other generated formal-run artifacts into the tracked worktree would make the checkout dirty after the first formal run.

Recommended structure:

```text
$FAM_RESULTS_DIR/
├── raw/
│   ├── e0/
│   ├── e1/
│   ├── e2/
│   ├── e3/
│   │   ├── latency/
│   │   └── throughput/
│   └── e4/
├── manifests/
├── environment/
└── evidence/          E4 transcripts and screenshots
```

Every formal run **SHALL** produce an immutable result set.

Example:

```text
$FAM_RESULTS_DIR/raw/e3/latency/
    20260910T142212Z_local_block01.jsonl
    20260910T142212Z_local_block01.agent.jsonl
    20260910T142945Z_federated_block01.jsonl
    20260910T142945Z_federated_block01.agent.jsonl
```

Each run produces the runner interaction stream and the agent telemetry stream, joined by `run_id`.

The final raw dataset **SHALL** be archived separately and identified by SHA-256.

---

## 38. Formal Run Manifest

Every formal run **SHALL** produce a manifest. Manifests share a common envelope and take one of two type-specific bodies, so that E4 is described honestly rather than forced into an automated-benchmark shape whose fields do not apply to it.

### Common envelope

```text
manifest_type            automated_experiment_manifest | human_llm_validation_manifest
manifest_schema_version
experiment
execution_protocol_version
execution_analysis_spec_version
protocol_git_commit
raw_schema_version
publication_data
run_id
room_id
room_version
participant identities
environment manifest reference
formal-run host identifier
start timestamp
completion status
validity classification       valid | invalid, naming its §35 class identifier if invalid
```

`validity classification` uses the canonical taxonomy in §35 and its identifiers verbatim. No other list of invalidity reasons exists.

`execution_protocol_version` and `execution_analysis_spec_version` record what was current when the run executed, and are never rewritten. A later analysis **MAY** use a newer `analysis_spec_version` and `analysis_code_commit`, provided the revision is disclosed (§3 Phase 4) and the immutable raw observations contain the data the revised analysis requires. Where they do not, the affected experiments are rerun rather than reinterpreted.

### `automated_experiment_manifest` — E0 to E3

```text
topology
block_id
workload
concurrency
message payload size
warm-up settings
measurement settings
sync configuration
timeout
rate-limit configuration reference
window_start_ns
window_end_ns
drain_end_ns

raw interaction stream SHA-256
raw agent telemetry stream SHA-256
```

### `human_llm_validation_manifest` — E4

```text
human client name and version
human client host or device
LLM provider and model identifier
agent configuration hash
executor identifier
interaction event IDs
transcript artifact reference and SHA-256
screenshot artifact reference and SHA-256
functional result
three-party topology confirmed        boolean, C4 completion
```

The human client host is recorded because it is deliberately outside the designated formal-run host (§39); recording it is what keeps that exemption auditable rather than implicit.

Concurrency, warm-up, measurement-window, drain and timing fields are absent from the E4 variant by design. E4 produces no measurements, and carrying empty benchmark fields would invite someone to fill them in later.

### Location and import

Manifests are written under `$FAM_RESULTS_DIR` with every other run-generated artifact during the campaign. Archival copies are imported into `results/manifests/` in the post-experiment commit (§3 Phase 5, architecture §34). Each imported copy retains its original `protocol_git_commit`; the commit that imports it is a different commit, and conflating the two would destroy the provenance the import exists to preserve.

---

## 39. Environment Manifest

Publication runs **SHALL** reference a sanitized environment manifest containing at minimum:

```text
Synapse version
Synapse image digest
PostgreSQL version
matrix-nio version
Python version
Docker version
Docker Compose version
Matrix room version

formal-run host identifier
host OS
host kernel
CPU model
logical CPU count
available RAM
virtualization / container runtime

Synapse config hash A
Synapse config hash B
experiment config hash
sync config hash
```

Secrets **SHALL NOT** be included.

Configuration hashes **SHALL** be the SHA-256 of the canonicalized, secret-stripped configuration document, computed by the bootstrap / environment verifier rather than by the runner.

The designated-host requirement applies to the testbed and the automated formal-experiment infrastructure: Synapse, PostgreSQL, the agent runtime, the experiment runner and all E0–E3 execution. All of it **SHALL** run on the same dedicated Linux host, whose identifier is recorded in `protocol-lock.json` and in every run manifest. Testbed or automated execution on any other host is invalid (§35), not merely noisier.

This requirement is governed by `protocol_version` (§3 Phase 4). §35 classifies its violation as an invalid run but does not define it.

It does **NOT** require the physical human client used in E4 to run on that host. For E4:

- the Matrix testbed and the LLM-backed agent **SHALL** remain on the designated formal Linux deployment;
- the actual human **MAY** use a standard Matrix client from an external workstation;
- that client's host or device and its client version **SHALL** be recorded in the E4 manifest.

Development and pilot work **MAY** run on a Windows/WSL2 workstation. Such output carries `publication_data = false` and never enters a publication dataset.

---

## 40. Data Processing Pipeline

Publication tables and figures **SHALL** be generated through version-controlled analysis code.

Required pipeline:

```text
immutable raw JSONL under $FAM_RESULTS_DIR
        ↓
digest verification          scripts/verify_digests.py
        ↓
schema validation
        ↓
processed dataset
        ↓
run-level summaries
        ↓
paired comparative analysis
        ↓
tables / figures             make analyse
        ↓
post-experiment import into results/
```

Digest verification runs first. Analysing a dataset whose per-file SHA-256 does not match its manifest is a provenance failure, not a data question.

### Analysis specification versus analysis implementation

Two identifiers, deliberately distinct:

| Identifier | Frozen when | Refers to |
|---|---|---|
| `analysis_spec_version` | at protocol lock | the pre-specified analytical methodology in this document — metric definitions (§19, §30, §31), aggregation rules, the bootstrap procedure (§32), inclusion and exclusion policy (§33–§35), and comparison definitions (§19.4) |
| `analysis_code_commit` | after data collection | the concrete version-controlled implementation of that frozen specification |

Analysis code **MAY** be implemented or corrected after data collection, provided the frozen analysis specification is unchanged. Changing the specification is a methodological revision under §3 Phase 4: it increments `analysis_spec_version` and is disclosed.

`analysis_spec_version` and `protocol_version` are independent counters, and ownership is semantic rather than positional — §3 Phase 4 defines what each governs. The rules in this section that transform immutable raw observations into reported results belong to `analysis_spec_version`.

Every processed artifact **SHALL** carry `analysis_spec_version`, `analysis_code_commit` and `protocol_git_commit`. The three answer different questions and none substitutes for another.

Manual spreadsheet modification **SHALL NOT** be required to reproduce reported results.

---

## 41. E4 — Human + LLM Functional Validation

### Objective

Two objectives:

1. demonstrate that the same federated communication architecture can host a real LLM-backed autonomous execution layer;
2. complete the validation of C4, by placing an actual human in the three-party federated topology E1 exercises programmatically.

E4 is not a performance experiment. It is, however, load-bearing for C4: a failed E4 leaves C4 partially unevidenced.

### Topology

E4 uses a three-party federated room, the same shape as E1:

```text
@actual-human:hs-a.test      real person, standard Matrix client
@human-role-b:hs-b.test      programmatic human-role participant
@llm-agent:hs-b.test         LLM-backed agent
```

This is deliberate. In E0–E3 every participant is a program, and `HumanParticipant` denotes a role rather than a person. Running E4 in a three-party room is what makes scope §18 condition 3 — at least three mixed participants sharing a persistent interaction space — true at least once with an actual human present, rather than satisfied only by a programmatic stand-in.

### Independent repetitions

E4 **SHALL** be executed:

```text
3 independent sessions
```

A fresh three-participant federated room **SHALL** be created for each independent E4 session. The three formal sessions therefore use three distinct rooms.

C4's completion half rests entirely on E4. A single unreplicated session would leave the one requirement that depends on a person being present as the weakest-evidenced in the study, so E4 is replicated even though it produces no measurements.

### Procedure

Per session:

1. configure the existing agent runtime with `LLMExecutor`;
2. retain the same communication architecture used in E1–E3;
3. create a fresh three-party federated room;
4. join the actual human through a standard Matrix client on Domain A;
5. join the programmatically controlled human-role participant on Domain B;
6. join the LLM-backed agent on Domain B;
7. confirm and record that all three required participants are members of the room;
8. the actual human sends at least 3 natural-language requests to the agent;
9. for each request the remote agent receives it, the LLM executor generates a response, and the response is sent through the room;
10. the human observes at least 3 corresponding valid LLM-backed responses;
11. record the evidence listed below.

A longer multi-turn interaction **MAY** be performed.

### Evidence

Record per session:

- model / provider identifier;
- date;
- code and configuration version;
- room ID and room version;
- the three participant identities, with confirmed membership;
- event IDs of the ≥3 requests and ≥3 responses;
- sanitized transcript, plus a screenshot where it adds anything.

### Acceptance criteria

A session passes when all of the following hold:

- the room contains three participants across two federation domains — an actual person using a standard Matrix client on one domain, a programmatically controlled human-role participant on the other, and the LLM-backed agent;
- membership of all three is confirmed and recorded;
- the actual human sends at least 3 natural-language requests and observes at least 3 corresponding valid LLM-backed responses;
- the agent uses the same communication runtime as E0–E3, with only the executor replaced.

E4 passes overall when:

```text
3 / 3 valid sessions pass
```

*Valid* carries its §35 meaning, and E4 defines no validity rules of its own. A session lost to an external dependency or to the human client's own environment falls under `external_dependency_or_client_environment_failure` (§35) and may be repeated. A session in which the tested architecture fails to carry the interaction is a valid experimental failure and stays in the record. §35, *Where the boundary falls*, gives the worked cases.

E4 remains functional validation only. No performance, model-quality or statistical generalization claim **SHALL** be derived from these sessions.

E4 evidences LLM-runtime compatibility and completes C4. It does not establish D3 as a general architectural property (scope §7).

Because C4 depends on E4, a failed E4 is not merely a missing smoke test: if E4 fails for a reason attributable to the communication architecture, C4 remains partially unevidenced and the failure **SHALL** be reported as such rather than reclassified.

---

## 42. Experiment-to-Claim Mapping

| Experiment | Primary evidence |
|---|---|
| E0 | C1, C2 |
| E1 | C4 structural portion, C5 |
| E2 | C3 |
| E3 | RQ3 |
| E4 | C4 completion with an actual human, LLM-runtime applicability |

RQ1 is supported primarily by E0 plus runtime persistence demonstrated in E2.

RQ2 is supported by E1 and E2.

RQ4 is answered through architecture and implementation mapping rather than a standalone experiment.

C4 requires E1 and E4 jointly. Neither is sufficient alone (scope §6 C4, *Empirical support*).

---

## 43. Minimum Evidence Matrix

| Claim | Required evidence | Failure condition |
|---|---|---|
| Stable agent identity | same Matrix identity after restart | new identity required |
| Non-privileged operation | ordinary Client-Server APIs only | privileged runtime integration required |
| Persistent multi-party room, structural | three participants share a room across two domains — E1 | topology cannot operate |
| Persistent multi-party room, with an actual human | three-party room containing a real person, 3/3 E4 sessions | actual human cannot participate through a standard client |
| Native federation | room spans HS-A and HS-B | agent bridge required |
| Durable recovery | `S_recovered == S_sent` | missing/unexpected event |
| Exactly-once logical processing | one ACK per offline request | duplicate/missing processing |
| Bidirectional cross-domain interaction | request/ACK across federation | persistent delivery failure |
| Federation characterization | valid paired local/federated runs | comparable benchmark impossible |
| LLM compatibility | actual human ↔ remote LLM agent | communication architecture must change |

---

## 44. Negative Result Policy

A technically valid failure is scientific evidence.

The project **SHALL NOT** modify C1–C5 merely to reinterpret a failed result as successful.

A core failure **SHALL** be classified as potentially:

1. falsifying part of the architectural hypothesis;
2. Matrix/Synapse-specific;
3. configuration-specific;
4. workload-specific;
5. an architectural limitation.

The manuscript **SHALL** report material failures where relevant.

---

## 45. Protocol Acceptance Criteria

The protocol is implementation-ready when the runner supports:

- sequential workloads;
- bounded-concurrency workloads;
- continuous controlled sync;
- warm-up/drain separation;
- logical-interaction timeout;
- paired run execution;
- randomized/counterbalanced ordering;
- rate-limit detection;
- immutable raw results;
- formal manifests;
- Matrix event correlation;
- event-set recovery assertions;
- exact-once logical processing checks;
- an agent telemetry stream joinable by `run_id`;
- completion-time-based window accounting;
- steady-state verification;
- result output written outside the working tree;
- both manifest variants under a common envelope.

Statistical analysis **SHALL** remain outside the benchmark execution loop.

---

## 46. Formal Protocol Lock

Immediately before formal publication data collection, generate:

```text
protocol-lock.json
```

containing at minimum:

```text
protocol_version
analysis_spec_version
git_commit
git_tag
result_schema_version
manifest_schema_version
formal-run host identifier

Synapse config hashes
experiment config hashes
sync configuration
rate-limit configuration

timeout
message payload size
message body padding character
latency workload specification
throughput concurrency levels
warm-up specification
measurement duration
drain period
inter-run quiescence interval
E1 quiescence quiet interval
E2 synchronization timeline limit (gap-recovery trigger)
E2 offline request count

E0 repetition count
E1 repetition count
E2 repetition count
E3 repetition counts

generated run-order schedule
random seed
```

The corresponding Git tag **SHALL** identify the exact implementation used.

The formal-run validity taxonomy in §35 is closed at protocol lock. Adding a class after the lock is an execution-affecting change: it increments `protocol_version` (§3 Phase 4) and is disclosed.

---

## 47. Frozen Formal Experiment Set

After protocol lock, publication-critical execution consists of:

```text
E0:
3 independent runs
40 interactions/run

E1:
3 independent fresh-room runs
40 interactions/run  (20 cross-domain + 20 same-domain)

E2:
3 independent fresh-room runs
100 offline sends/run

E3 low-load latency:
20 paired local/federated blocks          = 40 runs
50 warm-up + 500 measured interactions/run

E3 throughput:
C = 8, 32
20 paired local/federated blocks per C    = 80 runs
10 s warm-up (not drained)
60 s measured
10 s drain

E4:
3 independent sessions
three-party federated room, a distinct fresh room per session (SHALL)
>= 3 human requests and >= 3 valid LLM responses per session
3 / 3 sessions must pass

Total formal runs: 3 + 3 + 3 + 40 + 80 = 129, plus 3 E4 sessions.
```

No additional experiment is required for the first manuscript unless a methodological flaw prevents interpretation of these results.

---

## 48. Deferred Experimental Scope

The following remain outside the publication critical path:

- multi-agent coordination;
- multiple autonomous agents in one room;
- agent relevance/routing;
- custom Matrix event types;
- substrate-derived LLM memory;
- E2EE;
- malicious participants;
- server crashes;
- federation partitions;
- WAN deployment;
- public-Internet federation;
- XMPP comparison;
- ANP comparison;
- SLIM comparison;
- A2A comparison;
- blockchain identity;
- payments;
- discovery;
- cold restart with no retained transport checkpoint;
- payload-size sensitivity.

---

## 49. Interpretation Boundary

Successful execution permits the manuscript to claim, within the tested Matrix/Synapse environment, that:

- persistent autonomous identity survives runtime restart;
- autonomous participation does not require privileged runtime integration;
- mixed participants can share a persistent federated room;
- interaction history can support runtime interruption/recovery;
- the local and federated communication paths have measured comparative behavior under controlled workload;
- the same communication architecture can host an LLM-backed participant.

It **SHALL NOT** imply:

- universal Matrix performance;
- maximum Matrix throughput;
- Internet-scale federation performance;
- production high availability;
- security guarantees;
- protocol superiority;
- general autonomous-agent scalability.

---

## 50. Final Protocol Freeze Rule

Once:

```text
research-scope.md          = FINAL
testbed-architecture.md    = FINAL
experimental-protocol.md   = FINAL
```

the implementation process **SHALL** treat all three as authoritative.

If implementation conflicts with the frozen specification, the default action is:

```text
report and classify the conflict
```

rather than silently modifying research requirements or experimental procedure.

The protocol may be reopened only if:

1. a technical property makes an experiment impossible as specified;
2. a methodological flaw threatens validity;
3. a dependency/configuration defect materially affects interpretation.

Otherwise implementation proceeds against the frozen protocol.
