import sqlite3
import unittest
from contextlib import closing
from fractions import Fraction as F
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis.requests import ReportRequest, sign_report
from archive.receipts import read_first_receipt
from archive.report_store import ReportStore
from archive.retrieve import get_report
from dispatch.plan_store import PlanStore
from dispatch.plans import save_plan
from dispatch.port_bindings import ExitBinding, ReceivedPort
from dispatch.terminals import Terminal
from exchange.issue import issue_piece
from exchange.piece_store import PieceStore
from exchange.receive import receive_piece
from exchange.wire import encode_piece
from source.ports import PortDirectory
from source.revision_store import RevisionStore
from source.revisions import save_revision
from source.routes import RouteDraft, Transition


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.station_path = Path(directory.name) / 'station.sqlite'
        self.center_path = Path(directory.name) / 'center.sqlite'
        self.db = sqlite3.connect(self.center_path)
        self.addCleanup(lambda: self.db.close())
        self.initialize_center()
        self.original_piece = self.issue(F(1, 2))
        self.received, self.plan = self.receive_and_plan(self.original_piece)

    def initialize_center(self):
        self.pieces = PieceStore(self.db)
        self.plans = PlanStore(self.db)
        self.reports = ReportStore(self.db)
        for store in (self.pieces, self.plans, self.reports):
            store.initialize()

    def reopen(self):
        self.db.close()
        self.db = sqlite3.connect(self.center_path)
        self.initialize_center()

    def issue(self, release_share):
        draft = RouteDraft(PortDirectory({'in': 'bench'}, ('pass', 'fail')), {'bench': (
            Transition('exit', 'pass', 2, release_share), Transition('exit', 'fail', 2, 1 - release_share),
        )}, f'order-{release_share}')
        with closing(sqlite3.connect(self.station_path)) as db:
            store = RevisionStore(db)
            store.initialize()
            return issue_piece(store, 'A', save_revision(store, 'A', draft))

    def receive_and_plan(self, piece):
        receipt = receive_piece(self.pieces, encode_piece(piece), piece.identity)
        plan = save_plan(self.plans, self.pieces, (receipt.received_id,), (
            ExitBinding(ReceivedPort(receipt.received_id, 'pass'), Terminal.RELEASE),
            ExitBinding(ReceivedPort(receipt.received_id, 'fail'), Terminal.REJECT),
        ))
        return receipt, plan

    def sign(self, deadline=2):
        return sign_report(self.reports, self.plans, self.pieces, ReportRequest(
            self.plan.plan_id, ReceivedPort(self.received.received_id, 'in'), deadline,
        ))

    def assert_no_signed_rows(self):
        self.assertEqual(self.db.execute('SELECT count(*) FROM reports').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 0)
        self.assertIsNone(self.reports.get(1))
        self.assertIsNone(read_first_receipt(self.db, 1))
        self.assertIsNone(get_report(self.reports, 1))

    def test_receipt_insert_failure_rolls_back_report_and_retry_can_sign(self):
        self.db.execute('''
            CREATE TRIGGER reject_first_receipt BEFORE INSERT ON first_receipts
            WHEN EXISTS (SELECT 1 FROM reports WHERE report_id = NEW.report_id)
            BEGIN SELECT RAISE(ABORT, 'receipt write refused'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt write refused'):
            self.sign()
        self.reopen()
        self.assert_no_signed_rows()
        self.db.execute('DROP TRIGGER reject_first_receipt')
        signed = self.sign()
        self.assertEqual(signed.report.probability, F(1, 2))
        self.assertEqual(signed.first_receipt.report_id, signed.report.report_id)
        self.assertEqual(get_report(self.reports, signed.report.report_id), signed)
        self.assertEqual(self.db.execute('SELECT count(*) FROM reports').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 1)

    def test_deferred_constraint_failure_on_commit_leaves_no_reports_or_receipts(self):
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.execute('CREATE TABLE shift_register (id INTEGER PRIMARY KEY)')
        self.db.execute('''
            CREATE TABLE receipt_register (
                shift_id INTEGER REFERENCES shift_register(id) DEFERRABLE INITIALLY DEFERRED
            )
        ''')
        self.db.execute('''
            CREATE TRIGGER record_receipt_shift AFTER INSERT ON first_receipts
            BEGIN INSERT INTO receipt_register VALUES (701); END
        ''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.sign()
        self.assertFalse(self.db.in_transaction)
        self.reopen()
        self.assert_no_signed_rows()
        self.assertEqual(self.db.execute('SELECT count(*) FROM receipt_register').fetchone()[0], 0)

    def test_original_report_after_correction_and_reopening(self):
        original = self.sign()
        original_received_id = self.received.received_id
        original_plan_id = self.plan.plan_id
        corrected_piece = self.issue(F(1))
        self.received, self.plan = self.receive_and_plan(corrected_piece)
        corrected = self.sign()
        self.reopen()
        stored_old = get_report(self.reports, original.report.report_id)
        stored_new = get_report(self.reports, corrected.report.report_id)
        self.assertEqual(stored_old, original)
        self.assertEqual(stored_new, corrected)
        self.assertEqual((stored_old.report.probability, stored_new.report.probability), (F(1, 2), F(1)))
        self.assertEqual(stored_old.report.plan_id, original_plan_id)
        self.assertEqual(stored_old.report.sources[0].received_id, original_received_id)
        self.assertEqual(stored_old.report.sources[0].issued_id, self.original_piece.identity)
        self.assertEqual(stored_old.report.sources[0].revision_id, self.original_piece.revision_id)
        self.assertEqual(stored_new.report.sources[0].received_id, self.received.received_id)
        self.assertNotEqual(stored_old.first_receipt, stored_new.first_receipt)

    def test_two_reports_of_same_plan_keep_separate_first_receipts(self):
        first, second = self.sign(1), self.sign(2)
        self.reopen()
        self.assertEqual(first.report.plan_id, second.report.plan_id)
        for original, probability in ((first, F(0)), (second, F(1, 2))):
            with self.subTest(report=original.report.report_id):
                handed_over = get_report(self.reports, original.report.report_id)
                self.assertEqual(handed_over.report.probability, probability)
                self.assertEqual(handed_over.first_receipt, original.first_receipt)
                self.assertEqual(handed_over.first_receipt.report_id, original.report.report_id)
        self.assertNotEqual(first.first_receipt.receipt_id, second.first_receipt.receipt_id)

    def test_unknown_report_returns_not_found(self):
        signed = self.sign()
        self.assertIsNone(get_report(self.reports, signed.report.report_id + 1))
