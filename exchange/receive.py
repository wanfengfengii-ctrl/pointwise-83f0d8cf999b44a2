import hashlib

from exchange.piece_store import PieceStore, ReceivedPiece
from exchange.wire import decode_piece, encode_piece


def receive_piece(store: PieceStore, payload: bytes, declared_identity: str) -> ReceivedPiece:
    canonical = encode_piece(decode_piece(payload))
    actual_identity = hashlib.sha256(canonical).hexdigest()
    if not isinstance(declared_identity, str) or actual_identity != declared_identity:
        raise ValueError('declared identity does not match the station piece')
    return store.accept(actual_identity, canonical)


def get_received_piece(store: PieceStore, received_id: int) -> ReceivedPiece:
    if type(received_id) is not int or not 0 < received_id < 2**63:
        raise ValueError('invalid receipt number')
    received = store.get(received_id)
    if received is None:
        raise LookupError(f'unknown received piece {received_id}')
    return received
