import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class FirstReceipt:
    receipt_id: int
    report_id: int


def initialize_receipts(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS first_receipts (
            receipt_id INTEGER PRIMARY KEY,
            report_id INTEGER NOT NULL UNIQUE REFERENCES reports(report_id)
        )
    ''')


def insert_first_receipt(connection: sqlite3.Connection, report_id: int) -> FirstReceipt:
    if not connection.in_transaction:
        raise ValueError('first receipt requires the report transaction')
    cursor = connection.execute('INSERT INTO first_receipts (report_id) VALUES (?)', (report_id,))
    return FirstReceipt(cursor.lastrowid, report_id)


def read_first_receipt(connection: sqlite3.Connection, report_id: int) -> FirstReceipt | None:
    row = connection.execute(
        'SELECT receipt_id FROM first_receipts WHERE report_id = ?', (report_id,)
    ).fetchone()
    if row is None:
        return None
    return FirstReceipt(row[0], report_id)
