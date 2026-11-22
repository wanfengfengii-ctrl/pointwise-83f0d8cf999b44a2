import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction

from archive.receipts import FirstReceipt
from archive.report_store import PieceOrigin
from dispatch.port_bindings import ReceivedPort


@dataclass(frozen=True)
class ScanReportDraft:
    plan_id: int
    start: ReceivedPort
    deadline: int
    scan_received_id: int
    scan_label: str
    scan_hour: int
    support: Fraction
    joint: Fraction
    conditional: Fraction | None
    sources: tuple[PieceOrigin, ...]


@dataclass(frozen=True)
class StoredScanReport(ScanReportDraft):
    report_id: int

    @property
    def request(self):
        from analysis.requests import ScanReportRequest
        return ScanReportRequest(
            self.plan_id, self.start, self.deadline,
            self.scan_received_id, self.scan_label, self.scan_hour,
        )


def _encode_request(draft: ScanReportDraft) -> str:
    return json.dumps({
        'plan_id': draft.plan_id,
        'start': [draft.start.received_id, draft.start.port],
        'deadline': draft.deadline,
        'scan_received_id': draft.scan_received_id,
        'scan_label': draft.scan_label,
        'scan_hour': draft.scan_hour,
    }, sort_keys=True)


class ScanReportStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        with self.transaction() as connection:
            connection.execute('''
                CREATE TABLE IF NOT EXISTS scan_reports (
                    report_id INTEGER PRIMARY KEY,
                    plan_id INTEGER NOT NULL,
                    start_received_id INTEGER NOT NULL,
                    start_port TEXT NOT NULL,
                    deadline INTEGER NOT NULL CHECK (deadline BETWEEN 0 AND 24),
                    scan_received_id INTEGER NOT NULL,
                    scan_label TEXT NOT NULL,
                    scan_hour INTEGER NOT NULL CHECK (scan_hour BETWEEN 0 AND 24),
                    support TEXT NOT NULL,
                    joint TEXT NOT NULL,
                    conditional TEXT,
                    sources TEXT NOT NULL,
                    request TEXT NOT NULL
                )
            ''')
            connection.execute('''
                CREATE TABLE IF NOT EXISTS scan_first_receipts (
                    receipt_id INTEGER PRIMARY KEY,
                    report_id INTEGER NOT NULL UNIQUE REFERENCES scan_reports(report_id)
                )
            ''')

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.connection.in_transaction:
            raise ValueError('report storage requires an idle connection')
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            yield self.connection

    def insert(self, transaction: sqlite3.Connection, draft: ScanReportDraft) -> StoredScanReport:
        if transaction is not self.connection or not transaction.in_transaction:
            raise ValueError('report insertion requires the store transaction')
        if draft.conditional is not None and not isinstance(draft.conditional, Fraction):
            raise ValueError('conditional result must be a Fraction or None')
        sources = json.dumps([
            [source.received_id, source.issued_id, source.station, source.revision_id]
            for source in draft.sources
        ])
        cursor = transaction.execute('''
            INSERT INTO scan_reports (
                plan_id, start_received_id, start_port, deadline,
                scan_received_id, scan_label, scan_hour, support, joint,
                conditional, sources, request
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            draft.plan_id, draft.start.received_id, draft.start.port, draft.deadline,
            draft.scan_received_id, draft.scan_label, draft.scan_hour,
            str(draft.support), str(draft.joint),
            None if draft.conditional is None else str(draft.conditional),
            sources, _encode_request(draft),
        ))
        return StoredScanReport(
            draft.plan_id, draft.start, draft.deadline, draft.scan_received_id,
            draft.scan_label, draft.scan_hour, draft.support, draft.joint,
            draft.conditional, draft.sources, cursor.lastrowid,
        )

    def get(self, report_id: int) -> StoredScanReport | None:
        row = self.connection.execute('''
            SELECT plan_id, start_received_id, start_port, deadline,
                   scan_received_id, scan_label, scan_hour, support, joint,
                   conditional, sources
            FROM scan_reports WHERE report_id = ?
        ''', (report_id,)).fetchone()
        if row is None:
            return None
        (plan_id, start_received_id, start_port, deadline, scan_received_id,
         scan_label, scan_hour, support, joint, conditional, sources) = row
        return StoredScanReport(
            plan_id, ReceivedPort(start_received_id, start_port), deadline,
            scan_received_id, scan_label, scan_hour,
            Fraction(support), Fraction(joint),
            None if conditional is None else Fraction(conditional),
            tuple(PieceOrigin(*source) for source in json.loads(sources)), report_id,
        )

    def insert_receipt(self, transaction: sqlite3.Connection, report_id: int) -> FirstReceipt:
        if transaction is not self.connection or not transaction.in_transaction:
            raise ValueError('first receipt requires the store transaction')
        cursor = transaction.execute(
            'INSERT INTO scan_first_receipts (report_id) VALUES (?)', (report_id,)
        )
        return FirstReceipt(cursor.lastrowid, report_id)

    def get_receipt(self, report_id: int) -> FirstReceipt | None:
        row = self.connection.execute('''
            SELECT receipt_id FROM scan_first_receipts WHERE report_id = ?
        ''', (report_id,)).fetchone()
        if row is None:
            return None
        return FirstReceipt(row[0], report_id)
