from collections import defaultdict
from fractions import Fraction

from analysis.rational_system import solve_rational_system
from exchange.evidence import (
    MAX_EVIDENCE_HOUR,
    ScanEvidence,
    ScanJointExit,
    ScanSupport,
)
from exchange.first_exit import _exit_reachable_states
from source.routes import RouteRevision


def _instant_matrix(transitions, positions):
    """Settled-mass closure: x_s = seed_s + sum of instant inflows into s.

    Row ``s`` subtracts every instantaneous state edge ``t -> s`` so a one-step
    split into a state with a delayed exit preserves that mass, while genuine
    instantaneous loops are still resolved by the rational linear system.
    """
    size = len(positions)
    matrix = [[Fraction(row == column) for column in range(size)] for row in range(size)]
    for state, edges in transitions.items():
        if state not in positions:
            continue
        for edge in edges:
            if (
                edge.target_kind == 'state'
                and edge.hours == 0
                and edge.probability
                and edge.target in positions
            ):
                matrix[positions[edge.target]][positions[state]] -= edge.probability
    return matrix


def _solve(matrix, constants):
    if not any(constants):
        return constants
    return solve_rational_system(matrix, constants)


def compute_scan_evidence(revision: RouteRevision) -> ScanEvidence:
    """Freeze per-entry first-scan support and joint first-exit evidence.

    Two pools share a visit-local clock. Unscanned mass (U) follows every edge
    until it reaches a marked state, which fires the first scan, or a public
    exit. Matched mass (T) starts at a scan and follows the states that can
    still reach a public exit, preserving the joint scan-to-first-exit relation.
    Scanned mass with no exit inside the 24-hour window stays in the support.
    Instantaneous classes settle through exact rational linear systems; delayed
    transitions schedule mass at a later visit hour.
    """
    draft = revision.draft
    transitions = draft.transitions
    labels_by_state = dict(draft.scan_labels)
    entries = tuple(sorted(draft.ports.entries))
    exits = tuple(sorted(draft.ports.exits))
    labels = tuple(sorted(set(labels_by_state.values())))

    exit_states = _exit_reachable_states(transitions)
    labeled_states = set(labels_by_state)
    retained = sorted(set(exit_states) | labeled_states)
    u_states = tuple(state for state in retained if state not in labeled_states)
    t_states = tuple(exit_states)
    u_positions = {state: index for index, state in enumerate(u_states)}
    t_positions = {state: index for index, state in enumerate(t_states)}
    u_matrix = _instant_matrix(transitions, u_positions)
    t_matrix = _instant_matrix(transitions, t_positions)

    support_rows = []
    joint_rows = []
    for entry in entries:
        landing = draft.ports.entries[entry]
        if landing not in retained:
            continue

        # Arrivals at a private state during each visit hour, per pool.
        pending_u: dict[int, dict[str, Fraction]] = defaultdict(lambda: defaultdict(Fraction))
        pending_t: dict[tuple[str, int], dict[int, dict[str, Fraction]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(Fraction))
        )
        joint_events: dict[tuple[str, int], dict[int, dict[str, Fraction]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(Fraction))
        )
        support: dict[tuple[str, int], Fraction] = defaultdict(Fraction)

        if landing in labeled_states:
            tag = (labels_by_state[landing], 0)
            support[tag] += Fraction(1)
            if landing in t_positions:
                pending_t[tag][0][landing] += Fraction(1)
        else:
            pending_u[0][landing] += Fraction(1)

        for hour in range(MAX_EVIDENCE_HOUR + 1):
            # First scans reached through delayed U transitions.
            for state, mass in list(pending_u[hour].items()):
                if state in labeled_states and mass:
                    tag = (labels_by_state[state], hour)
                    support[tag] += mass
                    if state in t_positions:
                        pending_t[tag][hour][state] += mass
                    pending_u[hour][state] = Fraction(0)

            # Settle the instantaneous U class.
            u_values = _solve(
                u_matrix, [pending_u[hour].get(state, Fraction(0)) for state in u_states]
            )
            for state, mass in zip(u_states, u_values):
                if not mass:
                    continue
                for edge in transitions[state]:
                    if not edge.probability:
                        continue
                    share = mass * edge.probability
                    if edge.hours > 0:
                        arrival = hour + edge.hours
                        if arrival > MAX_EVIDENCE_HOUR:
                            continue
                        if edge.target_kind == 'state' and edge.target in retained:
                            pending_u[arrival][edge.target] += share
                    elif edge.target_kind == 'state' and edge.target in labeled_states:
                        tag = (labels_by_state[edge.target], hour)
                        support[tag] += share
                        if edge.target in t_positions:
                            pending_t[tag][hour][edge.target] += share
                    # Instant exits and instant edges into retained unlabeled
                    # states are already represented by the settled closure.

            # Settle each matched T class, then schedule its delayed exits.
            for tag, by_hour in pending_t.items():
                t_values = _solve(
                    t_matrix,
                    [by_hour[hour].get(state, Fraction(0)) for state in t_states],
                )
                for state, mass in zip(t_states, t_values):
                    if not mass:
                        continue
                    for edge in transitions[state]:
                        if not edge.probability:
                            continue
                        share = mass * edge.probability
                        if edge.hours > 0:
                            arrival = hour + edge.hours
                            if arrival > MAX_EVIDENCE_HOUR:
                                continue
                            if edge.target_kind == 'exit':
                                joint_events[tag][arrival][edge.target] += share
                            elif edge.target in t_positions:
                                by_hour[arrival][edge.target] += share
                        elif edge.target_kind == 'exit':
                            joint_events[tag][hour][edge.target] += share
                        # Instant edges into states unable to reach an exit keep
                        # the scanned mass open inside the support deficit.

        for (label, scan_hour), mass in support.items():
            if mass:
                support_rows.append(ScanSupport(entry, label, scan_hour, mass))
        for (label, scan_hour), by_hour in joint_events.items():
            for exit_hour, targets in by_hour.items():
                for exit_name, mass in targets.items():
                    if mass:
                        joint_rows.append(
                            ScanJointExit(entry, label, scan_hour, exit_name, exit_hour, mass)
                        )

    support_rows.sort(key=lambda row: (row.entry, row.label, row.hour))
    joint_rows.sort(
        key=lambda row: (row.entry, row.label, row.scan_hour, row.exit, row.exit_hour)
    )
    return ScanEvidence(entries, exits, labels, tuple(support_rows), tuple(joint_rows))
