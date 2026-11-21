import sqlite3
import unittest
from fractions import Fraction as F

from dispatch.plan_store import PlanStore
from dispatch.plans import get_plan, save_plan
from dispatch.port_bindings import ExitBinding, ReceivedPort, Transfer
from dispatch.terminals import Terminal
from exchange.evidence import ExitProbability, FirstExitEvidence
from exchange.piece_store import PieceStore
from exchange.receive import receive_piece
from exchange.wire import StationPiece, encode_piece


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.pieces = PieceStore(self.db)
        self.plans = PlanStore(self.db)
        self.pieces.initialize()
        self.plans.initialize()
        a = StationPiece('A', 1, FirstExitEvidence(('in',), ('out',), (
            ExitProbability('in', 'out', 1, F(1)),
        )))
        b = StationPiece('B', 1, FirstExitEvidence(('in',), ('pass', 'fail'), ()))
        self.a = receive_piece(self.pieces, encode_piece(a), a.identity).received_id
        self.b = receive_piece(self.pieces, encode_piece(b), b.identity).received_id
        self.outlet = ReceivedPort(self.a, 'out')

    def test_unreceived_or_unselected_same_name_entry_is_rejected(self):
        for other_id in (999, self.b):
            with self.subTest(other_id=other_id), self.assertRaisesRegex(ValueError, 'transfer target'):
                save_plan(self.plans, self.pieces, (self.a,), (
                    ExitBinding(self.outlet, Transfer(ReceivedPort(other_id, 'in'))),
                ))
        self.assertEqual(self.db.execute('SELECT count(*) FROM dispatch_plans').fetchone()[0], 0)

    def test_complete_plan_preserves_exact_ports_and_terminals(self):
        bindings = (
            ExitBinding(self.outlet, Transfer(ReceivedPort(self.b, 'in'))),
            ExitBinding(ReceivedPort(self.b, 'pass'), Terminal.RELEASE),
            ExitBinding(ReceivedPort(self.b, 'fail'), Terminal.REJECT),
        )
        stored = save_plan(self.plans, self.pieces, (self.b, self.a), bindings)
        self.plans.initialize()
        loaded = get_plan(self.plans, stored.plan_id)
        self.assertEqual(loaded, stored)
        self.assertEqual(loaded.received_ids, (self.a, self.b))
        self.assertCountEqual(loaded.bindings, bindings)

    def test_missing_or_conflicting_binding_is_not_registered(self):
        for bindings, message in (
            ((), 'missing outlet'),
            ((ExitBinding(self.outlet, Terminal.RELEASE),
              ExitBinding(self.outlet, Terminal.REJECT)), 'duplicate binding'),
            ((ExitBinding(self.outlet, 'release'),), 'destination'),
        ):
            with self.subTest(bindings=bindings), self.assertRaisesRegex(ValueError, message):
                save_plan(self.plans, self.pieces, (self.a,), bindings)
        self.assertEqual(self.db.execute('SELECT count(*) FROM dispatch_plans').fetchone()[0], 0)
