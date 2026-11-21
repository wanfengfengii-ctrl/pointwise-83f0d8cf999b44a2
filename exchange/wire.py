import hashlib
import json
from dataclasses import dataclass
from fractions import Fraction

from exchange.evidence import ExitProbability, FirstExitEvidence, validate_exit_evidence


@dataclass(frozen=True)
class StationPiece:
    station: str
    revision_id: int
    evidence: FirstExitEvidence

    @property
    def identity(self) -> str:
        return piece_identity(self)


def _validate_origin(station: str, revision_id: int) -> None:
    if not isinstance(station, str) or not station.strip():
        raise ValueError('station is required')
    if type(revision_id) is not int or not 0 < revision_id < 2**63:
        raise ValueError('invalid piece revision number')


def encode_piece(piece: StationPiece) -> bytes:
    if not isinstance(piece, StationPiece):
        raise ValueError('expected a station piece')
    _validate_origin(piece.station, piece.revision_id)
    evidence = piece.evidence
    validate_exit_evidence(evidence)
    ordered_rows = sorted(evidence.rows, key=lambda row: (row.entry, row.exit, row.hours))
    body = {
        'version': 1,
        'station': piece.station,
        'revision': piece.revision_id,
        'entries': sorted(evidence.entries),
        'exits': sorted(evidence.exits),
        'rows': [
            [row.entry, row.exit, row.hours, row.probability.numerator,
             row.probability.denominator]
            for row in ordered_rows
        ],
    }
    return json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _object(fields: list[tuple[str, object]]) -> dict:
    result = dict(fields)
    if len(result) != len(fields):
        raise ValueError('duplicate key in station piece')
    return result


def _decode_row(value: object) -> ExitProbability:
    if not isinstance(value, list) or len(value) != 5:
        raise ValueError('invalid evidence row shape')
    entry, exit_name, hours, numerator, denominator = value
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise ValueError('invalid rational encoding')
    return ExitProbability(entry, exit_name, hours, Fraction(numerator, denominator))


def decode_piece(payload: bytes) -> StationPiece:
    if not isinstance(payload, bytes):
        raise ValueError('station piece payload must be bytes')
    try:
        body = json.loads(payload.decode('utf-8'), object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid station piece JSON') from error
    keys = {'version', 'station', 'revision', 'entries', 'exits', 'rows'}
    if not isinstance(body, dict) or body.keys() != keys:
        raise ValueError('invalid station piece envelope')
    if type(body['version']) is not int or body['version'] != 1:
        raise ValueError('unsupported station piece version')
    _validate_origin(body['station'], body['revision'])
    if any(not isinstance(body[key], list) for key in ('entries', 'exits', 'rows')):
        raise ValueError('invalid station piece directories or rows')
    evidence = FirstExitEvidence(
        tuple(body['entries']), tuple(body['exits']), tuple(map(_decode_row, body['rows']))
    )
    validate_exit_evidence(evidence)
    return StationPiece(
        body['station'], body['revision'],
        FirstExitEvidence(
            tuple(sorted(evidence.entries)),
            tuple(sorted(evidence.exits)),
            tuple(sorted(evidence.rows, key=lambda row: (row.entry, row.exit, row.hours))),
        ),
    )


def piece_identity(piece: StationPiece) -> str:
    return hashlib.sha256(encode_piece(piece)).hexdigest()
