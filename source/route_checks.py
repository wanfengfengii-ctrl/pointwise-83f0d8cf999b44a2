from collections.abc import Mapping, Sequence
from fractions import Fraction

from source.ports import validate_ports
from source.routes import RouteDraft, Transition


def validate_scan_labels(scan_labels: Mapping[str, str], states: set[str]) -> None:
    if not isinstance(scan_labels, Mapping):
        raise ValueError('scan labels must be a mapping')
    for state, label in scan_labels.items():
        if not isinstance(state, str) or state not in states:
            raise ValueError(f'scan label for {state!r} refers to an unknown state')
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f'scan label for {state!r} must be a non-blank string')


def validate_route(station: str, draft: RouteDraft) -> None:
    if not isinstance(station, str) or not station.strip():
        raise ValueError('station is required')
    if not isinstance(draft, RouteDraft):
        raise ValueError('expected a route draft')
    if not isinstance(draft.work_order, str) or not draft.work_order.strip():
        raise ValueError('work order is required')
    if not isinstance(draft.transitions, Mapping):
        raise ValueError('expected transitions indexed by private state')
    for name in draft.transitions:
        if not isinstance(name, str) or not name.strip():
            raise ValueError('invalid private state name')

    states = set(draft.transitions)
    validate_ports(draft.ports, states)
    validate_scan_labels(draft.scan_labels, states)
    destinations = {'state': states, 'exit': set(draft.ports.exits)}
    for state, edges in draft.transitions.items():
        if not isinstance(edges, Sequence) or not edges:
            raise ValueError(f'state {state!r} has no transitions')
        total = Fraction(0)
        for edge in edges:
            if not isinstance(edge, Transition):
                raise ValueError(f'invalid transition from {state!r}')
            if edge.target_kind not in ('state', 'exit'):
                raise ValueError('invalid transition target kind')
            if not isinstance(edge.target, str) or edge.target not in destinations[edge.target_kind]:
                raise ValueError(f'unknown transition target from {state!r}')
            if type(edge.hours) is not int or edge.hours < 0:
                raise ValueError('transition hours must be a nonnegative integer')
            if not isinstance(edge.probability, Fraction) or edge.probability < 0:
                raise ValueError('transition probability must be a nonnegative Fraction')
            total += edge.probability
        if total != 1:
            raise ValueError(f'outgoing probabilities for {state!r} must sum to 1')
