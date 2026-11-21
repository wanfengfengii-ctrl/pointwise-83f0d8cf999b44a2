import sqlite3
from dataclasses import dataclass

from exchange.wire import StationPiece, decode_piece


@dataclass(frozen=True)
class ReceivedPiece:
    received_id: int
    issued_id: str
    piece: StationPiece


class PieceStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS received_pieces (
                received_id INTEGER PRIMARY KEY,
                issued_id TEXT NOT NULL UNIQUE,
                payload BLOB NOT NULL
            )
        ''')

    def accept(self, issued_id: str, canonical_payload: bytes) -> ReceivedPiece:
        piece = decode_piece(canonical_payload)
        if self.connection.in_transaction:
            raise ValueError('receipt storage requires an idle connection')
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            self.connection.execute('''
                INSERT INTO received_pieces (issued_id, payload) VALUES (?, ?)
                ON CONFLICT (issued_id) DO NOTHING
            ''', (issued_id, canonical_payload))
            received_id, stored_payload = self.connection.execute('''
                SELECT received_id, payload FROM received_pieces WHERE issued_id = ?
            ''', (issued_id,)).fetchone()
            if stored_payload != canonical_payload:
                raise ValueError('issued identity already belongs to different content')
            return ReceivedPiece(received_id, issued_id, piece)

    def get(self, received_id: int) -> ReceivedPiece | None:
        row = self.connection.execute('''
            SELECT issued_id, payload FROM received_pieces WHERE received_id = ?
        ''', (received_id,)).fetchone()
        if row is None:
            return None
        issued_id, payload = row
        return ReceivedPiece(received_id, issued_id, decode_piece(payload))
