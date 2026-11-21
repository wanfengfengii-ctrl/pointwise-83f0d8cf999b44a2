import json
import sqlite3
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis.requests import ReportRequest, sign_report
from archive.report_store import ReportStore
from archive.retrieve import get_report
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
        for case in run_demo(Path(directory)):
            print('POINTWISE_SCENARIO ' + json.dumps(case, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
