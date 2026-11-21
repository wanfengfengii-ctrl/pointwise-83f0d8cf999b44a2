from collections.abc import Sequence

from dispatch.plan_checks import validate_plan
from dispatch.plan_store import PlanStore, StoredPlan
from dispatch.port_bindings import ExitBinding, build_port_catalog
from exchange.piece_store import PieceStore
from exchange.receive import get_received_piece


def save_plan(
    store: PlanStore,
    piece_store: PieceStore,
    received_ids: Sequence[int],
    bindings: Sequence[ExitBinding],
) -> StoredPlan:
    if not isinstance(received_ids, Sequence) or not received_ids:
        raise ValueError('select at least one received piece')
    if not isinstance(bindings, Sequence) or isinstance(bindings, (str, bytes)):
        raise ValueError('expected a sequence of outlet bindings')
    ids = tuple(received_ids)
    received = tuple(get_received_piece(piece_store, received_id) for received_id in ids)
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate piece selection')
    checked_bindings = tuple(bindings)
    validate_plan(build_port_catalog(received), checked_bindings)
    return store.insert(
        tuple(sorted(ids)), tuple(sorted(checked_bindings, key=lambda binding: binding.source))
    )


def get_plan(store: PlanStore, plan_id: int) -> StoredPlan:
    if type(plan_id) is not int or not 0 < plan_id < 2**63:
        raise ValueError('invalid plan number')
    plan = store.get(plan_id)
    if plan is None:
        raise LookupError(f'unknown dispatch plan {plan_id}')
    return plan
