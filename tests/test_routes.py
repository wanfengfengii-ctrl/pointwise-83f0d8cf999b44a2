import sqlite3
import unittest
from contextlib import closing
from fractions import Fraction as F
from pathlib import Path
from tempfile import TemporaryDirectory

from source.ports import PortDirectory
from source.revision_store import RevisionStore
from source.revisions import get_revision, save_revision
from source.routes import RouteDraft, Transition


class RoutesTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = RevisionStore(self.db)
        self.store.initialize()
        self.ports = PortDirectory({'in': 'bench'}, ('pass', 'fail'))

    def test_rejects_outgoing_total_over_one(self):
        draft = RouteDraft(self.ports, {'bench': (
            Transition('exit', 'pass', 1, F(2, 3)),
            Transition('exit', 'fail', 1, F(2, 3)),
        )}, 'work-1')
        with self.assertRaisesRegex(ValueError, 'sum to 1'):
            save_revision(self.store, 'N', draft)
        self.assertEqual(self.db.execute('SELECT count(*) FROM route_revisions').fetchone()[0], 0)
        with self.assertRaises(LookupError):
            get_revision(self.store, 'N', 1)

    def test_valid_revision_preserves_ports_weights_hours_and_work_order(self):
        edges = [Transition('exit', 'pass', 1, F(1, 2)), Transition('exit', 'fail', 3, F(1, 2))]
        draft = RouteDraft(self.ports, {'bench': edges}, 'order-12')
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'station.sqlite'
            with closing(sqlite3.connect(path)) as db:
                store = RevisionStore(db)
                store.initialize()
                revision_id = save_revision(store, 'N', draft)
            edges.clear()
            with closing(sqlite3.connect(path)) as db:
                store = RevisionStore(db)
                store.initialize()
                frozen = get_revision(store, 'N', revision_id)
                with self.assertRaises(LookupError):
                    get_revision(store, 'S', revision_id)
        self.assertEqual(frozen.draft.ports, self.ports)
        self.assertEqual(frozen.draft.transitions['bench'], (
            Transition('exit', 'pass', 1, F(1, 2)), Transition('exit', 'fail', 3, F(1, 2)),
        ))
        self.assertEqual(frozen.draft.work_order, 'order-12')
        with self.assertRaises(TypeError):
            frozen.draft.ports.entries['in'] = 'other'

    def test_bad_ports_references_weights_and_hours_leave_no_revision(self):
        routes = (
            RouteDraft(PortDirectory({'in': 'absent'}, ('pass',)), {'bench': (
                Transition('exit', 'pass', 1, F(1)),
            )}, 'order'),
            RouteDraft(PortDirectory({'in': 'bench'}, ('pass', 'pass')), {'bench': (
                Transition('exit', 'pass', 1, F(1)),
            )}, 'order'),
            RouteDraft(self.ports, {'bench': (Transition('state', 'absent', 0, F(1)),)}, 'order'),
            RouteDraft(self.ports, {'bench': (Transition('exit', 'pass', True, F(1)),)}, 'order'),
            RouteDraft(self.ports, {'bench': (Transition('exit', 'pass', -1, F(1)),)}, 'order'),
            RouteDraft(self.ports, {'bench': (Transition('exit', 'pass', 1, 1),)}, 'order'),
            RouteDraft(self.ports, {'bench': (Transition('exit', 'pass', 1, F(-1)),)}, 'order'),
            RouteDraft(self.ports, {'bench': (Transition('exit', 'pass', 1, F(1, 2)),)}, 'order'),
        )
        for draft in routes:
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                save_revision(self.store, 'N', draft)
        self.assertEqual(self.db.execute('SELECT count(*) FROM route_revisions').fetchone()[0], 0)
