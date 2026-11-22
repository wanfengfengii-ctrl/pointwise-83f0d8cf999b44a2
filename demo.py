import json
import sqlite3
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis.requests import (
    ReportRequest,
    ScanReportRequest,
    sign_report,
    sign_scan_report,
)
from archive.report_store import ReportStore
from archive.retrieve import get_report, get_scan_report
from archive.scan_report_store import ScanReportStore
from dispatch.plan_store import PlanStore
from dispatch.plans import save_plan
from dispatch.port_bindings import ExitBinding, ReceivedPort, Transfer
from dispatch.terminals import Terminal
from exchange.issue import issue_piece
from exchange.piece_store import PieceStore
from exchange.receive import receive_piece
from exchange.wire import encode_piece
from source.ports import PortDirectory
from source.revision_store import RevisionStore
from source.revisions import save_revision
from source.routes import RouteDraft, Transition


def _draft(data: dict) -> RouteDraft:
    moves = {
        state: tuple(
            Transition(kind, target, hours, Fraction(numerator, denominator))
            for kind, target, hours, numerator, denominator in rows
        )
        for state, rows in data['transitions'].items()
    }
    return RouteDraft(PortDirectory(data['entries'], tuple(data['exits'])), moves, data['work_order'])


def _issue(store: RevisionStore, station: str, draft: RouteDraft):
    revision = save_revision(store, station, draft)
    piece = issue_piece(store, station, revision)
    return encode_piece(piece), piece.identity


def _scan_draft() -> RouteDraft:
    # Half the coupons reach the marked hold state at hour 0 and stay there;
    # the other half reach the bench and leave through "pass" at hour 1.
    return RouteDraft(
        PortDirectory({'in': 'split'}, ('pass',)),
        {
            'split': (
                Transition('state', 'hold', 0, Fraction(1, 2)),
                Transition('state', 'bench', 0, Fraction(1, 2)),
            ),
            'hold': (Transition('state', 'hold', 0, Fraction(1)),),
            'bench': (Transition('exit', 'pass', 1, Fraction(1)),),
        },
        '北桥-首扫-01',
        {'hold': 'R'},
    )


def run_scan_demo(directory: Path) -> list[dict]:
    station_path = directory / 'scan_stations.sqlite'
    center_path = directory / 'scan_center.sqlite'
    with closing(sqlite3.connect(station_path)) as station_db:
        revisions = RevisionStore(station_db)
        revisions.initialize()
        revision_id = save_revision(revisions, 'A', _scan_draft())
        piece = issue_piece(revisions, 'A', revision_id, version=2)
        payload, identity = encode_piece(piece), piece.identity

    with closing(sqlite3.connect(center_path)) as center_db:
        pieces = PieceStore(center_db)
        plans = PlanStore(center_db)
        scan_reports = ScanReportStore(center_db)
        for store in (pieces, plans, scan_reports):
            store.initialize()
        received = receive_piece(pieces, payload, identity)
        received_id = received.received_id
        start = ReceivedPort(received_id, 'in')
        plan = save_plan(plans, pieces, (received_id,), (
            ExitBinding(ReceivedPort(received_id, 'pass'), Terminal.RELEASE),
        ))
        request = ScanReportRequest(
            plan.plan_id, start, 1, received_id, 'R', 0,
        )
        signed = sign_scan_report(scan_reports, plans, pieces, request)
        report = signed.report
        assert (report.support, report.joint, report.conditional) == (
            Fraction(1, 2), Fraction(0), Fraction(0),
        )
        assert report.sources[0].received_id == received_id
        assert report.sources[0].issued_id == identity

    # The night shift closes the station; the day shift reopens the center and
    # retrieves the finalized report and its first receipt by the original id.
    with closing(sqlite3.connect(center_path)) as reopened:
        scan_reports = ScanReportStore(reopened)
        scan_reports.initialize()
        handed_over = get_scan_report(scan_reports, report.report_id)
        assert handed_over == signed
        assert handed_over.first_receipt == signed.first_receipt

    return [
        {
            'case_id': 'scan-v2',
            'input': {
                'plan_id': plan.plan_id,
                'start': [received_id, 'in'],
                'deadline': request.deadline,
                'scan_received_id': request.scan_received_id,
                'scan_label': request.scan_label,
                'scan_hour': request.scan_hour,
            },
            'expected': ['1/2', '0', '0'],
            'actual': [str(report.support), str(report.joint), str(report.conditional)],
            'downstream_expected': {
                'report_id': report.report_id,
                'receipt_linked': True,
                'received_ids': [received_id],
            },
            'downstream_actual': {
                'report_id': handed_over.report.report_id,
                'receipt_linked': handed_over.first_receipt.report_id
                == handed_over.report.report_id,
                'received_ids': [item.received_id for item in handed_over.report.sources],
            },
            'code_paths': [
                'exchange/scan_evidence.py', 'exchange/wire.py', 'exchange/receive.py',
                'analysis/scan_probabilities.py', 'analysis/requests.py',
                'archive/scan_report_store.py', 'archive/retrieve.py',
            ],
            'oracle': '半数试片第0小时首扫R后永久滞留，支持1/2、准时放行0，条件率0；重开中心按原号取回定稿及首执。',
        },
    ]


def run_demo(directory: Path) -> list[dict]:
    sample = Path(__file__).with_name('samples') / 'workday.json'
    data = json.loads(sample.read_text(encoding='utf-8'))
    station_path = directory / 'stations.sqlite'
    center_path = directory / 'center.sqlite'
    with closing(sqlite3.connect(station_path)) as station_db:
        station_store = RevisionStore(station_db)
        station_store.initialize()
        a_payload = _issue(station_store, 'A', _draft(data['A']))
        b_payload = _issue(station_store, 'B', _draft(data['B']))

    with closing(sqlite3.connect(center_path)) as center_db:
        pieces = PieceStore(center_db)
        plans = PlanStore(center_db)
        reports = ReportStore(center_db)
        for store in (pieces, plans, reports):
            store.initialize()
        a = receive_piece(pieces, *a_payload)
        b = receive_piece(pieces, *b_payload)
        start = ReceivedPort(a.received_id, 'in')
        plan = save_plan(plans, pieces, (a.received_id, b.received_id), (
            ExitBinding(ReceivedPort(a.received_id, 'to-b'), Transfer(ReceivedPort(b.received_id, 'in'))),
            ExitBinding(ReceivedPort(b.received_id, 'pass'), Terminal.RELEASE),
            ExitBinding(ReceivedPort(b.received_id, 'fail'), Terminal.REJECT),
        ))
        early = sign_report(reports, plans, pieces, ReportRequest(plan.plan_id, start, 1))
        signed = sign_report(reports, plans, pieces, ReportRequest(plan.plan_id, start, 2))
        used = get_report(reports, signed.report.report_id)
        assert used == signed
        shares = [early.report.probability, used.report.probability]
        expected_count = data['batch_size'] * used.report.probability
        receipt_linked = used.first_receipt.report_id == used.report.report_id
        assert shares == [Fraction(0), Fraction(1, 2)]
        assert expected_count == 6 and receipt_linked

        with closing(sqlite3.connect(station_path)) as station_db:
            station_store = RevisionStore(station_db)
            station_store.initialize()
            correction = _issue(station_store, 'B', _draft(data['B_correction']))
        corrected_b = receive_piece(pieces, *correction)
        assert corrected_b.received_id != b.received_id

    with closing(sqlite3.connect(center_path)) as reopened:
        for store in (PieceStore(reopened), PlanStore(reopened), ReportStore(reopened)):
            store.initialize()
        handed_over = get_report(ReportStore(reopened), signed.report.report_id)
        assert handed_over == signed
        old_received_ids = [item.received_id for item in handed_over.report.sources]
        original_receipt = handed_over.first_receipt == signed.first_receipt
        assert old_received_ids == [a.received_id, b.received_id] and original_receipt

    return [
        {
            'case_id': 'v5',
            'input': {'batch_size': data['batch_size'], 'A': data['A']['transitions'],
                      'B': data['B']['transitions'], 'deadlines': [1, 2]},
            'expected': ['0', '1/2'],
            'actual': [str(share) for share in shares],
            'downstream_expected': {'expected_releases': '6', 'receipt_linked': True},
            'downstream_actual': {'expected_releases': str(expected_count), 'receipt_linked': receipt_linked},
            'code_paths': ['exchange/issue.py', 'dispatch/plans.py', 'analysis/route_probabilities.py',
                           'analysis/requests.py', 'archive/publish.py', 'archive/retrieve.py'],
            'oracle': '两段分别耗一小时。两小时内放行概率为 1 × 1/2，12 片的期望为 6 片。',
        },
        {
            'case_id': 'v8',
            'input': {'original_report': signed.report.report_id, 'corrected_receipt': corrected_b.received_id},
            'expected': '1/2',
            'actual': str(handed_over.report.probability),
            'downstream_expected': {'received_ids': [a.received_id, b.received_id], 'original_receipt': True},
            'downstream_actual': {'received_ids': old_received_ids, 'original_receipt': original_receipt},
            'code_paths': ['source/revisions.py', 'exchange/receive.py', 'archive/report_store.py',
                           'archive/retrieve.py', 'archive/receipts.py'],
            'oracle': '重开后按原号查询，逐项对照勘误前保存的定稿及首执。',
        },
    ]


def main() -> None:
    with TemporaryDirectory(prefix='northbridge-workday-') as directory:
        base = Path(directory)
        for case in run_demo(base) + run_scan_demo(base):
            print('POINTWISE_SCENARIO ' + json.dumps(case, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
