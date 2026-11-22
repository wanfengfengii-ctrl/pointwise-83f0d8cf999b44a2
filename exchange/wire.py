import hashlib
import json
from dataclasses import dataclass
from fractions import Fraction

from exchange.evidence import (
    ExitProbability,
    FirstExitEvidence,
    ScanEvidence,
    ScanJointExit,
    ScanSupport,
    validate_exit_evidence,
    validate_scan_evidence,
)

SUPPORTED_VERSIONS = (1, 2)


@dataclass(frozen=True)
class StationPiece:
    station: str
    revision_id: int
    evidence: FirstExitEvidence
    version: int = 1
    scan_evidence: ScanEvidence | None = None

    @property
    def identity(self) -> str:
        return piece_identity(self)


def _validate_origin(station: str, revision_id: int) -> None:
    if not isinstance(station, str) or not station.strip():
        raise ValueError('station is required')
    if type(revision_id) is not int or not 0 < revision_id < 2**63:
        raise ValueError('invalid piece revision number')


def _fraction(value) -> Fraction:
    numerator, denominator = value
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise ValueError('invalid rational encoding')
    return Fraction(numerator, denominator)


def _exit_row(row: list) -> ExitProbability:
    if len(row) != 5:
        raise ValueError('invalid evidence row shape')
    entry, exit_name, hours, numerator, denominator = row
    if type(hours) is not int:
        raise ValueError('invalid evidence hour')
    return ExitProbability(entry, exit_name, hours, _fraction((numerator, denominator)))


def _scan_row(row: list) -> ScanSupport:
    if len(row) != 5:
        raise ValueError('invalid scan row shape')
    entry, label, hour, numerator, denominator = row
    if type(hour) is not int:
        raise ValueError('invalid scan hour')
    return ScanSupport(entry, label, hour, _fraction((numerator, denominator)))


def _joint_row(row: list) -> ScanJointExit:
    if len(row) != 7:
        raise ValueError('invalid joint scan row shape')
    entry, label, scan_hour, exit_name, exit_hour, numerator, denominator = row
    if type(scan_hour) is not int or type(exit_hour) is not int:
        raise ValueError('invalid joint scan hour')
    return ScanJointExit(
        entry, label, scan_hour, exit_name, exit_hour,
        _fraction((numerator, denominator)),
    )


def _encode_v1(piece: StationPiece) -> bytes:
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


def _encode_v2(piece: StationPiece) -> bytes:
    marginal = piece.evidence
    scan = piece.scan_evidence
    validate_exit_evidence(marginal)
    if not isinstance(scan, ScanEvidence):
        raise ValueError('version 2 pieces require scan evidence')
    validate_scan_evidence(scan)
    if scan.entries != marginal.entries or scan.exits != marginal.exits:
        raise ValueError('scan evidence directories must match the public piece directories')

    ordered_rows = sorted(marginal.rows, key=lambda row: (row.entry, row.exit, row.hours))
    ordered_scans = sorted(scan.support_rows, key=lambda row: (row.entry, row.label, row.hour))
    ordered_joints = sorted(
        scan.joint_rows,
        key=lambda row: (row.entry, row.label, row.scan_hour, row.exit, row.exit_hour),
    )
    body = {
        'version': 2,
        'station': piece.station,
        'revision': piece.revision_id,
        'entries': sorted(marginal.entries),
        'exits': sorted(marginal.exits),
        'evidence': {
            'rows': [
                [row.entry, row.exit, row.hours, row.probability.numerator,
                 row.probability.denominator]
                for row in ordered_rows
            ],
            'labels': sorted(scan.labels),
            'scans': [
                [row.entry, row.label, row.hour, row.probability.numerator,
                 row.probability.denominator]
                for row in ordered_scans
            ],
            'joints': [
                [row.entry, row.label, row.scan_hour, row.exit, row.exit_hour,
                 row.probability.numerator, row.probability.denominator]
                for row in ordered_joints
            ],
        },
    }
    return json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')


def encode_piece(piece: StationPiece) -> bytes:
    if not isinstance(piece, StationPiece):
        raise ValueError('expected a station piece')
    _validate_origin(piece.station, piece.revision_id)
    if piece.version == 1:
        return _encode_v1(piece)
    if piece.version == 2:
        return _encode_v2(piece)
    raise ValueError('unsupported station piece version')


def _object(fields: list[tuple[str, object]]) -> dict:
    result = dict(fields)
    if len(result) != len(fields):
        raise ValueError('duplicate key in station piece')
    return result


def _decode_evidence(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {'rows', 'labels', 'scans', 'joints'}:
        raise ValueError('invalid v2 evidence envelope')
    if any(not isinstance(value[key], list) for key in ('rows', 'labels', 'scans', 'joints')):
        raise ValueError('invalid v2 evidence lists')
    if any(not isinstance(label, str) for label in value['labels']):
        raise ValueError('invalid v2 scan labels')
    return value


def decode_piece(payload: bytes) -> StationPiece:
    if not isinstance(payload, bytes):
        raise ValueError('station piece payload must be bytes')
    try:
        body = json.loads(payload.decode('utf-8'), object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid station piece JSON') from error
    if not isinstance(body, dict):
        raise ValueError('invalid station piece envelope')
    if type(body.get('version')) is not int or body['version'] not in SUPPORTED_VERSIONS:
        raise ValueError('unsupported station piece version')
    _validate_origin(body['station'], body['revision'])

    if body['version'] == 1:
        keys = {'version', 'station', 'revision', 'entries', 'exits', 'rows'}
        if body.keys() != keys or any(
            not isinstance(body[key], list) for key in ('entries', 'exits', 'rows')
        ):
            raise ValueError('invalid station piece envelope')
        evidence = FirstExitEvidence(
            tuple(body['entries']), tuple(body['exits']),
            tuple(map(_exit_row, body['rows'])),
        )
        validate_exit_evidence(evidence)
        return StationPiece(
            body['station'], body['revision'],
            FirstExitEvidence(
                tuple(sorted(evidence.entries)),
                tuple(sorted(evidence.exits)),
                tuple(sorted(
                    evidence.rows, key=lambda row: (row.entry, row.exit, row.hours)
                )),
            ),
        )

    keys = {'version', 'station', 'revision', 'entries', 'exits', 'evidence'}
    if body.keys() != keys or any(
        not isinstance(body[key], list) for key in ('entries', 'exits')
    ):
        raise ValueError('invalid station piece envelope')
    evidence_body = _decode_evidence(body['evidence'])
    marginal = FirstExitEvidence(
        tuple(sorted(body['entries'])), tuple(sorted(body['exits'])),
        tuple(sorted(
            map(_exit_row, evidence_body['rows']),
            key=lambda row: (row.entry, row.exit, row.hours),
        )),
    )
    validate_exit_evidence(marginal)
    scan = ScanEvidence(
        marginal.entries, marginal.exits,
        tuple(sorted(evidence_body['labels'])),
        tuple(sorted(
            map(_scan_row, evidence_body['scans']),
            key=lambda row: (row.entry, row.label, row.hour),
        )),
        tuple(sorted(
            map(_joint_row, evidence_body['joints']),
            key=lambda row: (
                row.entry, row.label, row.scan_hour, row.exit, row.exit_hour
            ),
        )),
    )
    validate_scan_evidence(scan)
    if scan.entries != marginal.entries or scan.exits != marginal.exits:
        raise ValueError('scan evidence directories must match the public piece directories')
    return StationPiece(body['station'], body['revision'], marginal, 2, scan)


def piece_identity(piece: StationPiece) -> str:
    return hashlib.sha256(encode_piece(piece)).hexdigest()
