# Research Scope Freeze v1.1 FINAL

**Project:** Federated Agent Messaging
**Repository:** `federated-agent-messaging`
**Date:** 2026-09-02
**Status:** FROZEN
**Amends:** v1.0 — see [`CHANGELOG.md`](CHANGELOG.md)
**Purpose:** Fix the research hypothesis, contribution boundary, architectural requirements, research questions, evidence requirements, and experimental scope of the first publication.

---

## 1. Research Problem

Modern autonomous-agent systems require communication mechanisms that support interaction across runtime, organizational, and infrastructure boundaries.

A common approach is to introduce communication infrastructure specifically designed for agents: dedicated protocols, RPC mechanisms, gateways, message transports, discovery layers, or agent-oriented middleware.

An alternative is to reuse capabilities already present in mature general-purpose messaging systems.

This work investigates a specific version of that alternative:

> Can a mature federated messaging infrastructure with persistent replicated interaction spaces serve directly as shared communication infrastructure for human and autonomous AI participants?

The focus is not merely on transporting messages between agents.

The proposed architectural model treats a persistent messaging space itself as part of the interaction substrate: participant identity, membership, durable events, asynchronous recovery, and cross-domain propagation are supplied by the messaging infrastructure rather than recreated inside each agent application.

Matrix is used as the reference implementation because its room model combines persistent event history, room state, client-level participation, and federation across homeservers.

The empirical findings of this study apply directly to the tested Matrix/Synapse implementation.

Applicability to other messaging infrastructures is an architectural transferability argument rather than an experimentally demonstrated result.

---

## 2. Prior-Art Boundary

The research scope explicitly acknowledges that several individual elements of the proposed architecture have substantial prior art.

### 2.1 Mature Messaging Infrastructure for Software Agents

The use of general-purpose instant-messaging infrastructure for software-agent communication is not new.

SPADE demonstrated the use of XMPP/Jabber as the communication layer of a multi-agent platform as early as 2006.

Subsequent SPADE work developed this model further, including distributed deployment, federation, ordinary XMPP identities, and human-agent interaction.

Therefore, this publication **SHALL NOT** claim novelty for:

- using an existing instant-messaging protocol for agents;
- representing an agent as a messaging user;
- federated communication between agent identities;
- human-agent communication through a messaging system.

### 2.2 Persistent and Shared Interaction State

Persistent shared state and coordination mechanisms are also established concepts in multi-agent systems, including blackboard architectures, tuple spaces, coordination artifacts, and related middleware.

Therefore, the publication **SHALL NOT** claim that persistence or shared state between autonomous actors is itself novel.

### 2.3 Matrix and LLM-Based Agents

Prior peer-reviewed work has already demonstrated an LLM-backed AI assistant using Matrix as its decentralized communication interface.

Pap and Oniga (*Sensors*, 2024) implemented an LLM-based eHealth chatbot communicating through Matrix and discussed group rooms and decentralized deployment.

Therefore, this publication **SHALL NOT** claim:

- the first AI or LLM system using Matrix;
- the first human-AI interaction through Matrix;
- the first use of Matrix rooms for an AI application.

### 2.4 Modern Agent Messaging Protocols

Modern agent infrastructure is also increasingly adding messaging-system capabilities.

ANP defines cross-domain federated instant messaging between agents, including direct and group communication.

SLIM provides a distributed messaging layer for agent-oriented protocols and includes group communication and routing capabilities.

Other contemporary agent protocols and architectures similarly address distributed and heterogeneous agent interaction.

Therefore, this publication **SHALL NOT** claim novelty merely from:

- federation;
- group communication;
- asynchronous communication;
- agent-to-agent communication across domains;
- many-to-many communication.

---

## 3. Target Contribution

Subject to final related-work verification before submission, the target contribution is:

> A systematic architectural formulation and empirical evaluation of a replicated, room-centric federated messaging substrate as persistent shared interaction infrastructure for mixed human and autonomous AI participants across federation domains.

The contribution is based on the composition and evaluation of these properties rather than on any individual property in isolation.

The study specifically examines an architecture in which:

- autonomous participants use ordinary messaging identities and client interfaces;
- persistent multi-party rooms represent shared interaction spaces;
- relevant interaction history and room state are maintained by the messaging substrate;
- interaction spaces extend across distinct federation domains through native messaging federation;
- autonomous runtimes may disappear and later reconstruct pending interaction from the persistent substrate;
- the additional cost of the federated path is empirically characterized against a controlled same-domain baseline.

The architectural contribution is therefore more specific than "agents over federated messaging."

The study investigates **persistent federated interaction spaces as an agent communication substrate**.

No claim of being the first system to implement this concept **SHALL** be made unless a later systematic novelty review provides sufficient evidence.

---

## 4. Definitions

### 4.1 Autonomous Agent

Within this study, an autonomous agent is:

> a software actor that monitors communication events and can determine and execute a response without per-interaction human intervention.

The internal decision mechanism is outside the communication architecture being studied.

For controlled experiments, this mechanism may be deterministic in order to eliminate LLM latency and output variability as confounding variables.

A separate functional validation uses an LLM-backed execution layer.

### 4.2 First-Class Communication Participant

Within this publication, the term *first-class communication participant* has a deliberately restricted operational meaning.

A first-class participant has:

- a stable communication identity;
- membership in persistent interaction spaces;
- access through standard non-privileged participant interfaces;
- the ability to send and receive persistent communication events;
- access to interaction history according to the substrate's access rules.

The term does not imply feature parity with a complete human-oriented messaging client.

### 4.3 Persistent Interaction Space

A persistent interaction space is a named or otherwise identifiable communication space whose membership and durable interaction state are maintained by the communication substrate independently of an individual autonomous runtime.

In the reference implementation, this concept is instantiated as a Matrix room.

### 4.4 Federation Domain

A federation domain is an independently addressable messaging domain represented by a separately configured homeserver instance with its own server identity and credentials.

The experimental testbed uses multiple separately configured federation domains.

Because all laboratory infrastructure is controlled by the researchers, the experiment models administrative independence rather than requiring the homeservers to be operated by different real-world organizations.

---

## 5. Central Hypothesis

> A mature federated messaging infrastructure with persistent replicated interaction spaces can serve as a shared multi-party communication substrate in which humans and autonomous AI agents participate as persistent communication actors across federation domains.

The hypothesis concerns communication architecture.

It does not assume that the messaging substrate provides:

- reasoning;
- agent execution;
- discovery;
- external identity;
- reputation;
- payment;
- economic settlement.

---

## 6. Core Architectural Requirements

C1–C5 constitute the minimum architectural requirements required to evaluate the central hypothesis.

### C1 — Persistent Actor Identity

Human and autonomous participants **SHALL** have stable, addressable communication identities within the addressing model of the federation.

An autonomous participant's communication identity **SHALL** remain stable across termination and restart of its execution runtime.

**Rationale**

An agent represented exclusively by a process identifier, transient session, or application-local backend identifier is not itself a persistent participant of the communication substrate.

**Observable acceptance criteria**

C1 is satisfied when:

- the agent has a stable network-visible identifier;
- another participant can address the agent through that identifier;
- runtime termination does not delete or replace the communication identity;
- restarting the runtime allows the same communication identity to be resumed.

External or cryptographically verifiable identity is not required.

### C2 — Non-Privileged Participation

During normal operation, an autonomous agent **SHALL** participate through a standard non-privileged participant interface.

Normal operation **SHALL NOT** require:

- messaging-server administrative access;
- modification of messaging-server source code;
- an agent-specific server-side plugin;
- an agent-specific bridge required for ordinary communication.

Initial account provisioning is outside this requirement.

**Rationale**

If continued agent operation depends on privileged integration with a particular server deployment, the agent remains an application-specific server component rather than an independently deployable communication participant.

**Observable acceptance criteria**

After provisioning, the autonomous runtime can:

- authenticate;
- join an interaction space;
- receive events;
- send events;
- disconnect and reconnect;

using interfaces available to ordinary clients.

### C3 — Durable Asynchronous Interaction

Persistent interaction **SHALL** survive temporary absence of the autonomous execution runtime.

Events produced while the runtime is unavailable **SHALL** remain retrievable after reconnection, according to the retention, ordering, and access semantics of the messaging substrate.

**Rationale**

Autonomous runtimes cannot be assumed to remain continuously online.

If interaction state exists only inside the active runtime, temporary runtime failure destroys or interrupts the conversational process.

**Observable acceptance criteria**

C3 is satisfied when:

1. the agent joins a persistent interaction space;
2. its execution runtime is terminated while the messaging infrastructure remains operational;
3. other participants generate persistent events;
4. the agent runtime is restarted;
5. the agent retrieves the relevant missed events;
6. it resumes processing without creation of a new communication identity or interaction space.

C3 does not evaluate messaging-server failure or arbitrary federation partition recovery.

### C4 — Persistent Multi-Party Interaction Space

The substrate **SHALL** support persistent interaction spaces containing multiple mixed participant types.

The space **SHALL** maintain, according to substrate semantics:

- persistent identity of the space;
- membership information;
- durable interaction events;
- existence independent of an individual agent runtime.

**Rationale**

The proposed architecture is not merely a bilateral human-to-agent request endpoint.

The persistent interaction space itself acts as communication infrastructure shared by multiple actors.

**Observable acceptance criteria**

At least three participants, including at least:

- one human participant;
- one autonomous participant;

can participate in the same persistent space.

Membership and accessible history remain available across ordinary participant disconnection and autonomous-runtime restart.

The core study does not require multiple autonomous agents.

**Empirical support**

C4 is evidenced by two experiments jointly and is not fully established by either alone:

- **E1** validates the structural / substrate portion — a persistent three-participant space spanning two federation domains — using programmatically controlled standard Matrix user accounts;
- **E4** completes validation using an actual human with a standard Matrix client in the same three-party federated interaction topology.

The requirement is about actual humans and autonomous participants. It is **NOT** weakened to "human-role participant". Full empirical support for C4 requires E1 **and** E4.

### C5 — Federated Persistent Interaction Space

A persistent interaction space **SHALL** extend across distinct federation domains using the messaging substrate's native federation mechanism.

Relevant persistent events and interaction-space state **SHALL** be propagated between participating domains according to the guarantees of the underlying substrate.

No agent-specific cross-domain bridge **SHALL** be required.

**Rationale**

Simple cross-domain addressing or message forwarding is not sufficient to evaluate the proposed architecture.

The key property under investigation is that a persistent interaction space itself spans federation domains and is maintained by the messaging infrastructure.

**Observable acceptance criteria**

Participants belonging to at least two separately configured federation domains can:

- join the same persistent interaction space;
- exchange events bidirectionally;
- observe compatible membership state;
- access persistent events delivered through their respective homeservers;
- continue interaction using local access to their respective federation domains.

After the tested system reaches quiescence, participating domains expose compatible membership and delivered-event state for the interaction being evaluated.

The study does not claim:

- universal historical equivalence between servers;
- global total ordering;
- strong consistency;
- correctness under arbitrary network partitions;
- correctness of the federation protocol itself;
- messaging-server high availability.

---

## 7. Secondary Design Properties

These properties are architecturally relevant but are not required to establish the central hypothesis.

They **SHALL NOT** delay the core experimental program.

### D1 — Substrate-Derived Conversational Context

An autonomous runtime may reconstruct working conversational context from persistent interaction-space history instead of depending exclusively on a private conversation database.

This may reduce dependence on runtime-local conversational state.

It may also simplify runtime recovery or relocation.

This study does not require D1 to be experimentally evaluated before submission.

### D2 — Typed Semantic Extensibility

Agent-specific machine-readable semantics may be represented using extensible messaging events while the underlying substrate remains agent-agnostic.

Any such representation must satisfy the encoding and federation requirements of the substrate.

D2 is not required for the core publication.

### D3 — Execution-Layer Independence

The communication substrate should remain independent of the internal execution technology used by the autonomous participant.

The architecture therefore does not require:

- a particular LLM;
- a particular provider;
- a particular agent framework;
- a particular tool protocol.

Controlled communication experiments use deterministic execution.

A separate functional scenario validates compatibility with an LLM-backed runtime.

D3 as a general architectural property remains secondary and is **NOT** experimentally established by this study.

E4 is mandatory, but only as evidence that the communication architecture can host an LLM-backed execution layer. It is not a validation of D3 in general.

---

## 8. Secondary Correctness Properties

### K1 — Relevance Determination

Multi-party spaces may require autonomous actors to determine whether a particular event requires their action.

This becomes particularly important with multiple autonomous actors.

K1 is outside the mandatory experimental scope.

### K2 — Context Isolation

If substrate-derived context is used, interaction history from an unrelated or inaccessible space must not be incorporated into another space's context.

K2 follows from D1 and is outside the mandatory experimental scope.

### K3 — Semantic Event Conformance

If custom machine-readable events are introduced, they must remain valid under substrate encoding and federation rules.

K3 follows from D2 and is outside the mandatory experimental scope.

---

## 9. Research Questions

### RQ1 — Persistent Non-Privileged Agent Participation

> Can an autonomous AI agent operate as a persistent communication participant of a mature messaging infrastructure using only standard non-privileged participant mechanisms?

**Primary requirements:** C1, C2
**Supporting requirement:** C3

RQ1 distinguishes a persistent messaging participant from:

- an application-local bot process;
- a privileged server component;
- a dedicated agent gateway endpoint.

Evidence must demonstrate stable communication identity and ordinary participant-level operation.

### RQ2 — Federated Persistent Interaction

> Can mixed human and autonomous participants belonging to distinct federation domains maintain a shared persistent multi-party interaction space through native messaging federation?

**Primary requirements:** C3, C4, C5

Within the experimental scope, correct participation includes:

- cross-domain membership;
- bidirectional persistent event delivery;
- accessible durable interaction history;
- autonomous-runtime interruption and recovery;
- compatible interaction state after quiescence.

RQ2 does not evaluate arbitrary federation partitions or server failure.

### RQ3 — Federation Overhead

> What communication overhead does the federated interaction path introduce relative to an equivalent same-domain deployment under a controlled workload?

Primary measurements are:

- end-to-end interaction latency;
- p50, p95, and p99 latency;
- observed throughput under the tested bounded-concurrency workload;
- delivery or interaction failure rate.

Secondary measurements **MAY** include:

- CPU utilization;
- memory utilization;
- network traffic.

The controlled benchmark is intended primarily to characterize additional federation and server-processing cost.

If both homeservers execute within the same controlled infrastructure, the results **SHALL NOT** be presented as estimates of real-world Internet or geographically distributed federation latency.

RQ3 establishes characteristics only for the documented deployment and workload envelope.

It does not establish universal federation-performance bounds.

### RQ4 — Messaging-Primitive Reuse Boundary

> Which communication capabilities represented by C1–C5 can be realized directly through existing messaging primitives, and where are agent-specific extensions or additional layers required?

RQ4 is primarily an architectural-analysis question.

It is not a mandatory standalone performance experiment.

Candidate substrate primitives include:

- participant identities;
- room membership;
- durable event history;
- asynchronous event delivery;
- addressing;
- access control;
- persistent room state;
- federation.

RQ4 does not attempt to define all communication requirements of autonomous agents.

It considers only the architectural capabilities explicitly investigated by this study.

---

## 10. Requirement-to-RQ Mapping

| Requirement | RQ1 | RQ2 | RQ3 | RQ4 |
|---|---|---|---|---|
| **C1** Persistent actor identity | Primary | Supporting | — | Analyzed |
| **C2** Non-privileged participation | Primary | Supporting | — | Analyzed |
| **C3** Durable asynchronous interaction | Supporting | Primary | — | Analyzed |
| **C4** Persistent multi-party space | — | Primary | Workload context | Analyzed |
| **C5** Federated persistent space | — | Primary | Primary comparison | Analyzed |
| **D1** Substrate-derived context | — | Optional | — | Secondary |
| **D2** Typed extensibility | — | Optional | — | Secondary |
| **D3** Execution independence | Control | Control | Control | Secondary |

---

## 11. Minimum Experimental Evidence

The central hypothesis is considered empirically supported within the bounded scope of this study if the reproducible testbed demonstrates all of the following:

1. an autonomous runtime operates under a stable messaging identity;
2. normal operation requires no privileged messaging-server integration;
3. at least three mixed participants share a persistent interaction space — the structural case established programmatically in E1, with at least one instance in E4 containing an actual human;
4. participants belonging to at least two federation domains access the same federated interaction space;
5. persistent events traverse native federation and remain accessible through the participating domains;
6. interaction survives temporary termination and restart of the autonomous runtime;
7. local and federated interaction paths are quantitatively compared under an equivalent deterministic workload;
8. the same communication architecture can host an LLM-backed autonomous execution layer.

These observations establish feasibility and characterize the reference architecture.

They do not establish architectural optimality.

---

## 12. Mandatory Experimental Program

Only E0–E4 are on the pre-submission critical path.

### E0 — Same-Domain Functional Baseline

**Topology**

Human participant and autonomous participant communicate through one homeserver.

**Purpose**

Validate:

- C1;
- C2;
- basic instrumentation;
- deterministic autonomous execution;
- same-domain performance baseline.

### E1 — Federated Persistent Multi-Party Interaction

**Minimum topology**

Two separately configured Matrix homeservers representing two federation domains.

At least:

- Human A on Domain A;
- Human B on Domain A or Domain B;
- Agent on the opposite domain from at least one human participant.

All participate in the same persistent room.

**Purpose**

Validate:

- the structural portion of C4 — see §6 C4, *Empirical support*;
- C5;
- cross-domain membership;
- bidirectional event propagation;
- visibility of persistent interaction state through both domains.

E1 uses programmatically controlled standard Matrix user accounts. C4 is completed by E4.

This is the primary feasibility experiment supporting the architectural contribution.

### E2 — Autonomous Runtime Interruption and Recovery

**Procedure**

1. establish federated interaction;
2. stop the autonomous runtime;
3. keep both homeservers operational;
4. generate persistent room events;
5. restart the autonomous runtime;
6. recover missed events;
7. resume interaction.

**Purpose**

Validate C3 and the separation between:

- persistent communication state;
- transient autonomous execution state.

Messaging-server outage is explicitly excluded.

### E3 — Controlled Federation Overhead

Execute equivalent deterministic workloads under two configurations.

**Same-domain topology**

`Human → Homeserver A → Agent`

**Federated topology**

`Human → Homeserver A ⇄ Homeserver B → Agent`

**Primary outputs**

- end-to-end latency distributions;
- throughput;
- error/failure rate.

**Optional outputs**

- CPU;
- memory;
- network volume.

The benchmark **SHALL** use the same deterministic agent behavior in both configurations.

No LLM invocation **SHALL** be included in primary performance measurements.

### E4 — LLM-Backed Functional Validation

Replace the deterministic decision function with a minimal LLM-backed execution function while preserving the same messaging architecture.

**Purpose**

Two purposes:

1. demonstrate that the architecture is compatible with an actual contemporary AI-agent execution layer rather than only with deterministic automation;
2. complete the validation of C4, by placing an actual human in the same three-party federated topology E1 exercises programmatically.

E4 is:

- a functional validation;
- not a model-quality experiment;
- not a performance benchmark.

A minimal successful scenario is sufficient for purpose 1. Purpose 2 additionally requires that the room contain three participants across two federation domains, at least one of them an actual person.

---

## 13. Interpretation of Experimental Independence

The testbed contains multiple federation domains but is operated within a controlled research environment.

Therefore, the study demonstrates:

> protocol and deployment separation between federation domains

rather than proving organizational independence through separately owned real-world infrastructure.

The architecture permits independent domain operation.

The laboratory experiment models this boundary using:

- separate homeserver instances;
- separate server identities;
- separate credentials;
- native federation communication;
- ordinary client access.

The publication **SHALL** make this distinction explicit.

---

## 14. Reference Implementation Boundary

Matrix/Synapse is the reference implementation.

The empirical study therefore establishes that the proposed architecture can be instantiated using this technology.

Matrix supplies relevant properties including:

- persistent rooms and events;
- persistent room state;
- ordinary client-server participation;
- federation between homeservers;
- propagation of persistent room events between participating homeservers;
- historical event retrieval mechanisms.

The publication **SHALL** distinguish:

**Architecture-level argument**

A persistent federated messaging substrate may provide reusable interaction infrastructure for humans and autonomous participants.

**Implementation-level evidence**

Matrix/Synapse provides one concrete implementation in which the architecture is experimentally evaluated.

The study **SHALL NOT** infer that another federated messaging protocol automatically satisfies C1–C5.

---

## 15. Industrial Reference Boundary

Chums Chat serves as an industrial reference implementation that motivated the research abstraction.

The reproducible research artifact **SHALL NOT** contain proprietary Chums code or code derived from proprietary implementation details.

The public research testbed **SHALL** use only:

- public specifications;
- public APIs;
- open-source infrastructure;
- independently written research code.

Chums may contribute, where publication permission exists:

- architectural observations;
- deployment experience;
- sanitized production measurements;
- evidence of real-world implementation.

Industrial evidence **SHALL** be clearly separated from controlled testbed evidence.

Production and experimental measurements **SHALL NOT** be directly compared unless their environments and workload definitions make such comparison methodologically valid.

The paper remains reproducible without access to Chums.

---

## 16. Novelty and Claim Boundary

The principal intended contribution is architectural and empirical.

Performance results support the architecture but are not the primary novelty claim.

The manuscript **SHALL NOT** claim novelty based solely on:

- messaging for agents;
- agents represented as messaging users;
- XMPP-like or Matrix-like agent participation;
- federation;
- group communication;
- persistent communication;
- shared state;
- human-agent rooms;
- LLM interaction through Matrix;
- asynchronous delivery.

The manuscript **SHALL** specifically acknowledge the relevant historical and contemporary lines of work, including:

- XMPP/SPADE-style multi-agent communication;
- traditional multi-agent shared-state and coordination systems;
- existing Matrix-based AI/LLM systems;
- contemporary federated agent-messaging protocols and transports.

The intended differentiation is the systematic formulation and controlled empirical evaluation of persistent replicated interaction spaces as the shared communication substrate across human and autonomous participants belonging to federation domains.

This formulation remains a target contribution, not an unconditional first-of-kind claim.

Before submission, a final related-work review **SHALL** determine the strongest defensible wording.

Phrases such as:

- "the first"
- "the first federated"
- "the first Matrix-based"

**SHALL NOT** appear unless independently justified by the final literature review.

---

## 17. Explicit Non-Goals

The first publication does not attempt to establish or comprehensively evaluate:

- decentralized agent identity;
- cryptographically verifiable identity;
- reputation;
- trust infrastructure;
- agent discovery;
- payment protocols;
- economic settlement;
- shared economic state;
- universal agent-event schemas;
- agent reasoning quality;
- LLM comparison;
- tool-use protocols;
- multi-agent coordination algorithms;
- routing between multiple autonomous agents;
- end-to-end encryption;
- security properties;
- adversarial behavior;
- abuse prevention;
- messaging-server failure recovery;
- arbitrary federation partitions;
- geographic WAN performance;
- production-scale high availability;
- universal scalability limits;
- protocol superiority over A2A, ANP, SLIM, SPADE/XMPP, or other agent communication systems.

These may appear only as limitations, discussion, or follow-up research directions.

---

## 18. Minimum Publication Success Condition

The empirical core of the publication is complete when the study demonstrates:

**1. Persistent autonomous participation**
An autonomous participant maintains a stable communication identity across runtime restart.

**2. Non-privileged operation**
Normal agent participation uses ordinary messaging interfaces.

**3. Persistent mixed-participant space**
At least three mixed participants share a persistent interaction space.

Established jointly: E1 for the structural case using programmatically controlled standard accounts, E4 for at least one instance containing an actual human (§6 C4, *Empirical support*).

**4. Actual federation**
The space spans at least two separately configured federation domains using native federation.

**5. Persistent cross-domain interaction state**
Relevant persistent interaction events are accessible through the participating domains after federation propagation.

**6. Runtime recovery**
An autonomous runtime can disappear, return, retrieve missed interaction, and resume processing.

**7. Controlled federation characterization**
The same-domain and federated interaction paths are quantitatively compared under a deterministic workload.

**8. AI-runtime compatibility**
At least one functional scenario uses an LLM-backed execution layer.

This establishes LLM-runtime compatibility only. It does not establish D3 as a general architectural property.

No additional experimental capability is necessary for the first publication.

---

## 19. Scope Freeze Rule

The following are frozen:

- Central Hypothesis;
- C1–C5;
- RQ1–RQ3;
- E0–E4;
- explicit non-goals.

RQ4 is retained as secondary architectural analysis.

D1–D3 and K1–K3 remain optional.

Implementation findings **SHALL NOT** automatically expand the research scope.

If implementation reveals an interesting capability outside C1–C5, it **SHALL** be recorded as:

- observation;
- limitation;
- discussion;
- future work;
- candidate follow-up publication.

The research scope may be reopened before submission only if:

- direct prior art is discovered that materially invalidates the target contribution;
- a core assumption is shown to be technically incorrect;
- C1–C5 cannot be experimentally evaluated using the selected reference implementation.

Otherwise, the project proceeds directly to testbed implementation and evidence collection.

---

## 20. Prior-Art Anchors for Subsequent Related-Work Work

The following works/protocol families must be included in the subsequent formal related-work analysis:

1. Gregori, Palanca Cámara, and Aranda Bada, "A Jabber-based Multi-Agent System Platform," AAMAS 2006. DOI: [10.1145/1160633.1160866](https://doi.org/10.1145/1160633.1160866).
2. Palanca et al., "SPADE 3: Supporting the New Generation of Multi-Agent Systems," *IEEE Access*, 2020. DOI: [10.1109/ACCESS.2020.3027357](https://doi.org/10.1109/ACCESS.2020.3027357).
3. Pap and Oniga, "eHealth Assistant AI Chatbot Using a Large Language Model to Provide Personalized Answers through Secure Decentralized Communication," *Sensors*, 2024. DOI: [10.3390/s24186140](https://doi.org/10.3390/s24186140).
4. Agent Network Protocol, including the federated instant-messaging specification.
5. Secure Low-Latency Interactive Messaging (SLIM), current IETF Internet-Draft.
6. Recent surveys and taxonomies of AI-agent communication and interoperability protocols.
7. Relevant Matrix architecture and federation literature.
8. Classical shared-state and coordination models for multi-agent systems.

These sources define the minimum comparison set rather than the complete literature review.
