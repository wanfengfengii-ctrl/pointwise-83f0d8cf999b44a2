from collections.abc import Iterable
from dataclasses import dataclass

from dispatch.terminals import Terminal
from exchange.piece_store import ReceivedPiece


@dataclass(frozen=True, order=True)
class ReceivedPort:
    received_id: int
    port: str

    def __post_init__(self) -> None:
        if type(self.received_id) is not int or not 0 < self.received_id < 2**63:
            raise ValueError('invalid received-piece coordinate')
        if not isinstance(self.port, str) or not self.port.strip():
            raise ValueError('port name is required')


@dataclass(frozen=True)
class Transfer:
    target: ReceivedPort


@dataclass(frozen=True)
class ExitBinding:
    source: ReceivedPort
    destination: Transfer | Terminal


@dataclass(frozen=True)
class PortCatalog:
    entries: frozenset[ReceivedPort]
    exits: frozenset[ReceivedPort]


def build_port_catalog(pieces: Iterable[ReceivedPiece]) -> PortCatalog:
    entries = set()
    exits = set()
    for received in pieces:
        evidence = received.piece.evidence
        entries.update(ReceivedPort(received.received_id, name) for name in evidence.entries)
        exits.update(ReceivedPort(received.received_id, name) for name in evidence.exits)
    return PortCatalog(frozenset(entries), frozenset(exits))
