"""E3 benchmark topologies.

experimental-protocol.md §18 and §27: one implementation serves both
conditions. Only four things may differ between them —

    agent identity
    agent homeserver
    benchmark room
    communication topology

— and this module is the only place where any of them is resolved. Everything
downstream receives a :class:`BenchmarkTopology` and behaves identically, so a
topology-specific code path cannot be introduced by accident.
"""

from __future__ import annotations

from dataclasses import dataclass

from fam.common.frozen import E3_TOPOLOGY_FEDERATED, E3_TOPOLOGY_LOCAL

#: §3 of the Task 05 specification, §18 of the protocol. The sender is the
#: same identity on the same homeserver in both conditions; only the agent
#: moves. That is what makes the difference between the two runs the
#: federation boundary and nothing else.
BENCHMARK_SENDER = "@benchmark-human:hs-a.test"
BENCHMARK_AGENT_LOCAL = "@benchmark-agent-local:hs-a.test"
BENCHMARK_AGENT_FEDERATED = "@benchmark-agent-fed:hs-b.test"


@dataclass(frozen=True)
class BenchmarkTopology:
    name: str
    sender: str
    agent: str
    #: Whether the request/response path crosses the federation boundary.
    crosses_federation: bool

    @property
    def receiver_role(self) -> str:
        return "federated_agent" if self.crosses_federation else "local_agent"

    @property
    def participants(self) -> dict[str, str]:
        return {"benchmark_sender": self.sender, "benchmark_agent": self.agent}


LOCAL = BenchmarkTopology(
    name=E3_TOPOLOGY_LOCAL,
    sender=BENCHMARK_SENDER,
    agent=BENCHMARK_AGENT_LOCAL,
    crosses_federation=False,
)

FEDERATED = BenchmarkTopology(
    name=E3_TOPOLOGY_FEDERATED,
    sender=BENCHMARK_SENDER,
    agent=BENCHMARK_AGENT_FEDERATED,
    crosses_federation=True,
)

TOPOLOGIES = {LOCAL.name: LOCAL, FEDERATED.name: FEDERATED}


def topology(name: str) -> BenchmarkTopology:
    try:
        return TOPOLOGIES[name]
    except KeyError:
        raise ValueError(
            f"unknown E3 topology {name!r}; frozen values are "
            f"{sorted(TOPOLOGIES)}"
        ) from None
