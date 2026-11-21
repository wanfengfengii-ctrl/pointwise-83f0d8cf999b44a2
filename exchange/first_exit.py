from collections import defaultdict
from collections.abc import Mapping, Sequence
from fractions import Fraction

from analysis.rational_system import solve_rational_system
from exchange.evidence import MAX_EVIDENCE_HOUR, ExitProbability, FirstExitEvidence
from source.routes import RouteRevision, Transition


def _exit_reachable_states(transitions: Mapping[str, Sequence[Transition]]) -> list[str]:
    predecessors = defaultdict(set)
    reachable = set()
    for state, edges in transitions.items():
        for edge in edges:
            if not edge.probability or edge.hours > MAX_EVIDENCE_HOUR:
                continue
            if edge.target_kind == 'exit':
                reachable.add(state)
            else:
                predecessors[edge.target].add(state)
    pending = list(reachable)
    while pending:
        state = pending.pop()
        for parent in predecessors[state] - reachable:
            reachable.add(parent)
            pending.append(parent)
    return sorted(reachable)


def _instant_coefficients(
    transitions: Mapping[str, Sequence[Transition]], positions: dict[str, int]
) -> list[list[Fraction]]:
    matrix = [
        [Fraction(row == column) for column in range(len(positions))]
        for row in range(len(positions))
    ]
    for state, index in positions.items():
        for edge in transitions[state]:
            if edge.target_kind == 'state' and edge.hours == 0 and edge.target in positions:
                matrix[index][positions[edge.target]] -= edge.probability
    return matrix


def compute_first_exits(revision: RouteRevision) -> FirstExitEvidence:
    draft = revision.draft
    entries = tuple(sorted(draft.ports.entries))
    exits = tuple(sorted(draft.ports.exits))
    states = _exit_reachable_states(draft.transitions)
    positions = {state: index for index, state in enumerate(states)}
    entry_positions = {
        entry: positions[state]
        for entry, state in draft.ports.entries.items()
        if state in positions
    }

    # Every retained instantaneous class has a path to an exit or to a delayed step.
    # Edges into discarded states leave probability mass outside the result.
    coefficients = _instant_coefficients(draft.transitions, positions)
    rows = []
    for exit_name in exits:
        by_hour: list[list[Fraction]] = []
        for hours in range(MAX_EVIDENCE_HOUR + 1):
            constants = [Fraction(0)] * len(states)
            for state, index in positions.items():
                for edge in draft.transitions[state]:
                    if not edge.probability or edge.hours > hours:
                        continue
                    if edge.target_kind == 'exit':
                        if edge.target == exit_name and edge.hours == hours:
                            constants[index] += edge.probability
                    elif edge.hours > 0 and edge.target in positions:
                        earlier = by_hour[hours - edge.hours][positions[edge.target]]
                        constants[index] += edge.probability * earlier
            values = solve_rational_system(coefficients, constants) if any(constants) else constants
            by_hour.append(values)
            for entry, index in entry_positions.items():
                if values[index]:
                    rows.append(ExitProbability(entry, exit_name, hours, values[index]))

    rows.sort(key=lambda row: (row.entry, row.exit, row.hours))
    return FirstExitEvidence(entries, exits, tuple(rows))
