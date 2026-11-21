from dataclasses import dataclass

from analysis.horizon import validate_deadline
from analysis.route_probabilities import build_arrival_graph, release_probability
from archive.publish import ReportDraft, publish_report
from archive.report_store import PieceOrigin, ReportStore
from archive.retrieve import ReportHandover
from dispatch.plan_store import PlanStore
from dispatch.plans import get_plan
from dispatch.port_bindings import ReceivedPort
from exchange.piece_store import PieceStore
from exchange.receive import get_received_piece


@dataclass(frozen=True)
class ReportRequest:
    plan_id: int
    start: ReceivedPort
    deadline: int


def sign_report(
    report_store: ReportStore,
    plan_store: PlanStore,
    piece_store: PieceStore,
    request: ReportRequest,
) -> ReportHandover:
    if not isinstance(request, ReportRequest):
        raise ValueError('expected a report request')
    validate_deadline(request.deadline)
    plan = get_plan(plan_store, request.plan_id)
    pieces = tuple(get_received_piece(piece_store, received_id) for received_id in plan.received_ids)
    graph = build_arrival_graph(plan, pieces)
    probability = release_probability(graph, request.start, request.deadline)
    sources = tuple(
        PieceOrigin(item.received_id, item.issued_id, item.piece.station, item.piece.revision_id)
        for item in pieces
    )
    draft = ReportDraft(plan.plan_id, request.start, request.deadline, probability, sources)
    return publish_report(report_store, draft)
