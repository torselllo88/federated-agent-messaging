# Implementation Task 01 — Repository Bootstrap, Matrix Testbed, and E0

**Status:** ready for implementation
**Type:** non-normative implementation instruction. It does not amend the frozen specifications.
**Revision:** 2 — incorporates review findings on results layout, evidence hygiene, verifier scope, manifest schema, validity enum, provenance, account set, C2 evidence, and sequencing.

## Authoritative inputs

Read all five before writing implementation code:

- [`../research-scope.md`](../research-scope.md)
- [`../testbed-architecture.md`](../testbed-architecture.md)
- [`../experimental-protocol.md`](../experimental-protocol.md)
- [`../evidence-matrix.md`](../evidence-matrix.md)
- [`../CHANGELOG.md`](../CHANGELOG.md)

These define the research scope, architecture, experimental semantics, validity rules and evidence requirements. Do not reinterpret or expand them.

If an implementation constraint conflicts with a frozen requirement, **stop and report the conflict**. Do not silently redesign the experiment, and do not modify a frozen document to make the implementation fit.

---

## 0. What this task is

Task 01 is the first implementation vertical slice. It is not "the minimum needed for E0".

**E0 itself exercises only Domain A**: Human A and the local agent both live on `hs-a.test`, and no federated behaviour occurs.

Domain B, its PostgreSQL instance, the research TLS material and federation transport readiness are included **deliberately and in advance**, to retire the infrastructure risk that would otherwise land in the middle of the E1 task. Do not describe this infrastructure as required by E0.

The realistic effort profile follows from that: most of the work is federation transport — private CA, `.test` name resolution, TLS on the federation listener, signing-key discovery — not the E0 loop, which is a few hundred lines.

---

## 1. Implementation sequence

Implement in this order. Steps 1 and 2 exist to fail fast.

| # | Step |
|---|---|
| 1 | compatibility spike (§2) |
| 2 | choose and validate the TLS deployment mode (§3) |
| 3 | repository skeleton, bootstrap, result schemas |
| 4 | two homeservers + two PostgreSQL instances |
| 5 | environment verification |
| 6 | account provisioning |
| 7 | reusable Matrix client layer |
| 8 | deterministic agent + dual telemetry |
| 9 | E0 |
| 10 | analysis, manifest and digest validation |

---

## 2. Compatibility spike — do this first

Before building the vertical slice, run a small development-only spike:

1. Synapse 1.159.0 against PostgreSQL 16;
2. matrix-nio 0.26.0 as the client;
3. create a room explicitly requesting room version 12;
4. verify the resulting room version is exactly 12;
5. verify encryption is disabled;
6. verify the ordinary-client join / send / receive path.

Record the outcome in the completion report. The spike uses `publication_data = false`.

If this frozen combination does not behave as specified, **stop and report the conflict** before building around it. Everything downstream depends on it.

---

## 3. TLS deployment mode — decide before the Compose topology

Test the simplest direct Synapse federation-TLS configuration the architecture permits. If direct TLS works reliably, use it.

If it proves operationally problematic, use the architecture-permitted transparent TLS-termination proxy as deployment scaffolding ([architecture §2.4, §6](../testbed-architecture.md)). Such a proxy **MUST NOT**:

- interpret Matrix events;
- synthesize events;
- transform application payloads;
- replace native Server-Server semantics;
- act as an application bridge.

Record the selected mode and the rationale in the completion report. Do not redesign federation networking silently later.

---

## 4. Scope

Implement only:

1. repository and application skeleton;
2. Docker Compose infrastructure;
3. Synapse A + PostgreSQL A;
4. Synapse B + PostgreSQL B;
5. research TLS and bootstrap configuration;
6. federation **transport and bootstrap** readiness verification;
7. account provisioning;
8. minimal Matrix client abstraction;
9. deterministic agent runtime;
10. transport-checkpoint persistence sufficient for ordinary restart;
11. runner-side instrumentation;
12. agent-side instrumentation;
13. external result-directory handling;
14. E0;
15. reproducibility commands;
16. the frozen manifest and result schemas.

Do not implement room-level federation experiments.

---

## 5. Frozen technical baseline

Use the versions and decisions frozen in [`testbed-architecture.md` §4, §41](../testbed-architecture.md): Python 3.12, Synapse 1.159.0, PostgreSQL 16, matrix-nio 0.26.0, Docker Compose V2, Matrix room version 12, plaintext rooms, ordinary `m.room.message`, deterministic request/ACK behaviour, JSONL raw observations.

Pin every dependency. Record container digests. No floating tags for publication-relevant services.

---

## 6. Repository structure

The frozen architecture ([§31](../testbed-architecture.md)) is the source of truth. Approximately:

```text
federated-agent-messaging/
├── README.md
├── LICENSE
├── .gitignore
├── Makefile
├── docker-compose.yml
│
├── docs/
│   ├── research-scope.md
│   ├── testbed-architecture.md
│   ├── experimental-protocol.md
│   ├── evidence-matrix.md
│   ├── CHANGELOG.md
│   └── tasks/
│
├── infrastructure/
│   ├── synapse-a/
│   ├── synapse-b/
│   ├── postgres/
│   └── tls/
│
├── src/
│   └── fam/
│       ├── agent/
│       ├── executors/
│       ├── participants/
│       ├── instrumentation/
│       └── common/
│
├── scripts/
│   ├── bootstrap.py
│   ├── verify_environment.py
│   ├── collect_environment.py
│   └── verify_digests.py
│
├── experiments/
│   └── e0_baseline.py
│
└── results/
    ├── README.md
    └── schemas/
```

Do not create empty directories to match this diagram.

`results/manifests/`, `results/processed/` and `results/figures/` are **post-campaign import targets** ([architecture §34](../testbed-architecture.md), [protocol §3 Phase 5](../experimental-protocol.md)). They are not written during runs and need not exist yet. `results/schemas/` is tracked and exists before the campaign.

`results/raw/` is never the authoritative output location.

---

## 7. External results directory

All run-generated artifacts go outside the Git worktree, at `FAM_RESULTS_DIR`.

Reject formal-style execution when the variable is undefined, resolves inside the tracked repository, or is not writable. Each of those maps to `execution_precondition_violation` ([protocol §35](../experimental-protocol.md)).

Use the frozen layout exactly ([protocol §37](../experimental-protocol.md)):

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
└── evidence/
```

There is **no** top-level `$FAM_RESULTS_DIR/e4/`. E4 raw data lives at `raw/e4/`; `evidence/` is reserved for evidence artifacts such as E4 transcripts and screenshots.

Only the directories Task 01 actually needs must be populated, but the path conventions must already match the frozen layout.

E0 produces two streams per run — runner-side JSONL and agent-side JSONL — correlated by `run_id`.

---

## 8. Docker infrastructure

Two independent domains, `hs-a.test` and `hs-b.test`, each with its own Synapse instance, PostgreSQL instance, signing key, TLS identity, configuration and persistent volume. They share no database, signing key, Synapse state, user namespace or access token. Shared Docker networking is permitted for transport only.

---

## 9. Federation readiness verification

`make verify` establishes only the transport and bootstrap layer ([protocol §4.1](../experimental-protocol.md), [architecture §16](../testbed-architecture.md)). It **MAY** check:

- both homeservers reachable;
- DNS and internal name resolution;
- the TCP federation path;
- the TLS federation handshake;
- normal server identity and signing-key discovery.

It **MUST NOT** perform:

- federated room creation or join as a federation test;
- cross-domain room membership propagation checks;
- persistent room-event propagation checks;
- federated history-visibility checks.

Those are what E1 evaluates for C5. Prevalidating them would let a genuine C5 failure surface as an environment problem instead of a finding.

`make verify` must additionally perform the frozen environment-verifier responsibilities:

- frozen configuration hash verification;
- confirmation that planned client-side limits are non-binding for the formal workload envelope.

---

## 10. TLS material

A private research CA may be generated automatically. If used: certificates carry the relevant names; trust configuration is **mandatory**, not optional (`federation_custom_ca_list`); generation is reproducible and documented; no private key is ever committed. Prefer the simplest working implementation.

---

## 11. Bootstrap

A privileged bootstrap stage, separate from experiment execution, may generate configuration, initialize Synapse and PostgreSQL, generate TLS material, create accounts, provision credentials, collect sanitized configuration hashes and verify transport readiness.

After bootstrap, experiment processes hold no Synapse admin credential, no PostgreSQL credential, no signing key and no privileged server filesystem access.

---

## 12. Configuration provenance

Bootstrap and environment tooling produce sanitized configuration fingerprints: canonicalized, secret-stripped configuration, SHA-256 ([architecture §33](../testbed-architecture.md)).

Collect at minimum the Synapse A config hash, Synapse B config hash, experiment config hash and sync config hash.

**The experiment runner never reads Synapse configuration.** The verifier and bootstrap layer produce sanitized frozen values and hashes; manifests reference them. This restriction is itself part of the C2 evidence.

---

## 13. Rate limits

Configure client-side limits clearly non-binding for the planned campaign, identically on both domains. Verify and document at minimum:

```text
rc_message
rc_room_creation
rc_joins
rc_invites
rc_login
rc_federation
```

A fresh room per formal run is mandatory across the campaign, so the room-creation and join limits bind long before the message limit does.

Do not disable or alter federation behaviour to make future benchmarks look better. Frozen values must be inspectable in sanitized provenance.

---

## 14. Accounts

Provisioning is written once. Create the full frozen set:

```text
@human-a:hs-a.test
@human-b:hs-b.test

@agent-local:hs-a.test
@agent:hs-b.test

@benchmark-human:hs-a.test
@benchmark-agent-local:hs-a.test
@benchmark-agent-fed:hs-b.test

@actual-human:hs-a.test
@human-role-b:hs-b.test
@llm-agent:hs-b.test
```

Where the frozen documents use different exact MXIDs, follow the documents. Provisioning these accounts does not mean implementing E1–E4.

---

## 15. Matrix client abstraction

A minimal reusable layer over matrix-nio — not a general Matrix framework:

```text
login / authenticate
create_room(room_version=12)
join_room
send_message
continuous_sync
receive_event
disconnect
reconnect
```

Usable later by HumanParticipant, the local agent, the federated agent and the runner. Keep Matrix transport code separate from executor logic.

---

## 16. Synchronization

One continuous long-poll sync loop, no artificial polling delay, frozen filters, transport-checkpoint persistence.

Do not implement the E2 forced-gap recovery experiment. E0 creates no gap: nothing is sent while the agent is down. If gap-recovery code appears naturally in the client layer, keep it isolated and unexercised until E2.

---

## 17. Agent runtime

An ordinary external Matrix client:

```text
connect()  synchronize()  filter()  execute()  respond()  checkpoint()  instrument()
```

No access to Synapse internals, databases or admin APIs.

---

## 18. Deterministic executor

Implement `DeterministicExecutor` only. Exactly one deterministic ACK per valid REQUEST, using the frozen message format. No LLM executor beyond a trivial interface placeholder if one is needed.

Design message construction so the frozen fixed 256-byte E3 body can be introduced cleanly later. No custom Matrix event types.

---

## 19. Idempotency and correlation

Programmatic sends use deterministic Matrix `txnId` values. Track experiment ID, run ID, sequence ID, transaction ID and Matrix event ID. A retry of the same logical send reuses its original `txnId`.

Agent telemetry must record enough to establish, after the fact:

```text
one logical request → one processing operation → one logical ACK
```

---

## 20. Instrumentation

Instrumentation is part of this task, not a later enhancement. Two append-only JSONL streams.

**Runner stream** — at minimum: experiment, `run_id`, `sequence_id`, `room_id`, participant identities, transaction IDs, event IDs, primary monotonic timestamps, raw outcome.

**Agent stream** — at minimum: experiment, `run_id`, `sequence_id`, request `event_id`, receive and process observations, response `txn_id`, response `event_id` where known, duplicate-processing decision, transport and checkpoint information.

Raw records carry execution facts and the frozen metadata needed to reconstruct a classification — never the classification itself. Do not persist a derived analytical metric as an authoritative raw field where it can be recomputed ([protocol §22](../experimental-protocol.md)).

---

## 21. Manifests and digests

Use the frozen common manifest envelope from [protocol §38](../experimental-protocol.md) from the start. Do not invent an E0-only shape that will need migration for E1–E4.

At minimum:

```text
manifest_type
manifest_schema_version
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
execution-host identifier
start timestamp
completion status
validity classification
raw artifact references and SHA-256 digests
```

E0 uses `manifest_type = automated_experiment_manifest`. Fields may be omitted only where the frozen schema defines them as optional. §38 is authoritative; this list is a convenience.

Every result stream has a SHA-256 digest recorded in the manifest — for E0 that is the runner JSONL and the agent JSONL. Implement `scripts/verify_digests.py` so stored digests can be checked independently.

---

## 22. Validity handling

Implement the machine-readable validity-class enum exactly as frozen in [protocol §35](../experimental-protocol.md). That section defines named class identifiers in a table and is the single authoritative source; the code enum mirrors it and does not become a second source of truth.

Free-text validity classification is not acceptable. Human-readable explanation may accompany the enum value but never replaces it.

Do not invent additional classes. Operational setup errors and tested-system failures stay distinct:

```text
incorrect setup / invalid execution environment  ≠  tested-system failure
```

If the exact frozen enum cannot be identified from the documents, report that as a specification conflict before implementing the schema.

If a failure's mapping is uncertain, record it and report the ambiguity. Do not silently exclude the run.

---

## 23. E0

Implement only E0, per [protocol §14](../experimental-protocol.md): three independent fresh-room runs.

Each run:

1. create a fresh room version 12 room on Domain A;
2. assert room version is exactly 12, encryption is disabled, and all other frozen room configuration matches;
3. join Human A;
4. join Local Agent;
5. send 20 sequential deterministic requests;
6. verify 20 matching ACKs;
7. record the agent Matrix identity;
8. stop the agent runtime;
9. restart the same runtime;
10. authenticate using the same Matrix identity;
11. confirm room membership remains present;
12. send 20 additional requests;
13. verify 20 matching ACKs.

No administrator operation may occur between the pre-restart and post-restart phases.

A previous run's room must not be reused to satisfy step 2.

**Acceptance.** A run passes only if all 40 requests receive exactly one matching ACK, the agent identity is unchanged, room membership survives the restart, only ordinary client APIs are used, and no privileged runtime integration is required. E0 passes overall at 3/3 valid runs.

A failure of the correctly configured system remains an experimental failure. Do not retry it until it succeeds.

---

## 24. C2 evidence

E0 exists to evidence C1 and C2. C1 is demonstrated by the restart sequence. C2 needs positive evidence, not the absence of a contradiction.

Record, as part of the E0 run evidence, that:

- the agent runtime configuration contains no Synapse admin credential;
- the agent container or process holds no PostgreSQL credentials;
- the agent has no mounted Synapse data or configuration directories;
- the agent has no server signing keys or privileged filesystem access.

Additionally perform **one** explicit privilege-negative probe: use the agent's ordinary Matrix access token against a harmless Synapse admin endpoint and record that access is denied. Do not probe destructive or state-changing admin operations.

This probe is supporting evidence only. A single denied endpoint is not sufficient proof of C2 by itself. C2 evidence is the combination of ordinary Client-Server functionality, absence of privileged credentials and mounts, explicit rejection of privileged API access, and successful E0 operation.

---

## 25. Evidence hygiene

Every Task 01 E0 run carries:

```text
publication_data = false
```

These runs validate implementation behaviour. They are **not** publication evidence. They must not:

- increment evidence counters in [`evidence-matrix.md`](../evidence-matrix.md);
- mark C1 or C2 evidence as collected;
- tick any minimum-publication-success checkbox;
- modify formal evidence status anywhere.

Record them separately as development validation. Formal evidence is collected only in the later protocol-locked campaign ([protocol §3](../experimental-protocol.md) Phases 3–4).

---

## 26. Command surface

```bash
make setup      # initialize environment and provisioned identities
make verify     # environment + federation transport/bootstrap readiness + config hashes + rate-limit envelope
make e0         # the frozen E0 procedure
make analyse    # currently available analysis and validation over result artifacts
```

`make verify` must not pre-test E1/C5 room behaviour.

For this slice `make analyse` may be limited to schema validation, E0 pass/fail summary, digest verification and a basic run summary. No E3 statistics.

---

## 27. Processed-artifact provenance

`make analyse` already follows the frozen provenance model ([protocol §40](../experimental-protocol.md)). Every generated processed artifact records or references:

- `analysis_spec_version`
- `analysis_code_commit`
- `protocol_git_commit`
- source raw artifact digest(s)
- source run ID(s)

Task 01 needs no statistical analysis, but its E0 summaries and validation outputs use the final provenance pattern from the start.

---

## 28. Documentation

Add a concise developer and reproducibility path to `README.md`:

```bash
export FAM_RESULTS_DIR=...
make setup
make verify
make e0
make analyse
```

Document host prerequisites. Do not install host-level software silently. If something must be installed outside Docker and the Python project dependencies, report what is required, why, and the exact installation command. Do not modify the machine globally without explicit user action.

---

## 29. Testing

Automated tests where they earn their place, not a parallel test project. At minimum:

- deterministic message parsing;
- deterministic executor behaviour;
- result schema serialization;
- SHA-256 digest verification;
- the path guard preventing `FAM_RESULTS_DIR` inside the repository;
- correlation and duplicate handling.

Integration verification happens through E0 itself.

---

## 30. Non-goals

Do not implement E1, the E2 formal experiment, E3, E4, LLM integration, federated room interaction, custom Matrix events, E2EE, Chums integration, TRC-8004, payments, agent discovery, multi-agent coordination, RAG, vector databases, public federation, WAN deployment or performance tuning.

Do not optimize prematurely.

---

## 31. Clean-room requirement

Implementation sources of truth are the frozen research documents, the public Matrix specification, public Synapse documentation, public matrix-nio documentation and other public open-source documentation.

Do not inspect, copy from, import from or derive implementation code from proprietary Chums repositories. If such repositories are visible in the surrounding filesystem, ignore them.

---

## 32. Completion report

Do not report "done". Provide:

**A. Implemented components** — brief inventory.

**B. Command surface** — exact commands now available.

**C. Acceptance status** — architecture criteria A1–A13, reported only for those actually tested. A3 (native federation) is out of scope for this slice by design; say so rather than omitting it. Plus `E0 3/3`.

**D. Result location** — where development artifacts were written.

**E. Evidence** — E0 summary: three run IDs, request/ACK counts, agent identity before and after restart, result digests, and the C2 evidence set from §24.

**F. Spike outcomes** — compatibility spike result and the selected TLS mode with rationale.

**G. Deviations** — any deviation from the frozen architecture or protocol.

**H. Unresolved issues** — anything that may affect E1.

**I. Repository diff summary** — files created and changed.

Do not modify frozen research documents. If a genuine implementation conflict is found, report it separately instead of resolving it silently.

---

## 33. Definition of done

From a clean development checkout with documented prerequisites:

```bash
export FAM_RESULTS_DIR=<external writable directory>
make setup
make verify
make e0
make analyse
```

works reproducibly, with `E0 = 3/3 PASS`, and:

- stable local-agent Matrix identity across runtime restart;
- no privileged agent operation, positively evidenced per §24;
- runner and agent telemetry, correlated by `run_id`;
- immutable external raw outputs under the frozen layout;
- manifests using the frozen envelope, with SHA-256 provenance;
- no E1/C5 behaviour pre-tested by environment verification;
- no formal evidence status modified.

Only then does implementation proceed to federated room interaction and E1.
