from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction

from analysis.horizon import remaining_hours, validate_deadline
from analysis.rational_system import solve_rational_system
from dispatch.plan_store import StoredPlan
from dispatch.port_bindings import ReceivedPort, Transfer
from dispatch.terminals import Terminal
from exchange.piece_store import ReceivedPiece


@dataclass(frozen=True)
class ArrivalEdge:
    hours: int
    probability: Fraction
    destination: ReceivedPort | Terminal


ArrivalGraph = Mapping[ReceivedPort, tuple[ArrivalEdge, ...]]


def build_arrival_graph(plan: StoredPlan, pieces: Iterable[ReceivedPiece]) -> ArrivalGraph:
    bindings = {binding.source: binding.destination for binding in plan.bindings}
    graph: dict[ReceivedPort, list[ArrivalEdge]] = {}
    for received in pieces:
        evidence = received.piece.evidence
        received_id = received.received_id
        for entry in evidence.entries:
            graph[ReceivedPort(received_id, entry)] = []
        for row in evidence.rows:
            destination = bindings[ReceivedPort(received_id, row.exit)]
            target = destination.target if isinstance(destination, Transfer) else destination
            graph[ReceivedPort(received_id, row.entry)].append(
                ArrivalEdge(row.hours, row.probability, target)
            )
    return {entry: tuple(edges) for entry, edges in graph.items()}


def _instant_transfers(graph: ArrivalGraph) -> dict[ReceivedPort, dict[ReceivedPort, Fraction]]:
    transfers: dict[ReceivedPort, dict[ReceivedPort, Fraction]] = {}
    for entry, edges in graph.items():
        targets = defaultdict(Fraction)
        for edge in edges:
            if edge.hours == 0 and isinstance(edge.destination, ReceivedPort) and edge.probability:
                targets[edge.destination] += edge.probability
        transfers[entry] = dict(targets)
    return transfers


def _solve_hour(
    constants: dict[ReceivedPort, Fraction],
    transfers: dict[ReceivedPort, dict[ReceivedPort, Fraction]],
    predecessors: Mapping[ReceivedPort, set[ReceivedPort]],
) -> dict[ReceivedPort, Fraction]:
    active = {entry for entry, contribution in constants.items() if contribution}
    pending = list(active)
    while pending:
        for predecessor in predecessors.get(pending.pop(), set()) - active:
            active.add(predecessor)
            pending.append(predecessor)

    # Outside this set the least nonnegative solution is zero. Within it every
    # state can reach a contribution that exits the current-hour recurrence.
    entries = sorted(active)
    zero = Fraction(0)
    coefficients = [
        [Fraction(source == target) - transfers[source].get(target, zero) for target in entries]
        for source in entries
    ]
    values = solve_rational_system(coefficients, [constants[entry] for entry in entries])
    return dict(zip(entries, values))


def release_probability(graph: ArrivalGraph, start: ReceivedPort, deadline: int) -> Fraction:
    validate_deadline(deadline)
    if not isinstance(start, ReceivedPort) or start not in graph:
        raise ValueError('starting entry is outside the dispatch plan')
    instant = _instant_transfers(graph)
    predecessors = defaultdict(set)
    for source, targets in instant.items():
        for target in targets:
            predecessors[target].add(source)

    history: list[dict[ReceivedPort, Fraction]] = []
    zero = Fraction(0)
    for available in range(deadline + 1):
        constants = dict.fromkeys(graph, zero)
        for entry, edges in graph.items():
            for edge in edges:
                remaining = remaining_hours(available, edge.hours)
                if remaining is None:
                    continue
                if edge.destination is Terminal.RELEASE:
                    constants[entry] += edge.probability
                elif isinstance(edge.destination, ReceivedPort) and edge.hours > 0:
                    constants[entry] += edge.probability * history[remaining].get(edge.destination, zero)
        history.append(_solve_hour(constants, instant, predecessors))
    return history[deadline].get(start, zero)
