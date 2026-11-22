import json
import sqlite3
import unittest
from contextlib import closing
from fractions import Fraction as F
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis.requests import ScanReportRequest, sign_scan_report
from archive.retrieve import get_scan_report
from archive.scan_report_store import ScanReportStore
from dispatch.plan_store import PlanStore
from dispatch.plans import save_plan
from dispatch.port_bindings import ExitBinding, ReceivedPort, Transfer
from dispatch.terminals import Terminal
from exchange.evidence import ScanEvidence
from exchange.issue import issue_piece
from exchange.piece_store import PieceStore
from exchange.receive import receive_piece
from exchange.wire import decode_piece, encode_piece
from source.ports import PortDirectory
from source.revision_store import RevisionStore
from source.revisions import get_revision, save_revision
from source.routes import RouteDraft, Transition


class ScanRouteTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = RevisionStore(self.db)
        self.store.initialize()
        self.ports = PortDirectory({'in': 'bench'}, ('pass',))
        self.states = {'bench': (Transition('exit', 'pass', 1, F(1)),)}

    def test_scan_labels_freeze_in_revision_and_default_to_empty(self):
        plain = RouteDraft(self.ports, self.states, 'work')
        self.assertEqual(dict(plain.scan_labels), {})
        labeled = RouteDraft(self.ports, self.states, 'work', {'bench': 'R'})
        revision_id = save_revision(self.store, 'A', labeled)
        frozen = get_revision(self.store, 'A', revision_id)
        self.assertEqual(dict(frozen.draft.scan_labels), {'bench': 'R'})

    def test_invalid_scan_labels_are_rejected(self):
        for labels in (
            {'bench': ''}, {'bench': '   '}, {'unknown': 'R'}, {'bench': 7},
            'not-a-mapping',
        ):
            with self.subTest(labels=labels):
                with self.assertRaises(ValueError):
                    save_revision(self.store, 'A', RouteDraft(self.ports, self.states, 'w', labels))


class ScanWireTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = RevisionStore(self.db)
        self.store.initialize()

    def issue(self, labels, states=None):
        states = states or {'bench': (Transition('exit', 'pass', 1, F(1)),)}
        revision_id = save_revision(
            self.store, 'A', RouteDraft(PortDirectory({'in': 'bench'}, ('pass',)), states, 'w', labels)
        )
        return issue_piece(self.store, 'A', revision_id, version=2)

    def test_v2_envelope_evidence_and_identity_round_trip(self):
        piece = self.issue({'bench': 'R'})
        self.assertEqual(piece.version, 2)
        self.assertIsInstance(piece.scan_evidence, ScanEvidence)
        payload = encode_piece(piece)
        body = json.loads(payload)
        self.assertEqual(body['version'], 2)
        self.assertEqual(set(body), {'version', 'station', 'revision', 'entries', 'exits', 'evidence'})
        self.assertEqual(set(body['evidence']), {'rows', 'labels', 'scans', 'joints'})
        decoded = decode_piece(payload)
        self.assertEqual(decoded, piece)
        self.assertEqual(decoded.identity, piece.identity)

    def test_v1_three_argument_issue_stays_version_one(self):
        revision_id = save_revision(
            self.store, 'B',
            RouteDraft(PortDirectory({'in': 'bench'}, ('pass',)),
                       {'bench': (Transition('exit', 'pass', 1, F(1)),)}, 'w'),
        )
        piece = issue_piece(self.store, 'B', revision_id)
        self.assertEqual(piece.version, 1)
        self.assertEqual(json.loads(encode_piece(piece))['version'], 1)

    def test_equivalent_replay_keeps_the_received_id(self):
        piece = self.issue({'bench': 'R'})
        payload = encode_piece(piece)
        body = json.loads(payload)
        equivalent = json.dumps(body, indent=2).encode('utf-8')
        with closing(sqlite3.connect(':memory:')) as center:
            store = PieceStore(center)
            store.initialize()
            first = receive_piece(store, payload, piece.identity)
            second = receive_piece(store, equivalent, piece.identity)
            self.assertEqual(second, first)

    def test_identity_conflict_is_rejected(self):
        piece = self.issue({'bench': 'R'})
        body = json.loads(encode_piece(piece))
        body['station'] = 'Z'
        with closing(sqlite3.connect(':memory:')) as center:
            store = PieceStore(center)
            store.initialize()
            with self.assertRaises(ValueError):
                receive_piece(store, json.dumps(body).encode('utf-8'), piece.identity)


class ScanReportTests(unittest.TestCase):
    def setUp(self):
        self.station_db = sqlite3.connect(':memory:')
        self.addCleanup(self.station_db.close)
        self.revisions = RevisionStore(self.station_db)
        self.revisions.initialize()
        self.center = sqlite3.connect(':memory:')
        self.addCleanup(self.center.close)
        self.pieces = PieceStore(self.center)
        self.plans = PlanStore(self.center)
        self.reports = ScanReportStore(self.center)
        for store in (self.pieces, self.plans, self.reports):
            store.initialize()

    def issue(self, station, entries, exits, labels, states):
        revision_id = save_revision(
            self.revisions, station,
            RouteDraft(PortDirectory(entries, tuple(exits)), states, 'work', labels),
        )
        return issue_piece(self.revisions, station, revision_id, version=2)

    def receive(self, piece):
        return receive_piece(self.pieces, encode_piece(piece), piece.identity).received_id

    def sign(self, plan_id, start, deadline, scan_id, label, hour):
        return sign_scan_report(
            self.reports, self.plans, self.pieces,
            ScanReportRequest(plan_id, start, deadline, scan_id, label, hour),
        )

    def test_held_released_and_unmarked_fixture_expectations(self):
        states = {
            'split': (Transition('state', 'hold', 0, F(1, 2)), Transition('state', 'bench', 0, F(1, 2))),
            'hold': (Transition('state', 'hold', 0, F(1)),),
            'bench': (Transition('exit', 'pass', 1, F(1)),),
        }
        expectations = (
            ({'hold': 'R'}, (F(1, 2), F(0), F(0))),
            ({'bench': 'R'}, (F(1, 2), F(1, 2), F(1))),
            ({}, (F(0), F(0), None)),
        )
        for labels, expected in expectations:
            with self.subTest(labels=labels):
                piece = self.issue('A', {'in': 'split'}, ('pass',), labels, states)
                received_id = self.receive(piece)
                plan = save_plan(self.plans, self.pieces, (received_id,), (
                    ExitBinding(ReceivedPort(received_id, 'pass'), Terminal.RELEASE),
                ))
                report = self.sign(
                    plan.plan_id, ReceivedPort(received_id, 'in'), 1, received_id, 'R', 0
                ).report
                self.assertEqual(
                    (report.support, report.joint, report.conditional), expected
                )

    def test_same_hour_competition_and_sequential_scan(self):
        a_states = {
            'a0': (Transition('state', 'b0', 0, F(1)),),
            'b0': (Transition('exit', 'go', 0, F(1)),),
        }
        b_states = {'r0': (Transition('exit', 'release', 0, F(1)),)}
        a = self.issue('A', {'in': 'a0'}, ('go',), {'a0': 'X'}, a_states)
        b = self.issue('B', {'in': 'r0'}, ('release',), {'r0': 'R'}, b_states)
        ar, br = self.receive(a), self.receive(b)
        plan = save_plan(self.plans, self.pieces, (ar, br), (
            ExitBinding(ReceivedPort(ar, 'go'), Transfer(ReceivedPort(br, 'in'))),
            ExitBinding(ReceivedPort(br, 'release'), Terminal.RELEASE),
        ))
        blocked = self.sign(plan.plan_id, ReceivedPort(ar, 'in'), 0, br, 'R', 0).report
        self.assertEqual((blocked.support, blocked.joint, blocked.conditional), (F(0), F(0), None))

        a2 = self.issue('A2', {'in': 'a0'}, ('go',), {},
                        {'a0': (Transition('exit', 'go', 1, F(1)),)})
        b2 = self.issue('B2', {'in': 'r0'}, ('release',), {'r0': 'R'}, b_states)
        ar2, br2 = self.receive(a2), self.receive(b2)
        plan2 = save_plan(self.plans, self.pieces, (ar2, br2), (
            ExitBinding(ReceivedPort(ar2, 'go'), Transfer(ReceivedPort(br2, 'in'))),
            ExitBinding(ReceivedPort(br2, 'release'), Terminal.RELEASE),
        ))
        clear = self.sign(plan2.plan_id, ReceivedPort(ar2, 'in'), 1, br2, 'R', 1).report
        self.assertEqual((clear.support, clear.joint, clear.conditional), (F(1), F(1), F(1)))

    def test_instant_loop_keeps_closed_class_weights(self):
        states = {
            'A': (
                Transition('state', 'B', 0, F(1, 2)),
                Transition('state', 'C', 0, F(1, 4)),
                Transition('state', 'R', 0, F(1, 4)),
            ),
            'B': (Transition('state', 'A', 0, F(1)),),
            'C': (Transition('state', 'C', 0, F(1)),),
            'R': (Transition('exit', 'release', 1, F(1)),),
        }
        piece = self.issue('A', {'in': 'A'}, ('release',), {'R': 'R'}, states)
        received_id = self.receive(piece)
        plan = save_plan(self.plans, self.pieces, (received_id,), (
            ExitBinding(ReceivedPort(received_id, 'release'), Terminal.RELEASE),
        ))
        at_zero = self.sign(plan.plan_id, ReceivedPort(received_id, 'in'), 0, received_id, 'R', 0).report
        at_one = self.sign(plan.plan_id, ReceivedPort(received_id, 'in'), 1, received_id, 'R', 0).report
        self.assertEqual((at_zero.support, at_zero.joint, at_zero.conditional), (F(1, 2), F(0), F(0)))
        self.assertEqual((at_one.support, at_one.joint, at_one.conditional), (F(1, 2), F(1, 2), F(1)))

    def test_delayed_scan_records_support_without_out_of_window_release(self):
        states = {
            'start': (
                Transition('state', 'wait', 2, F(1, 2)),
                Transition('exit', 'fast', 1, F(1, 2)),
            ),
            'wait': (Transition('state', 'R', 0, F(1)),),
            'R': (Transition('exit', 'slow', 1, F(1)),),
        }
        piece = self.issue('A', {'in': 'start'}, ('fast', 'slow'), {'R': 'R'}, states)
        received_id = self.receive(piece)
        plan = save_plan(self.plans, self.pieces, (received_id,), (
            ExitBinding(ReceivedPort(received_id, 'fast'), Terminal.RELEASE),
            ExitBinding(ReceivedPort(received_id, 'slow'), Terminal.RELEASE),
        ))
        early = self.sign(plan.plan_id, ReceivedPort(received_id, 'in'), 2, received_id, 'R', 2).report
        on_time = self.sign(plan.plan_id, ReceivedPort(received_id, 'in'), 3, received_id, 'R', 2).report
        wrong_hour = self.sign(plan.plan_id, ReceivedPort(received_id, 'in'), 3, received_id, 'R', 1).report
        self.assertEqual((early.support, early.joint, early.conditional), (F(1, 2), F(0), F(0)))
        self.assertEqual((on_time.support, on_time.joint, on_time.conditional), (F(1, 2), F(1, 2), F(1)))
        self.assertEqual((wrong_hour.support, wrong_hour.joint, wrong_hour.conditional), (F(0), F(0), None))

    def test_report_and_first_receipt_share_a_transaction_and_reopen(self):
        piece = self.issue('A', {'in': 'bench'}, ('pass',), {'bench': 'R'},
                           {'bench': (Transition('exit', 'pass', 1, F(1)),)})
        received_id = self.receive(piece)
        plan = save_plan(self.plans, self.pieces, (received_id,), (
            ExitBinding(ReceivedPort(received_id, 'pass'), Terminal.RELEASE),
        ))
        handover = self.sign(plan.plan_id, ReceivedPort(received_id, 'in'), 1, received_id, 'R', 0)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'center.sqlite'
            backup = sqlite3.connect(path)
            self.center.backup(backup)
            backup.close()
            with closing(sqlite3.connect(path)) as reopened:
                store = ScanReportStore(reopened)
                store.initialize()
                self.assertEqual(get_scan_report(store, handover.report.report_id), handover)

    def test_invalid_requests_and_mismatched_pieces(self):
        piece = self.issue('A', {'in': 'bench'}, ('pass',), {'bench': 'R'},
                           {'bench': (Transition('exit', 'pass', 1, F(1)),)})
        received_id = self.receive(piece)
        plan = save_plan(self.plans, self.pieces, (received_id,), (
            ExitBinding(ReceivedPort(received_id, 'pass'), Terminal.RELEASE),
        ))
        start = ReceivedPort(received_id, 'in')
        for kwargs in (
            {'deadline': 25}, {'scan_hour': 2}, {'scan_hour': True},
            {'scan_label': ' '}, {'deadline': 1.5},
        ):
            with self.subTest(kwargs=kwargs):
                request = ScanReportRequest(plan.plan_id, start, 1, received_id, 'R', 0)
                # Rebuild with the invalid field through object replacement.
                request = ScanReportRequest(
                    plan.plan_id, start,
                    kwargs.get('deadline', 1),
                    received_id,
                    kwargs.get('scan_label', 'R'),
                    kwargs.get('scan_hour', 0),
                )
                with self.assertRaises(ValueError):
                    sign_scan_report(self.reports, self.plans, self.pieces, request)
        other = self.issue('A2', {'in': 'bench'}, ('pass',), {'bench': 'R'},
                           {'bench': (Transition('exit', 'pass', 2, F(1)),)})
        other_id = self.receive(other)
        with self.assertRaises(ValueError):
            self.sign(plan.plan_id, start, 1, other_id, 'R', 0)
        with self.assertRaises(LookupError):
            self.sign(999, start, 1, received_id, 'R', 0)
        self.assertIsNone(get_scan_report(self.reports, 777))


if __name__ == '__main__':
    unittest.main()
