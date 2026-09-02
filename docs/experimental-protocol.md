# Experimental Protocol v1.0 FINAL

**Project:** Federated Agent Messaging
**Repository:** `federated-agent-messaging`
**Date:** 2026-09-02
**Status:** PRE-SPECIFIED EXPERIMENTAL PROTOCOL
**Depends on:**

- [`research-scope.md`](research-scope.md) (FROZEN, v1.0)
- [`testbed-architecture.md`](testbed-architecture.md) (FROZEN, v1.0)

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

Any material code or configuration change requires:

1. protocol-version increment;
2. documented rationale;
3. rerunning all affected publication experiments.

---

## 4. General Preconditions

Before every formal experiment:

1. both Synapse homeservers **SHALL** report healthy status;
2. both PostgreSQL instances **SHALL** be operational;
3. federation connectivity **SHALL** pass environment verification;
4. test participants **SHALL** authenticate successfully;
5. experiment rooms **SHALL** use Matrix room version 12;
6. encryption **SHALL** be disabled;
7. no unrelated workload **SHALL** run against either homeserver;
8. Git working tree **SHALL** be clean;
9. the checked-out commit **SHALL** match the protocol lock;
10. environment metadata **SHALL** be recorded;
11. Synapse configuration hashes **SHALL** match the frozen configuration;
12. client-side message rate limiting **SHALL** be confirmed non-binding for the planned workload.

If a precondition fails, the run **SHALL** be classified as invalid rather than as an experimental failure.

---

## 5. Synapse Rate-Limit Policy

Client-side message throttling **SHALL NOT** become the dominant limiting factor in E2 or E3.

The default Synapse client message rate limits are therefore unsuitable for the experimental workloads.

Both homeservers **SHALL** use identical explicitly configured client-message rate limits set sufficiently above the maximum planned experiment load.

The frozen configuration **SHALL** document at minimum:

```text
rc_message
```

and any other client rate limit capable of affecting the experiment.

Formal E3 runs **SHALL** verify that no request failed because of client-side:

```text
M_LIMIT_EXCEEDED
```

unless such behavior is itself caused by an unexpected configuration defect.

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

Secondary human-role participant:

```text
@human-b:hs-a.test
```

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

---

## 12. Failure Rate

For a workload:

```text
failure_rate =
unsuccessful logical interactions
/
initiated logical interactions
```

HTTP or Matrix client retransmission **SHALL NOT** create a new logical interaction.

A logical request producing multiple distinct ACK events **SHALL** be classified as:

```text
duplicate_response
```

and therefore unsuccessful.

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

- C4;
- C5;
- RQ2.

### Topology

```text
@human-a:hs-a.test
@human-b:hs-a.test
@agent:hs-b.test
```

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
12. query relevant room state/history through ordinary client interfaces associated with Domain A and Domain B;
13. verify the expected test-event set is accessible through both domains;
14. verify expected membership state;
15. after all expected events are observed, require an additional 2-second quiet interval.

### Quiescence definition

Quiescence is reached only after:

1. every expected experimental request/ACK event has been observed;
2. required membership state has been confirmed;
3. no experimental interaction remains outstanding;
4. an additional 2-second quiet interval has elapsed.

The quiet interval is not itself evidence of Matrix convergence.

### Acceptance criteria

An E1 run passes when:

- all 40 requests receive exactly one matching ACK;
- the expected request/response event set is accounted for;
- the expected experiment events are accessible through both participating domains;
- expected membership is visible;
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
6. Human A sends exactly 100 deterministic requests while the agent runtime is offline;
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

A fresh benchmark room **SHOULD** be created for each formal run.

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

Throughput is defined as:

> **the number of logical interactions successfully completed during the 30-second measurement window divided by 30 seconds.**

Reported in:

```text
completed interactions / second
```

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
10 formal runs
```

are performed in paired local/federated blocks.

Planned measured observations:

```text
5000 measured interactions per topology
```

before accounting for failures.

---

## 22. E3 Workload B — Concurrent Throughput

Throughput is evaluated at:

```text
C = 1
C = 8
C = 32
```

where `C` is the maximum number of outstanding logical interactions.

Whenever an interaction completes or times out, another request **MAY** be issued to maintain the target concurrency.

### Warm-up

Each run begins with:

```text
10 seconds warm-up
```

At the end of warm-up:

1. no new warm-up requests are issued;
2. all warm-up requests are allowed to complete or timeout;
3. warm-up observations are excluded;
4. the runner seeds up to `C` measured requests;
5. the 30-second measurement window begins.

### Measurement window

Duration:

```text
30 seconds
```

Only interactions that:

```text
complete successfully before measurement_end
```

count toward the primary throughput numerator.

Therefore:

```text
primary throughput =
successful interactions completed inside measurement window
/
30 seconds
```

### Measurement-end behavior

At:

```text
measurement_end
```

the runner **SHALL** stop initiating new requests.

Requests initiated before measurement_end but still outstanding are allowed to continue during a:

```text
10-second drain period
```

subject to the ordinary per-interaction timeout.

A request completing during the drain period:

- does **NOT** count toward the throughput numerator;
- **DOES** count as successful for final interaction-outcome/failure-rate accounting.

This distinction **SHALL** be preserved in raw results.

### Replication

For every:

```text
topology × concurrency
```

combination:

```text
10 formal runs
```

**SHALL** be executed.

Total formal throughput runs:

```text
2 topologies × 3 concurrency levels × 10 runs = 60 runs
```

---

## 23. Throughput Pilot Rule

The planned concurrency levels are:

```text
1, 8, 32
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

Before each publication benchmark batch, the runner **SHALL** verify that the frozen client-side message-rate configuration exceeds the planned offered load.

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
successful interactions completed inside the 30-second measurement window
/
30
```

For each concurrency/topology report:

- median run throughput;
- distribution across runs;
- failure rate;
- number of requests still outstanding at measurement end;
- number completing during drain;
- number ultimately timing out.

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

An entire run may be excluded only if independently classified as invalid due to:

- corrupted instrumentation;
- host failure;
- accidental unrelated workload;
- wrong configuration;
- environment corruption;
- runner defect;
- infrastructure crash unrelated to the tested workload.

Every excluded formal run **SHALL** have a documented reason.

---

## 35. Invalid Run vs Experimental Failure

### Invalid run

Examples:

- wrong config loaded;
- runner crashes because of implementation defect;
- result file corrupted;
- host reboots;
- unrelated workload accidentally executes;
- protocol-lock mismatch.

Invalid runs **MAY** be repeated.

### Valid experimental failure

Examples:

- Matrix request times out;
- federated interaction fails under tested load;
- ACK is not delivered;
- duplicate logical response occurs;
- E2 fails to recover an event;
- valid request is processed twice;
- tested system reaches a documented internal constraint.

Valid failures **SHALL** remain in the dataset.

They **SHALL NOT** be silently rerun or discarded.

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

Recommended structure:

```text
results/
└── raw/
    ├── e0/
    ├── e1/
    ├── e2/
    ├── e3/
    │   ├── latency/
    │   └── throughput/
    └── e4/
```

Every formal run **SHALL** produce an immutable result set.

Example:

```text
results/raw/e3/latency/
    20260910T142212Z_local_block01.jsonl
    20260910T142945Z_federated_block01.jsonl
```

---

## 38. Formal Run Manifest

Every run **SHALL** produce a manifest containing:

```text
experiment
protocol version
Git commit
publication_data
topology
run ID
block ID
workload
concurrency
warm-up settings
measurement settings
sync configuration
timeout
room ID
room version
participant identities
rate-limit configuration reference
environment manifest
start timestamp
measurement start
measurement end
drain end
completion status
```

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

host OS
host kernel
CPU model
logical CPU count
available RAM

Synapse config hash A
Synapse config hash B
experiment config hash
sync config hash
```

Secrets **SHALL NOT** be included.

---

## 40. Data Processing Pipeline

Publication tables and figures **SHALL** be generated through version-controlled analysis code.

Required pipeline:

```text
immutable raw JSONL
        ↓
schema validation
        ↓
processed dataset
        ↓
run-level summaries
        ↓
paired comparative analysis
        ↓
tables / figures
```

Manual spreadsheet modification **SHALL NOT** be required to reproduce reported results.

---

## 41. E4 — Human + LLM Functional Validation

### Objective

Demonstrate that the same federated communication architecture can host a real LLM-backed autonomous execution layer.

E4 is not a performance experiment.

### Topology

```text
Actual human
   ↓
standard Matrix client
   ↓
hs-a.test
   ↓
persistent federated room
   ↓
hs-b.test
   ↓
LLM-backed agent
```

### Procedure

1. configure the existing agent runtime with `LLMExecutor`;
2. retain the same communication architecture used in E1–E3;
3. connect an actual human through a standard Matrix client;
4. join the federated room;
5. human sends a natural-language request;
6. remote agent receives the request;
7. LLM executor generates a response;
8. response is sent through the room;
9. human observes the response.

A short multi-turn interaction **MAY** be performed.

### Evidence

Record where appropriate:

- model/provider identifier;
- date;
- code/config version;
- room ID;
- relevant event IDs;
- sanitized transcript or screenshot.

### Acceptance criterion

E4 passes when an actual human successfully completes at least one natural-language request/response interaction with the remote LLM-backed agent through the federated architecture.

No performance or model-quality claim is derived from E4.

---

## 42. Experiment-to-Claim Mapping

| Experiment | Primary evidence |
|---|---|
| E0 | C1, C2 |
| E1 | C4, C5 |
| E2 | C3 |
| E3 | RQ3 |
| E4 | AI-runtime applicability / D3 |

RQ1 is supported primarily by E0 plus runtime persistence demonstrated in E2.

RQ2 is supported by E1 and E2.

RQ4 is answered through architecture and implementation mapping rather than a standalone experiment.

---

## 43. Minimum Evidence Matrix

| Claim | Required evidence | Failure condition |
|---|---|---|
| Stable agent identity | same Matrix identity after restart | new identity required |
| Non-privileged operation | ordinary Client-Server APIs only | privileged runtime integration required |
| Persistent multi-party room | three mixed participants share room | topology cannot operate |
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
- exact-once logical processing checks.

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
git_commit
git_tag
result_schema_version

Synapse config hashes
experiment config hashes
sync configuration
rate-limit configuration

timeout
latency workload specification
throughput concurrency levels
warm-up specification
measurement duration
drain period

E0 repetition count
E1 repetition count
E2 repetition count
E3 repetition counts

generated run-order schedule
random seed
analysis-method version
```

The corresponding Git tag **SHALL** identify the exact implementation used.

---

## 47. Frozen Formal Experiment Set

After protocol lock, publication-critical execution consists of:

```text
E0:
3 independent runs
40 interactions/run

E1:
3 independent fresh-room runs
40 interactions/run

E2:
3 independent fresh-room runs
100 offline requests/run

E3 low-load latency:
10 paired local/federated blocks
50 warm-up + 500 measured interactions/run

E3 throughput:
C = 1, 8, 32
10 paired local/federated blocks per C
10 s warm-up
30 s measured
10 s drain

E4:
at least one actual-human / LLM-agent functional interaction
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
- discovery.

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
