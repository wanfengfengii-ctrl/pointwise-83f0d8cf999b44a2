from dataclasses import dataclass

from analysis.horizon import MAX_EVIDENCE_HOUR, validate_deadline
from analysis.route_probabilities import build_arrival_graph, release_probability
from analysis.scan_probabilities import scan_report_probabilities
from archive.publish import ReportDraft, publish_report
from archive.report_store import PieceOrigin, ReportStore
from archive.retrieve import ReportHandover
from archive.scan_publish import ScanReportHandover, publish_scan_report
from archive.scan_report_store import ScanReportDraft, ScanReportStore
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


@dataclass(frozen=True)
class ScanReportRequest:
    plan_id: int
    start: ReceivedPort
    deadline: int
    scan_received_id: int
    scan_label: str
    scan_hour: int


def _validate_scan_request(request: ScanReportRequest) -> None:
    if not isinstance(request, ScanReportRequest):
        raise ValueError('expected a scan report request')
    validate_deadline(request.deadline)
    if type(request.scan_hour) is not int or isinstance(request.scan_hour, bool):
        raise ValueError('scan hour must be an integer')
    if type(request.deadline) is not int or isinstance(request.deadline, bool):
        raise ValueError('deadline must be an integer')
    if not 0 <= request.scan_hour <= request.deadline <= MAX_EVIDENCE_HOUR:
        raise ValueError('require 0 <= scan_hour <= deadline <= 24')
    if not isinstance(request.scan_label, str) or not request.scan_label.strip():
        raise ValueError('scan label must be a non-blank string')
    if type(request.scan_received_id) is not int or isinstance(request.scan_received_id, bool) \
            or not 0 < request.scan_received_id < 2**63:
        raise ValueError('invalid scan received-piece number')
    if not isinstance(request.start, ReceivedPort):
        raise ValueError('starting entry is required')


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


def sign_scan_report(
    report_store: ScanReportStore,
    plan_store: PlanStore,
    piece_store: PieceStore,
    request: ScanReportRequest,
) -> ScanReportHandover:
    _validate_scan_request(request)
    plan = get_plan(plan_store, request.plan_id)
    if request.scan_received_id not in plan.received_ids:
        raise ValueError('scan received piece is not selected by the plan')
    pieces = tuple(get_received_piece(piece_store, received_id) for received_id in plan.received_ids)
    if any(item.piece.version != 2 for item in pieces):
        raise ValueError('scan reports require version 2 pieces')
    support, joint = scan_report_probabilities(
        plan, pieces, request.start, request.deadline,
        request.scan_received_id, request.scan_label, request.scan_hour,
    )
    conditional = None if support == 0 else joint / support
    sources = tuple(
        PieceOrigin(item.received_id, item.issued_id, item.piece.station, item.piece.revision_id)
        for item in pieces
    )
    draft = ScanReportDraft(
        plan.plan_id, request.start, request.deadline, request.scan_received_id,
        request.scan_label, request.scan_hour, support, joint, conditional, sources,
    )
    return publish_scan_report(report_store, draft)
