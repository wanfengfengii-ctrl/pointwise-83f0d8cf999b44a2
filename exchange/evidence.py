from dataclasses import dataclass
from fractions import Fraction

MAX_EVIDENCE_HOUR = 24


@dataclass(frozen=True)
class ExitProbability:
    entry: str
    exit: str
    hours: int
    probability: Fraction


@dataclass(frozen=True)
class FirstExitEvidence:
    entries: tuple[str, ...]
    exits: tuple[str, ...]
    rows: tuple[ExitProbability, ...]


def _names(values: tuple[str, ...]) -> set[str]:
    if not isinstance(values, tuple):
        raise ValueError('port directory must be a tuple')
    if any(not isinstance(name, str) or not name.strip() for name in values):
        raise ValueError('invalid public port name')
    names = set(values)
    if len(names) != len(values):
        raise ValueError('duplicate public port')
    return names


def validate_exit_evidence(evidence: FirstExitEvidence) -> None:
    if not isinstance(evidence, FirstExitEvidence):
        raise ValueError('expected first-exit evidence')
    entries = _names(evidence.entries)
    exits = _names(evidence.exits)
    if not entries:
        raise ValueError('at least one entry is required')
    if not isinstance(evidence.rows, tuple):
        raise ValueError('evidence rows must be a tuple')

    totals = dict.fromkeys(entries, Fraction(0))
    coordinates = set()
    for row in evidence.rows:
        if not isinstance(row, ExitProbability):
            raise ValueError('invalid evidence row')
        if not isinstance(row.entry, str) or row.entry not in entries:
            raise ValueError('evidence entry outside public directory')
        if not isinstance(row.exit, str) or row.exit not in exits:
            raise ValueError('evidence exit outside public directory')
        if type(row.hours) is not int or not 0 <= row.hours <= MAX_EVIDENCE_HOUR:
            raise ValueError('evidence hour outside public window')
        if not isinstance(row.probability, Fraction) or row.probability <= 0:
            raise ValueError('evidence probability must be a positive Fraction')
        coordinate = (row.entry, row.exit, row.hours)
        if coordinate in coordinates:
            raise ValueError('duplicate evidence coordinate')
        coordinates.add(coordinate)
        totals[row.entry] += row.probability
        if totals[row.entry] > 1:
            raise ValueError('entry probability exceeds 1')
