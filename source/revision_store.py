import json
import sqlite3
from fractions import Fraction
from types import MappingProxyType

from source.ports import PortDirectory
from source.routes import RouteDraft, RouteRevision, Transition


def _encode_route(draft: RouteDraft) -> str:
    transitions = {
        state: [
            [edge.target_kind, edge.target, edge.hours, edge.probability.numerator,
             edge.probability.denominator]
            for edge in edges
        ]
        for state, edges in draft.transitions.items()
    }
    return json.dumps({
        'entries': dict(draft.ports.entries),
        'exits': draft.ports.exits,
        'work_order': draft.work_order,
        'transitions': transitions,
        'scan_labels': dict(draft.scan_labels),
    }, sort_keys=True)


def _decode_route(payload: str) -> RouteDraft:
    data = json.loads(payload)
    ports = PortDirectory(MappingProxyType(data['entries']), tuple(data['exits']))
    transitions = {
        state: tuple(
            Transition(kind, target, hours, Fraction(numerator, denominator))
            for kind, target, hours, numerator, denominator in edges
        )
        for state, edges in data['transitions'].items()
    }
    scan_labels = MappingProxyType(dict(data.get('scan_labels', {})))
    return RouteDraft(
        ports, MappingProxyType(transitions), data['work_order'], scan_labels
    )


class RevisionStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS route_revisions (
                revision_id INTEGER PRIMARY KEY,
                station TEXT NOT NULL,
                payload TEXT NOT NULL
            )
        ''')

    def insert(self, station: str, draft: RouteDraft) -> int:
        payload = _encode_route(draft)
        if self.connection.in_transaction:
            raise ValueError('revision storage requires an idle connection')
        with self.connection:
            cursor = self.connection.execute(
                'INSERT INTO route_revisions (station, payload) VALUES (?, ?)',
                (station, payload),
            )
            return cursor.lastrowid

    def get(self, station: str, revision_id: int) -> RouteRevision | None:
        row = self.connection.execute(
            'SELECT payload FROM route_revisions WHERE station = ? AND revision_id = ?',
            (station, revision_id),
        ).fetchone()
        if row is None:
            return None
        return RouteRevision(station, revision_id, _decode_route(row[0]))
