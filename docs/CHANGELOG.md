# Changelog

Methodology changes to the frozen document set. Each entry records what changed, which documents it touched, and why — the reason matters more than the edit, because a reviewer asking "was this decided before or after you saw the data" is answered by this file plus the Git history.

Versions apply to the frozen set as a whole: `research-scope.md`, `testbed-architecture.md`, `experimental-protocol.md`. The evidence matrix follows them and is not separately versioned.

---

## v1.1 — 2026-09-02

**Status:** frozen. No experimental data existed when these changes were made; nothing here is a post-hoc adjustment to observed results.

### Provenance

v1.0 was reviewed as a corpus for internal contradictions, unimplementable requirements, validity risks and underspecified parameters. The review found four issues that would have broken on first contact with the implementation, two that would have drawn direct methodological challenge at review, and a set of underspecified parameters. This version applies the resulting decisions.

### Summary

| # | Change | Documents | Class |
|---|---|---|---|
| 1 | Human B relocated to Domain B | arch, protocol | validity |
| 2 | E1 request classes accounted for separately | arch, protocol | validity |
| 3 | E1 cross-domain check strengthened from inclusion to equality | protocol | validity |
| 4 | E4 moved to a three-party room; acceptance criterion extended | arch, protocol | validity |
| 5 | Agent telemetry stream introduced | arch, protocol | instrumentation |
| 6 | `offline_send` class introduced for E2 | protocol | correctness |
| 7 | Throughput estimator redesigned as a steady-state completion rate | protocol | measurement |
| 8 | Concurrency set reduced to {8, 32}; C=1 becomes a derived check | protocol | measurement |
| 9 | Formal replication raised from 10 to 20 paired blocks | protocol | statistics |
| 10 | E3 message body frozen at exactly 256 UTF-8 bytes | arch, protocol | control |
| 11 | T3 stamping point defined exactly | arch, protocol | measurement |
| 12 | All run-generated artifacts moved outside the tracked working tree | arch, protocol, README, .gitignore | operability |
| 13 | Rate-limit inventory expanded; verification ownership moved | protocol | operability |
| 14 | Configuration-hash mechanics and ownership defined | arch, protocol | operability |
| 15 | All formal E0–E3 runs pinned to one dedicated Linux host | arch, protocol | validity |
| 16 | Private CA trust raised from MAY to SHALL; client-side trust added | arch | correctness |
| 17 | Concurrency replenishment raised from MAY to SHALL | protocol | correctness |
| 18 | Proxy prohibition narrowed to the Matrix layer (§6 and §2.4) | arch | feasibility |
| 19 | "substrate-consistent order" removed | arch, protocol | terminology |
| 20 | Cold restart declared an explicit non-goal | arch, protocol | scope hygiene |
| 21 | "achievable throughput" corrected in the research scope | scope | terminology |
| 22 | D3 separated from E4 | scope, protocol, matrix | scope hygiene |
| 23 | `protocol-lock.json` field list completed | protocol | reproducibility |
| 24 | Evidence matrix gained run inventory, exclusion log, limitations | matrix | traceability |
| 25 | C4 empirical support split between E1 and E4 | scope, protocol, matrix | scope clarification |
| 26 | Manifest split into a common envelope with two type variants | protocol | traceability |
| 27 | Post-experiment import; four distinct provenance identifiers | arch, protocol, results | reproducibility |
| 28 | Phase 4 post-lock change rule scoped to execution-affecting changes | protocol | operability |
| 29 | `analysis-method version` renamed `analysis_spec_version` and separated from `analysis_code_commit` | protocol, arch, results | reproducibility |
| 30 | E4 raised to three independent sessions with per-session content requirements | protocol, arch, matrix | validity |
| 31 | `counted_in_window` removed from the raw schema; derived during analysis | protocol, arch | reproducibility |
| 32 | Single canonical formal-run validity taxonomy replaces two divergent lists | protocol, matrix | correctness |
| 33 | Validity classes named; two added; blanket precondition-invalidity rule removed | protocol, matrix | correctness |
| 34 | Measurement-window field names standardized to `window_start_ns` / `window_end_ns` | protocol, arch | schema hygiene |
| 35 | Federation readiness precondition narrowed to transport/bootstrap; room checks moved to creation-time assertions | protocol | validity |
| 36 | Invalid-run classes made mutually exclusive by construction | protocol | correctness |
| 37 | Fresh benchmark room per E3 run made mandatory; setup rate limits non-binding | protocol | validity |
| 38 | `analysis_spec_version` removed from `protocol_lock_mismatch`; execution-time provenance added | protocol | reproducibility |
| 39 | Bootstrap federation verification bounded to transport and bootstrap readiness | arch | validity |

---

### Validity

**1 — Human B relocated from `hs-a.test` to `hs-b.test`.**
E1 required verifying that events are accessible "through Domain A and Domain B", but every defined role except the agent lived on Domain A. The only Domain-B viewpoint available was the agent's own report, which is not independent evidence for C5. Moving Human B to Domain B supplies an ordinary-client view of the remote domain using a standard participant interface. Scope §12 already permitted Human B on either domain, so no scope change was required.

**2 — E1 request classes accounted for separately.**
A consequence of change 1: Human B and the agent now share Domain B, so Human B's requests traverse a same-domain path. Only Human A's 20 requests exercise a cross-domain request/response loop. Pooling all 40 into one figure would let a federated failure hide behind same-domain successes, so the two classes are recorded and reported separately, and explicit bidirectional visibility checks were added.

**3 — E1 cross-domain verification strengthened from inclusion to equality.**
v1.0 built the expected event set and confirmed it was present in each domain's view. Such a check is structurally incapable of detecting divergence: an extra, missing or differing event on one side passes. C5 in the scope speaks of *compatible* state, which is an equality claim. The protocol now requires set equality of the experiment-related events and exact equality of expected membership between the two views.

**4 — E4 moved to a three-party room; acceptance criterion extended.**
C4 requires at least one human participant, but in E0–E3 every participant is a program and `HumanParticipant` denotes a role, not a person. As written, the requirement was never satisfied literally anywhere in the study. E4 now runs in the same three-party federated shape as E1, with an actual person on Domain A. The acceptance criterion was extended to record the three-party composition — changing only the topology would have exercised the condition without capturing the evidence.

**25 — C4 empirical support split between E1 and E4.**
C4 requires at least one human participant, but E1's participants are all programs. Two resolutions were available: weaken the requirement to "human-role participant", or split its evidence. The requirement was deliberately *not* weakened — it is about actual humans and autonomous participants, and softening it to buy a clean mapping would have hollowed out the claim the paper makes. Instead C4 now carries an *Empirical support* subsection: E1 validates the structural portion using programmatically controlled standard Matrix accounts, E4 completes validation with an actual human in the same three-party federated topology, and full support requires both. This is the only scope change in v1.1, and it narrows rather than widens what counts as sufficient evidence. Its cost is that E4 becomes load-bearing: a failed E4 now leaves C4 partially unevidenced, so E4 gained explicit failure semantics and, under change 30, replication.

**15 — All formal E0–E3 runs pinned to one dedicated Linux host.**
Two reasons. A virtualized desktop container runtime introduces scheduling noise of the same order as the effect E3 measures. And splitting the formal set across hosts would leave C1–C5 and RQ3 evidenced on different environments with two manifests to reconcile. Development and pilot work remain on a Windows/WSL2 workstation, marked `publication_data = false`. Running the testbed or automated experiment infrastructure on any other host is now an invalid run rather than a noisier one. The rule is scoped explicitly to that infrastructure: E4's actual human may use a standard client from an external workstation, whose host and client version are recorded in the E4 manifest.

### Measurement method

**7 — Throughput redesigned as a steady-state completion rate.**
v1.0 counted only interactions both started and finished inside a 30-second window, discarding up to `C` in-flight interactions at the trailing edge. That censoring is one-sided and grows as absolute throughput falls, so it discarded proportionally more from the slower topology and biased the local-versus-federated ratio against federation. v1.1 runs one continuous closed loop, does not drain between warm-up and measurement, and counts completions inside a 60-second window regardless of when the request was initiated. At steady state the leading edge compensates the trailing edge. Because the window is now defined by completion time, additional record fields became mandatory (`initiated_monotonic_ns`, `completed_monotonic_ns`, window bounds, `phase`, `counted_in_window`), and a first-half/second-half completion-rate diagnostic was added, since the estimator is unbiased only if steady state has actually been reached.

**8 — Concurrency set reduced to {8, 32}.**
At one outstanding interaction, throughput is the reciprocal of mean RTT, which Workload A already measures on ten thousand observations per topology. C=1 was retained as a derived consistency check in analysis rather than as forty additional runs.

**11 — T3 stamping point defined exactly.**
`/sync` returns batches, so "the runner receives the ACK" could reasonably mean batch receipt or per-event processing, and the two produce different distributions. T3 is now stamped at the very start of the runner callback for the matching ACK, after the sync response is parsed and before application processing.

**10 — E3 message body frozen at exactly 256 UTF-8 bytes.**
v1.0's envelope implied a roughly 30-byte payload without ever stating a size, leaving an unstated boundary on RQ3. The body is now an ASCII-only correlation prefix padded with a fixed character to exactly 256 bytes, asserted before sending. ASCII-only makes byte length equal character length, so the padding count is unambiguous and both topologies carry byte-identical payloads. The requirement covers the `m.room.message` body only, not the Matrix event or federation PDU. Payload-size sensitivity is now an explicit non-goal and a stated limitation.

**9 — Formal replication raised from 10 to 20 paired blocks.**
The cluster bootstrap resamples blocks. Ten clusters produce wide intervals with poor coverage. Each latency run takes on the order of a minute and each throughput run about 85 seconds, so the additional statistical footing costs roughly two hours of machine time. Total formal runs rose from 89 to 129.

### Correctness

**6 — `offline_send` class introduced for E2.**
E2 deliberately sends 100 requests while the agent runtime is stopped, but v1.0 required every initiated interaction to resolve into one outcome category under a 10-second timeout, and defined failure rate over initiated interactions. Taken literally, all 100 offline requests would time out and E2 would report total failure by construction. Offline sends are now a distinct class, excluded from the failure-rate denominator, with a response deadline that begins when the restart and recovery phase begins.

**16 — Private CA trust raised from MAY to SHALL; client-side trust added.**
v1.0 required TLS for federation but left trusting the private CA optional. If neither homeserver trusts it, federation does not come up at all — it is a functional precondition, not an option. Synapse provides `federation_custom_ca_list` for this case. Separately, E4's human client must reach the Client-Server API over HTTPS, which requires the CA in that workstation's trust store, `hs-a.test` resolving from it, and the endpoint being reachable outside the Docker network.

**17 — Concurrency replenishment raised from MAY to SHALL.**
If replenishment is optional, concurrency is not held at `C`, the offered load is undefined, and the throughput measurement is uninterpretable.

### Instrumentation and data handling

**5 — Agent telemetry stream introduced.**
E2's required observations — the recovered event set, whether history pagination was invoked, deduplication decisions, per-request processing outcomes — are agent-side facts, but v1.0 described only the runner's record and gave the agent no reporting path. Each run now produces two append-only streams joined by `run_id`. The stream uses no privileged interface; it is the runtime's own record of its own behaviour, so C2 is unaffected. Ordinary-client observation of the remote domain deliberately does *not* come from this stream — it comes from Human B.

**12 — All run-generated artifacts moved outside the tracked working tree.**
v1.0 required a clean Git working tree before every formal experiment while writing raw results into that same tree. After the first run the tree is dirty and every subsequent run fails its own precondition, so a sequence of formal runs was impossible. A first pass moved only raw data out, which reintroduced the same failure through tracked per-run manifests. The rule is therefore total: for the entire formal campaign, raw streams, agent telemetry, per-run manifests, E4 evidence and environment output all go to `$FAM_RESULTS_DIR`, and nothing is written into the working tree while data collection is in progress. Each manifest carries the SHA-256 of both of its raw streams, so the external archive is verifiable file by file rather than only as one blob. This also removes a latent conflict where compressed raw archives would have been silently excluded by `.gitignore`.

**27 — Post-experiment import, with three distinct provenance identifiers.**
Keeping the tree clean is only half of it: the protocol-lock precondition also pins `HEAD` to the lock commit, so committing between runs would break the lock check just as writing between runs breaks the clean-tree check. `HEAD` therefore stays on the lock commit for the whole campaign, and archival copies of manifests, processed datasets and figures are imported afterwards in one separate post-experiment commit. Every imported artifact retains four identifiers that are deliberately not collapsed: `protocol_git_commit` (the lock the data was produced under), the SHA-256 provenance of its raw streams, `analysis_spec_version` (the frozen analytical methodology) and `analysis_code_commit` (the implementation, necessarily committed after the lock). The commit that performs the import is none of the four. Without this separation it is impossible to tell whether a figure changed because the data changed or because the analysis did.

**28 — Phase 4's post-lock change rule scoped to execution-affecting changes.**
As written, Phase 4 required a protocol-version increment and a rerun of affected experiments for "any material code or configuration change", while Phase 5 mandated a post-lock import commit and §40 acknowledged that analysis code is written after the lock. Read literally, implementing the analysis triggered a rerun of the campaign it was meant to analyse. The rule now enumerates what it covers — testbed and runtime code, agent and runner behaviour, infrastructure and workload configuration, instrumentation producing primary observations, frozen protocol parameters — and what it does not: analysis implementation, figure generation, result import, non-normative documentation, archival metadata. The documentation exemption is deliberately narrow — README, `results/README.md` and this changelog, but never the three frozen specifications, whose parameters live inside documentation files and could otherwise be edited under cover of "it is only documentation". A post-lock edit to normative content is classified by what it changes: execution specification increments `protocol_version`, analysis methodology increments `analysis_spec_version`, both if both. Post-collection work remains version-controlled and provenance-linked.

**29 — `analysis-method version` renamed `analysis_spec_version`, separated from `analysis_code_commit`.**
The lock manifest froze an "analysis-method version" before collection while processed artifacts carried an `analysis_code_commit` created after it, and nothing said whether these were the same thing. They are not. `analysis_spec_version` is the pre-specified analytical methodology — metric definitions, aggregation rules, the bootstrap procedure, inclusion and exclusion policy, comparison definitions — frozen at protocol lock. `analysis_code_commit` is the version-controlled implementation of that specification, which may legitimately be written or corrected afterwards. Every processed artifact now carries both, alongside `protocol_git_commit` and raw-stream digests. The two are further declared independent counters with worked examples, because the reflex on any revision is to increment both — which would erase the distinction the rename was made to create. Ownership is defined **semantically, never by section number**: `protocol_version` covers anything affecting how raw observations are generated, recorded or classified during execution; `analysis_spec_version` covers how immutable raw observations are transformed into reported results. An earlier draft assigned ownership by section ranges, which misfiled two rules immediately — the run-validity section contained an execution requirement, and the failure-rate formula sat outside every enumerated range. Section placement may now change without changing ownership.

**30 — E4 raised to three independent sessions.**
Once C4's completion depended on E4 (change 25), the requirement that most depends on a person being present was also the only one evidenced by a single unreplicated observation, while C1, C2, C3 and C5 each required 3/3 runs. E4 is now three independent sessions, fresh room preferred per session, each requiring at least three natural-language requests from the actual human and three corresponding valid LLM-backed responses, with membership of all three participants confirmed and recorded. A distinct fresh three-participant federated room **SHALL** be created per session, so the three sessions use three distinct rooms. E4 passes only at 3/3. It remains functional validation: no performance, model-quality or statistical generalization claim is drawn from it.

**31 — `counted_in_window` removed from the authoritative raw schema.**
The field was required in the raw throughput record while being a direct expression of the throughput estimator, so a single value was owned by both version counters at once. Worse, a later estimator revision would have made already-written raw data wrong, and raw data may not be corrected in place. Raw records now carry execution facts and the frozen window bounds needed to reconstruct a classification — `initiated_monotonic_ns`, `completed_monotonic_ns`, `window_start_ns`, `window_end_ns`, `phase`, the raw outcome and the correlation identifiers — and analysis derives whether an interaction counts. This is what makes the independence of `protocol_version` and `analysis_spec_version` hold in practice rather than only on paper.

**32 — One canonical formal-run validity taxonomy.**
Two sections carried non-identical lists of invalidity reasons: the outlier-policy section held a closed list ("may be excluded **only if**") that was shorter than the open example list in the invalid-run section, which meant excluding a run for protocol-lock mismatch was formally inadmissible. There is now one authoritative closed taxonomy of eight classes, closed at protocol lock, with everything else referencing it and maintaining no list of its own. It is paired with an explicit rule: any failure produced by the correctly configured testbed under the tested workload is an experimental outcome, not an invalid run — the specific move this taxonomy exists to prevent is rerunning until a failure disappears.

**33 — Validity classes named; two classes added; the blanket precondition rule removed.**
Closing the taxonomy exposed two gaps. Several §4 preconditions — dirty worktree, missing or misplaced `$FAM_RESULTS_DIR` — had no class, while §4 nonetheless declared any failed precondition invalid; and E4's ad-hoc rule invalidated sessions for an LLM provider outage or a client trust-store problem, neither of which any class covered. Two classes were added, `execution_precondition_violation` and `external_dependency_or_client_environment_failure`, and the former absorbs the formal-host condition so it is named in exactly one place. All nine classes now carry machine-readable identifiers used verbatim in the run manifest.

The blanket rule "if a precondition fails, the run **SHALL** be classified as invalid" was replaced: a failed precondition invalidates a run only when it maps to a class, and failure of behaviour under evaluation stays an experimental outcome. The authentication precondition was rewritten for the same reason — requiring participants to "authenticate successfully" before a run would have made an authentication failure an invalidation, when authenticating through a standard non-privileged interface is exactly what C2 asserts and E0 measures. It now requires only that accounts were provisioned and credentials are available.

**34 — Measurement-window field names standardized.**
`window_start_ns` and `window_end_ns` are now the only normative names, used identically in raw schemas, run manifests, protocol-lock and schema definitions, analysis code and normative text. The parallel `measurement start` / `measurement end` forms in the run manifest and the `measurement_start` / `measurement_end` forms in the throughput text are gone. Prose may still say "the end of the measurement window"; a data reference says `window_end_ns`.

**35 — Federation readiness narrowed; room checks moved to creation time.**
The federation precondition read "federation connectivity **SHALL** pass environment verification", which would have pre-tested the very behaviour E1 exists to evaluate: a genuine C5 failure could have been filed as an environment problem rather than a finding — the same trap the authentication precondition had. Verification is now bounded to transport and bootstrap readiness — reachability, name resolution, the federation TCP/TLS path, signing-key discovery — and is explicitly forbidden from touching federated room join, membership propagation, event propagation or cross-domain history. Separately, room version and encryption state were preconditions of a "before every formal experiment" list, yet rooms are created during setup and do not exist at precondition time. They are now assertions made immediately after each fresh room is created, with a mismatch classified as `frozen_configuration_error`, and a previous run's room may not be used to satisfy them.

**36 — Invalid-run classes made mutually exclusive.**
A Synapse configuration-hash mismatch fell under three classes at once, while the manifest must name exactly one. The first three classes were redefined along disjoint axes — locked artifact *identity*, frozen configuration *content*, and *operational* prerequisites — so a configuration hash is now unambiguously `frozen_configuration_error` and never `protocol_lock_mismatch`. Mutual exclusivity is achieved by construction rather than by a precedence rule, which would have been another thing to get wrong.

**37 — Fresh benchmark room per E3 run made mandatory.**
E3 said a fresh benchmark room **SHOULD** be created per run, while the room-assertion rule forbids satisfying the frozen-configuration checks with a previous run's room — so a fresh room was already obligatory in effect, at two different normative strengths. E3 now says **SHALL**. The practical consequence is roughly eighty-six room creations across the campaign, which is exactly why `rc_room_creation`, `rc_joins`, `rc_invites` and the related setup limits are now required to be non-binding for the planned campaign and confirmed by the environment verifier alongside `rc_message`.

**38 — `analysis_spec_version` removed from `protocol_lock_mismatch`; execution-time provenance added.**
The class listed `analysis_spec_version` among the identities that must match the lock, but a methodological revision after collection is explicitly legitimate. As written, an implementer comparing the current analysis spec against the lock at analysis time could have invalidated perfectly good runs retroactively. The class now covers execution-time identities only — protocol commit or tag, execution `protocol_version`, frozen raw-result schema version. Run manifests gained `execution_protocol_version`, `execution_analysis_spec_version` and `raw_schema_version`, recorded at execution and never rewritten, so a later analysis may legitimately run under a newer spec provided the revision is disclosed and the raw observations contain what it needs.

**39 — Bootstrap federation verification bounded.**
The architecture listed "verify federation connectivity" among bootstrap responsibilities without bounds, while the protocol had just forbidden pre-testing room-level federation. A bootstrap written to the architecture could reasonably have tested federation by creating a room and joining across domains — prevalidating precisely the behaviour E1 exists to evaluate. Bootstrap verification is now limited to reachability, name resolution, the TCP/TLS federation path and signing-key discovery, with room join, membership propagation, event propagation and history visibility explicitly excluded and reserved for E1/C5.

**26 — Manifest split into a common envelope with two type variants.**
v1.0's single run manifest mandated concurrency, warm-up settings, measurement windows and drain timestamps — none of which exist for E4, whose evidence is a model identifier, a transcript and a screenshot. Forcing E4 into the benchmark shape would have left a manifest full of empty measurement fields, which is an invitation to fill them in. Manifests now share a common envelope and take one of two bodies: `automated_experiment_manifest` for E0–E3 and `human_llm_validation_manifest` for E4.

**13 — Rate-limit inventory expanded; verification ownership moved.**
v1.0 named only `rc_message`, but every formal run uses a fresh room, so `rc_room_creation` and `rc_joins` bind first; `rc_invites` and `rc_login` were added for the same reason. More seriously, v1.0 asked the runner to verify the frozen Synapse rate-limit configuration while forbidding it any server access — an unimplementable requirement that also cut against the C2 evidence. Configuration verification now belongs to the bootstrap and environment verifier; the runner receives the frozen values as manifest data and never reads server configuration.

**14 — Configuration-hash mechanics and ownership defined.**
v1.0 required "Synapse config hash A/B" without saying how it is computed or by whom. Hashes are now the SHA-256 of the canonicalized, secret-stripped configuration document, produced by the bootstrap and environment verifier.

**23 — `protocol-lock.json` field list completed.**
Several parameters were declared freezable but absent from the lock manifest: the E2 synchronization timeline limit that triggers gap recovery, the E2 offline request count, the E1 quiescence quiet interval, the inter-run quiescence interval, the message payload size and padding character, and the formal-run host identifier. The timeline limit matters most: it determines whether C3's evidence exercises the recovery mechanism at all.

### Scope and terminology hygiene

**18 — Reverse-proxy prohibition narrowed.**
v1.0 forbade any reverse proxy. Synapse supports direct TLS but recommends a proxy, so an ordinary deployment difficulty could have forced a reopening of the architecture freeze. The prohibition now targets what actually matters — application bridges, protocol transformation, event rewriting or re-signing, any component participating in Matrix semantics — while permitting a transparent TLS-termination proxy as deployment scaffolding, recorded in the environment manifest when present.

The §2.4 native-federation principle was amended to match. It still read as a blanket ban on relays and forwarding, which contradicted the §6 allowance. The test is now behavioural rather than structural: transport-layer scaffolding is permitted provided it does not interpret, synthesize, transform or replace native Matrix Client-Server or Server-Server communication semantics. Application bridges, custom event relays and experiment-side forwarding remain prohibited.

**19 — "substrate-consistent order" removed.**
The term was undefined and unnecessary. Matrix room history is an event graph, not a globally ordered queue. E2 correctness is event-set equality plus exactly-once processing; no ordering property is claimed or required.

**20 — Cold restart declared an explicit non-goal.**
E2 always restarts with a retained synchronization checkpoint. Recovery from a lost checkpoint is the harder case and is not evaluated. v1.0 implied this; v1.1 states it as a limitation.

**21 — "achievable throughput" corrected in the research scope.**
Scope §9 listed "achievable throughput" among RQ3's primary measurements while the protocol explicitly forbade describing results as achievable or maximum. Two frozen documents contradicted each other verbatim. The scope now reads "observed throughput under the tested bounded-concurrency workload".

**22 — D3 separated from E4.**
The scope treated D3 as a secondary property that must not delay the core program, while also making an LLM-backed execution layer a minimum publication success condition and E4 a critical-path experiment. E4 is mandatory and evidences LLM-runtime compatibility; D3 as a general architectural property remains secondary and is not experimentally established.

**24 — Evidence matrix extended.**
Gained a planned-run inventory with a collected counter, an excluded-and-failed-run log required by protocol §34 and §35, and a stated-limitations table assembled from the design rather than reconstructed at writing time.

### Considered and deliberately not changed

| Item | Decision |
|---|---|
| Matrix room version 12 | Retained. It is the current stable default and matrix-nio exposes `room_version` on room creation. A compatibility smoke test remains on the pilot checklist. |
| E1 with both humans on separate domains from each other | Not adopted. The three-party shape with one participant on Domain A and two on Domain B satisfies C5 and keeps the topology minimal. |
| Payload-size sweep | Not adopted. One fixed size, with the boundary stated as a limitation, rather than a new experiment. |
| Cold-restart recovery experiment | Not adopted. Recorded as a non-goal and a follow-up candidate. |

### Scope changes

One, deliberately narrow. C4 gained an **Empirical support** subsection (scope §6) stating that E1 evidences its structural portion using programmatically controlled standard accounts, that E4 completes it with an actual human, and that full support requires both. The text of the requirement is unchanged and was explicitly *not* weakened to "human-role participant". Scope §11 item 3, §12 E1 and E4, and §18 condition 3 were updated to match, along with the experiment-to-claim mapping, the minimum evidence matrix, the architecture traceability table and A6/A13.

No other scope change was made. The clarification narrows what counts as sufficient evidence for C4; it does not widen the study.

### Unchanged

The central hypothesis, the text of C1–C5, RQ1–RQ4, the E0–E4 program, the explicit non-goals, the prior-art and novelty boundaries, and the industrial reference boundary are unchanged from v1.0.

---

## v1.0 — 2026-09-02

Initial freeze of the research scope, testbed architecture and experimental protocol.
