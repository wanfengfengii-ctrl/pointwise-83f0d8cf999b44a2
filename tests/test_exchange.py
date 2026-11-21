import json
import sqlite3
import unittest
from contextlib import closing
from fractions import Fraction as F
from pathlib import Path
from tempfile import TemporaryDirectory

from exchange.evidence import ExitProbability, FirstExitEvidence
from exchange.issue import issue_piece
from exchange.piece_store import PieceStore
from exchange.receive import get_received_piece, receive_piece
from exchange.wire import StationPiece, decode_piece, encode_piece
from source.ports import PortDirectory
from source.revision_store import RevisionStore
from source.revisions import save_revision
from source.routes import RouteDraft, Transition


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.station_db = sqlite3.connect(':memory:')
        self.addCleanup(self.station_db.close)
        self.station = RevisionStore(self.station_db)
        self.station.initialize()

    def issue(self, edges, exits, extra_states=None):
        draft = RouteDraft(
            PortDirectory({'in': 'private-bench'}, exits),
            {'private-bench': edges, **(extra_states or {})},
            'private-work-order',
        )
        revision = save_revision(self.station, 'A', draft)
        return issue_piece(self.station, 'A', revision)

    def test_retry_and_closed_state_preserve_probability_deficit(self):
        piece = self.issue((
            Transition('state', 'private-bench', 0, F(1, 2)),
            Transition('exit', 'pass', 2, F(1, 4)),
            Transition('state', 'private-closed', 0, F(1, 4)),
        ), ('pass',), {'private-closed': (
            Transition('state', 'private-closed', 0, F(1)),
            Transition('exit', 'pass', 0, F(0)),
        )})
        payload = encode_piece(piece)
        loaded = decode_piece(payload)
        self.assertEqual(loaded.evidence.rows, (ExitProbability('in', 'pass', 2, F(1, 2)),))
        for private in (b'private-bench', b'private-closed', b'private-work-order'):
            self.assertNotIn(private, payload)

    def test_incident_issue(self):
        piece = self.issue((
            Transition('state', 'private-bench', 0, F(1, 4)),
            Transition('exit', 'to-b', 0, F(1, 4)),
            Transition('exit', 'pass', 1, F(1, 2)),
        ), ('to-b', 'pass'))
        self.assertEqual(decode_piece(encode_piece(piece)).evidence.rows, (
            ExitProbability('in', 'pass', 1, F(2, 3)),
            ExitProbability('in', 'to-b', 0, F(1, 3)),
        ))

    def test_same_exit_at_different_hours_remains_distinct(self):
        piece = self.issue((
            Transition('exit', 'X', 1, F(1, 4)),
            Transition('exit', 'X', 3, F(1, 4)),
            Transition('exit', 'Y', 2, F(1, 2)),
        ), ('Y', 'X'))
        self.assertEqual(decode_piece(encode_piece(piece)).evidence.rows, (
            ExitProbability('in', 'X', 1, F(1, 4)),
            ExitProbability('in', 'X', 3, F(1, 4)),
            ExitProbability('in', 'Y', 2, F(1, 2)),
        ))

    def test_window_includes_hour_24_but_excludes_hour_25(self):
        piece = self.issue((
            Transition('exit', 'pass', 24, F(1, 2)),
            Transition('exit', 'pass', 25, F(1, 2)),
        ), ('pass',))
        self.assertEqual(piece.evidence.rows, (ExitProbability('in', 'pass', 24, F(1, 2)),))

    def test_positive_hour_revisit_and_empty_entry_are_preserved(self):
        draft = RouteDraft(PortDirectory({'in': 'retry', 'empty': 'closed'}, ('out',)), {
            'retry': (Transition('state', 'retry', 1, F(1, 2)), Transition('exit', 'out', 0, F(1, 2))),
            'closed': (Transition('state', 'closed', 0, F(1)),),
        }, 'work-5')
        revision = save_revision(self.station, 'A', draft)
        evidence = issue_piece(self.station, 'A', revision).evidence
        self.assertEqual(evidence.entries, ('empty', 'in'))
        self.assertEqual(evidence.rows, tuple(
            ExitProbability('in', 'out', hour, F(1, 2**(hour + 1))) for hour in range(25)
        ))

    def test_issuance_uses_exact_revision_after_reinitialization(self):
        original = self.issue((Transition('exit', 'out', 1, F(1)),), ('out',))
        corrected = self.issue((Transition('exit', 'out', 0, F(1)),), ('out',))
        self.station.initialize()
        reissued = issue_piece(self.station, 'A', original.revision_id)
        self.assertEqual(reissued.identity, original.identity)
        self.assertEqual(encode_piece(reissued), encode_piece(original))
        self.assertEqual(reissued.evidence.rows, (ExitProbability('in', 'out', 1, F(1)),))
        self.assertNotEqual(corrected.revision_id, reissued.revision_id)
        self.assertNotEqual(corrected.identity, reissued.identity)
        with self.assertRaises(LookupError):
            issue_piece(self.station, 'A', 999)

    def test_rejects_mismatched_declared_identity_without_receiving(self):
        a = self.issue((Transition('exit', 'out', 1, F(1)),), ('out',))
        b = StationPiece('B', 1, FirstExitEvidence(('in',), ('out',), ()))
        with closing(sqlite3.connect(':memory:')) as db:
            store = PieceStore(db)
            store.initialize()
            received_b = receive_piece(store, encode_piece(b), b.identity)
            with self.assertRaisesRegex(ValueError, 'declared identity'):
                receive_piece(store, encode_piece(a), b.identity)
            self.assertEqual(db.execute('SELECT count(*) FROM received_pieces').fetchone()[0], 1)
            self.assertEqual(get_received_piece(store, received_b.received_id), received_b)

    def test_equivalent_replay_and_sparse_evidence_after_reopen(self):
        piece = StationPiece('B', 1, FirstExitEvidence(('idle', 'in'), ('out',), (
            ExitProbability('in', 'out', 1, F(1, 2)),
        )))
        payload = encode_piece(piece)
        body = json.loads(payload)
        body['entries'].reverse()
        body['rows'][0][-2:] = [2, 4]
        equivalent = json.dumps(body, indent=2).encode('utf-8')
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'center.sqlite'
            with closing(sqlite3.connect(path)) as db:
                store = PieceStore(db)
                store.initialize()
                original = receive_piece(store, payload, piece.identity)
            with closing(sqlite3.connect(path)) as db:
                store = PieceStore(db)
                store.initialize()
                self.assertEqual(receive_piece(store, equivalent, piece.identity), original)
                self.assertEqual(get_received_piece(store, original.received_id), original)
                self.assertEqual(original.piece.evidence.entries, ('idle', 'in'))
                self.assertEqual(db.execute('SELECT count(*) FROM received_pieces').fetchone()[0], 1)

    def test_rejects_invalid_evidence_before_inserting(self):
        piece = StationPiece('B', 1, FirstExitEvidence(('in',), ('out',), ()))
        with closing(sqlite3.connect(':memory:')) as db:
            store = PieceStore(db)
            store.initialize()
            invalid_cases = (
                ([['in', 'other', 0, 1, 1]], 'outside public directory'),
                ([['in', 'out', 0, 2, 3], ['in', 'out', 1, 2, 3]], 'exceeds 1'),
                ([['in', 'out', 0, 1, 3], ['in', 'out', 0, 1, 3]], 'duplicate evidence'),
                ([['in', 'out', 25, 1, 1]], 'outside public window'),
                ([['in', 'out', 0, 1, 0]], 'rational encoding'),
            )
            for rows, message in invalid_cases:
                with self.subTest(rows=rows):
                    body = json.loads(encode_piece(piece))
                    body['rows'] = rows
                    with self.assertRaisesRegex(ValueError, message):
                        receive_piece(store, json.dumps(body).encode('utf-8'), piece.identity)
            self.assertEqual(db.execute('SELECT count(*) FROM received_pieces').fetchone()[0], 0)
