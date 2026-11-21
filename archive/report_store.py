import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction

from archive.receipts import initialize_receipts
from dispatch.port_bindings import ReceivedPort


@dataclass(frozen=True)
class PieceOrigin:
    received_id: int
    issued_id: str
    station: str
    revision_id: int


@dataclass(frozen=True)
class ReportDraft:
    plan_id: int
    start: ReceivedPort
    deadline: int
    probability: Fraction
    sources: tuple[PieceOrigin, ...]


@dataclass(frozen=True)
class StoredReport(ReportDraft):
    report_id: int


class ReportStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        with self.transaction() as connection:
            connection.execute('''
                CREATE TABLE IF NOT EXISTS reports (
                    report_id INTEGER PRIMARY KEY,
                    plan_id INTEGER NOT NULL,
                    start_received_id INTEGER NOT NULL,
                    start_port TEXT NOT NULL,
                    deadline INTEGER NOT NULL CHECK (deadline BETWEEN 0 AND 24),
                    probability TEXT NOT NULL,
                    sources TEXT NOT NULL
                )
            ''')
            initialize_receipts(connection)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.connection.in_transaction:
            raise ValueError('report storage requires an idle connection')
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            yield self.connection

    def insert(self, transaction: sqlite3.Connection, draft: ReportDraft) -> StoredReport:
        if transaction is not self.connection or not transaction.in_transaction:
            raise ValueError('report insertion requires the store transaction')
        sources = json.dumps([
            [source.received_id, source.issued_id, source.station, source.revision_id]
            for source in draft.sources
        ])
        cursor = transaction.execute('''
            INSERT INTO reports (plan_id, start_received_id, start_port, deadline, probability, sources)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (draft.plan_id, draft.start.received_id, draft.start.port,
              draft.deadline, str(draft.probability), sources))
        return StoredReport(
            draft.plan_id, draft.start, draft.deadline, draft.probability, draft.sources,
            cursor.lastrowid,
        )

    def get(self, report_id: int) -> StoredReport | None:
        row = self.connection.execute('''
            SELECT plan_id, start_received_id, start_port, deadline, probability, sources
            FROM reports WHERE report_id = ?
        ''', (report_id,)).fetchone()
        if row is None:
            return None
        plan_id, received_id, start_port, deadline, probability, sources = row
        return StoredReport(
            plan_id, ReceivedPort(received_id, start_port), deadline, Fraction(probability),
            tuple(PieceOrigin(*source) for source in json.loads(sources)), report_id,
        )
