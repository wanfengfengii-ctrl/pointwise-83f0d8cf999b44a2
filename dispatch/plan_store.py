import json
import sqlite3
from dataclasses import dataclass

from dispatch.port_bindings import ExitBinding, ReceivedPort, Transfer
from dispatch.terminals import parse_terminal


@dataclass(frozen=True)
class StoredPlan:
    plan_id: int
    received_ids: tuple[int, ...]
    bindings: tuple[ExitBinding, ...]


def _encode_binding(binding: ExitBinding) -> list:
    source = [binding.source.received_id, binding.source.port]
    destination = binding.destination
    if isinstance(destination, Transfer):
        return [source, ['entry', destination.target.received_id, destination.target.port]]
    return [source, ['terminal', destination.value]]


def _decode_binding(row: list) -> ExitBinding:
    source, destination = row
    kind, *values = destination
    if kind == 'entry':
        target = Transfer(ReceivedPort(*values))
    elif kind == 'terminal':
        target = parse_terminal(*values)
    else:
        raise ValueError(f'unknown saved binding type {kind!r}')
    return ExitBinding(ReceivedPort(*source), target)


class PlanStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS dispatch_plans (
                plan_id INTEGER PRIMARY KEY,
                payload TEXT NOT NULL
            )
        ''')

    def insert(
        self, received_ids: tuple[int, ...], bindings: tuple[ExitBinding, ...]
    ) -> StoredPlan:
        payload = json.dumps({
            'received_ids': received_ids,
            'bindings': [_encode_binding(binding) for binding in bindings],
        })
        if self.connection.in_transaction:
            raise ValueError('plan storage requires an idle connection')
        with self.connection:
            cursor = self.connection.execute(
                'INSERT INTO dispatch_plans (payload) VALUES (?)', (payload,)
            )
            return StoredPlan(cursor.lastrowid, received_ids, bindings)

    def get(self, plan_id: int) -> StoredPlan | None:
        row = self.connection.execute(
            'SELECT payload FROM dispatch_plans WHERE plan_id = ?', (plan_id,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        return StoredPlan(
            plan_id,
            tuple(data['received_ids']),
            tuple(_decode_binding(binding) for binding in data['bindings']),
        )
