import sqlite3
import unittest
from contextlib import closing
from fractions import Fraction as F
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis.requests import ReportRequest, sign_report
from archive.report_store import PieceOrigin, ReportStore
from archive.retrieve import get_report
from dispatch.plan_store import PlanStore
from dispatch.plans import save_plan
from dispatch.port_bindings import ExitBinding, ReceivedPort, Transfer
from dispatch.terminals import Terminal
from exchange.evidence import ExitProbability, FirstExitEvidence
from exchange.issue import issue_piece
from exchange.piece_store import PieceStore
from exchange.receive import receive_piece
from exchange.wire import StationPiece, encode_piece
from source.ports import PortDirectory
from source.revision_store import RevisionStore
from source.revisions import save_revision
from source.routes import RouteDraft, Transition


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.db = sqlite3.connect(self.directory / 'center.sqlite')
        self.addCleanup(self.db.close)
        self.pieces = PieceStore(self.db)
        self.plans = PlanStore(self.db)
        self.reports = ReportStore(self.db)
        for store in (self.pieces, self.plans, self.reports):
            store.initialize()

    def receive(self, piece):
        return receive_piece(self.pieces, encode_piece(piece), piece.identity)

    def sign(self, plan, start, deadline):
        return sign_report(self.reports, self.plans, self.pieces, ReportRequest(plan.plan_id, start, deadline))

    def close_source_and_issue(self, drafts):
        pieces = []
        with closing(sqlite3.connect(self.directory / 'stations.sqlite')) as db:
            store = RevisionStore(db)
            store.initialize()
            for station, draft in drafts:
                revision = save_revision(store, station, draft)
                pieces.append(issue_piece(store, station, revision))
        return tuple(pieces)

    def test_positive_chain_with_source_database_closed(self):
        a, b = self.close_source_and_issue((
            ('A', RouteDraft(PortDirectory({'in': 'bench-a'}, ('next',)), {
                'bench-a': (Transition('exit', 'next', 1, F(1)),),
            }, 'a-order')),
            ('B', RouteDraft(PortDirectory({'in': 'bench-b'}, ('pass', 'fail')), {
                'bench-b': (Transition('exit', 'pass', 1, F(1, 2)), Transition('exit', 'fail', 1, F(1, 2))),
            }, 'b-order')),
        ))
        a_received, b_received = self.receive(a), self.receive(b)
        ax, bx = a_received.received_id, b_received.received_id
        start = ReceivedPort(ax, 'in')
        plan = save_plan(self.plans, self.pieces, (ax, bx), (
            ExitBinding(ReceivedPort(ax, 'next'), Transfer(ReceivedPort(bx, 'in'))),
            ExitBinding(ReceivedPort(bx, 'pass'), Terminal.RELEASE),
            ExitBinding(ReceivedPort(bx, 'fail'), Terminal.REJECT),
        ))
        early, exact = (self.sign(plan, start, deadline) for deadline in (1, 2))
        self.assertEqual((early.report.probability, exact.report.probability), (F(0), F(1, 2)))
        self.assertEqual(get_report(self.reports, early.report.report_id), early)
        self.assertEqual(get_report(self.reports, exact.report.report_id), exact)
        self.assertEqual(exact.first_receipt.report_id, exact.report.report_id)

    def test_invalid_report_requests_leave_no_successful_records(self):
        received = self.receive(StationPiece('A', 1, FirstExitEvidence(('in',), (), ())))
        plan = save_plan(self.plans, self.pieces, (received.received_id,), ())
        start = ReceivedPort(received.received_id, 'in')
        with self.assertRaises(LookupError):
            sign_report(self.reports, self.plans, self.pieces, ReportRequest(999, start, 1))
        for wrong_start in (ReceivedPort(999, 'in'), ReceivedPort(received.received_id, 'elsewhere')):
            with self.subTest(start=wrong_start), self.assertRaises(ValueError):
                self.sign(plan, wrong_start, 1)
        for deadline in (-1, 25, F(1, 2), 1.0, True):
            with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                self.sign(plan, start, deadline)
        for table in ('reports', 'first_receipts'):
            self.assertEqual(self.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        zero = self.sign(plan, start, 24)
        self.assertEqual(zero.report.probability, F(0))
        self.assertEqual(get_report(self.reports, zero.report.report_id), zero)

    def round_trip(self, a_piece, b_piece):
        a, b = self.receive(a_piece), self.receive(b_piece)
        ax, bx = a.received_id, b.received_id
        start = ReceivedPort(ax, 'in')
        plan = save_plan(self.plans, self.pieces, (ax, bx), (
            ExitBinding(ReceivedPort(ax, 'to-b'), Transfer(ReceivedPort(bx, 'in'))),
            ExitBinding(ReceivedPort(ax, 'pass'), Terminal.RELEASE),
            ExitBinding(ReceivedPort(bx, 'back'), Transfer(start)),
            ExitBinding(ReceivedPort(bx, 'fail'), Terminal.REJECT),
        ))
        return plan, start, (a, b)

    def test_immediate_round_trip_with_independent_received_pieces(self):
        a = StationPiece('A', 17, FirstExitEvidence(('in',), ('pass', 'to-b'), (
            ExitProbability('in', 'to-b', 0, F(1, 3)), ExitProbability('in', 'pass', 1, F(2, 3)),
        )))
        b = StationPiece('B', 19, FirstExitEvidence(('in',), ('back', 'fail'), (
            ExitProbability('in', 'back', 0, F(1, 2)), ExitProbability('in', 'fail', 0, F(1, 2)),
        )))
        plan, start, _ = self.round_trip(a, b)
        self.assertEqual(self.sign(plan, start, 0).report.probability, F(0))
        self.assertEqual(self.sign(plan, start, 1).report.probability, F(4, 5))

    def split_with_closed_loop(self, include_delayed_release):
        main_rows = [ExitProbability('in', 'pass', 0, F(1, 4))]
        bindings = []
        if include_delayed_release:
            main_rows.append(ExitProbability('in', 'wait', 0, F(1, 2)))
            loop_mass = F(1, 4)
        else:
            loop_mass = F(3, 4)
        main_rows.append(ExitProbability('in', 'loop', 0, loop_mass))
        main_rows.append(ExitProbability('hold', 'loop', 0, F(1)))
        main_exits = ('pass', 'loop', 'wait') if include_delayed_release else ('pass', 'loop')
        main = self.receive(StationPiece('main', 1, FirstExitEvidence(
            ('in', 'hold'), main_exits, tuple(main_rows),
        )))
        loop = self.receive(StationPiece('loop', 1, FirstExitEvidence(('in',), ('return',), (
            ExitProbability('in', 'return', 0, F(1)),
        ))))
        ids = [main.received_id, loop.received_id]
        bindings.extend((
            ExitBinding(ReceivedPort(main.received_id, 'pass'), Terminal.RELEASE),
            ExitBinding(ReceivedPort(main.received_id, 'loop'), Transfer(ReceivedPort(loop.received_id, 'in'))),
            ExitBinding(ReceivedPort(loop.received_id, 'return'), Transfer(ReceivedPort(main.received_id, 'hold'))),
        ))
        if include_delayed_release:
            slow = self.receive(StationPiece('slow', 1, FirstExitEvidence(('in',), ('pass',), (
                ExitProbability('in', 'pass', 1, F(1)),
            ))))
            ids.append(slow.received_id)
            bindings.extend((
                ExitBinding(ReceivedPort(main.received_id, 'wait'), Transfer(ReceivedPort(slow.received_id, 'in'))),
                ExitBinding(ReceivedPort(slow.received_id, 'pass'), Terminal.RELEASE),
            ))
        return save_plan(self.plans, self.pieces, ids, bindings), ReceivedPort(main.received_id, 'in')

    def test_closed_transfer_preserves_original_mass(self):
        plan, start = self.split_with_closed_loop(False)
        signed = self.sign(plan, start, 0)
        self.assertEqual(signed.report.probability, F(1, 4))
        self.assertEqual(get_report(self.reports, signed.report.report_id), signed)

    def test_delayed_release_becomes_eligible_without_admitting_closed_loop(self):
        plan, start = self.split_with_closed_loop(True)
        self.assertEqual(self.sign(plan, start, 0).report.probability, F(1, 4))
        self.assertEqual(self.sign(plan, start, 1).report.probability, F(3, 4))

    def test_positive_hour_transfer_can_reenter_same_coordinate(self):
        received = self.receive(StationPiece('retry', 1, FirstExitEvidence(('in',), ('again', 'pass'), (
            ExitProbability('in', 'again', 1, F(1, 2)), ExitProbability('in', 'pass', 0, F(1, 2)),
        ))))
        start = ReceivedPort(received.received_id, 'in')
        plan = save_plan(self.plans, self.pieces, (received.received_id,), (
            ExitBinding(ReceivedPort(received.received_id, 'again'), Transfer(start)),
            ExitBinding(ReceivedPort(received.received_id, 'pass'), Terminal.RELEASE),
        ))
        self.assertEqual(self.sign(plan, start, 0).report.probability, F(1, 2))
        self.assertEqual(self.sign(plan, start, 2).report.probability, F(7, 8))

    def test_incident(self):
        a, b = self.close_source_and_issue((
            ('A', RouteDraft(PortDirectory({'in': 'private-a'}, ('pass', 'to-b')), {
                'private-a': (
                    Transition('state', 'private-a', 0, F(1, 4)), Transition('exit', 'to-b', 0, F(1, 4)),
                    Transition('exit', 'pass', 1, F(1, 2)),
                ),
            }, 'a-source')),
            ('B', RouteDraft(PortDirectory({'in': 'private-b'}, ('back', 'fail')), {
                'private-b': (Transition('exit', 'back', 0, F(1, 2)), Transition('exit', 'fail', 0, F(1, 2))),
            }, 'b-source')),
        ))
        plan, start, received = self.round_trip(a, b)
        zero, one = (self.sign(plan, start, deadline) for deadline in (0, 1))
        self.assertEqual((zero.report.probability, one.report.probability), (F(0), F(4, 5)))
        self.assertEqual(one.report.plan_id, plan.plan_id)
        self.assertEqual(one.report.start, start)
        self.assertEqual(one.report.sources, (
            PieceOrigin(received[0].received_id, a.identity, 'A', a.revision_id),
            PieceOrigin(received[1].received_id, b.identity, 'B', b.revision_id),
        ))
        self.assertEqual(one.first_receipt.report_id, one.report.report_id)
        self.assertEqual(get_report(self.reports, one.report.report_id), one)
