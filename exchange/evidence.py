from collections import defaultdict
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


@dataclass(frozen=True)
class ScanSupport:
    entry: str
    label: str
    hour: int
    probability: Fraction


@dataclass(frozen=True)
class ScanJointExit:
    entry: str
    label: str
    scan_hour: int
    exit: str
    exit_hour: int
    probability: Fraction


@dataclass(frozen=True)
class ScanEvidence:
    entries: tuple[str, ...]
    exits: tuple[str, ...]
    labels: tuple[str, ...]
    support_rows: tuple[ScanSupport, ...]
    joint_rows: tuple[ScanJointExit, ...]


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


def validate_scan_evidence(evidence: ScanEvidence) -> None:
    if not isinstance(evidence, ScanEvidence):
        raise ValueError('expected scan evidence')
    entries = _names(evidence.entries)
    exits = _names(evidence.exits)
    labels = _names(evidence.labels)
    if not isinstance(evidence.support_rows, tuple) or not isinstance(evidence.joint_rows, tuple):
        raise ValueError('scan evidence rows must be tuples')

    support_totals: dict[tuple[str, str, int], Fraction] = defaultdict(Fraction)
    entry_support: dict[str, Fraction] = defaultdict(Fraction)
    coordinates = set()
    for row in evidence.support_rows:
        if not isinstance(row, ScanSupport):
            raise ValueError('invalid scan support row')
        if row.entry not in entries or row.label not in labels:
            raise ValueError('scan support outside public directories')
        if type(row.hour) is not int or not 0 <= row.hour <= MAX_EVIDENCE_HOUR:
            raise ValueError('scan hour outside public window')
        if not isinstance(row.probability, Fraction) or row.probability <= 0:
            raise ValueError('scan support must be a positive Fraction')
        key = (row.entry, row.label, row.hour)
        if key in coordinates:
            raise ValueError('duplicate scan support coordinate')
        coordinates.add(key)
        support_totals[key] += row.probability
        entry_support[row.entry] += row.probability
        if entry_support[row.entry] > 1:
            raise ValueError('entry probability exceeds 1')

    joint_totals: dict[tuple[str, str, int], Fraction] = defaultdict(Fraction)
    for row in evidence.joint_rows:
        if not isinstance(row, ScanJointExit):
            raise ValueError('invalid joint scan row')
        if row.entry not in entries or row.exit not in exits or row.label not in labels:
            raise ValueError('joint scan row outside public directories')
        for hour in (row.scan_hour, row.exit_hour):
            if type(hour) is not int or not 0 <= hour <= MAX_EVIDENCE_HOUR:
                raise ValueError('joint scan hour outside public window')
        if row.exit_hour < row.scan_hour:
            raise ValueError('exit cannot precede the supporting scan')
        if not isinstance(row.probability, Fraction) or row.probability <= 0:
            raise ValueError('joint scan mass must be a positive Fraction')
        key = (row.entry, row.label, row.scan_hour)
        if key not in support_totals:
            raise ValueError('joint scan mass lacks supporting first scan')
        joint_totals[key] += row.probability
        if joint_totals[key] > support_totals[key]:
            raise ValueError('joint scan mass exceeds its first-scan support')
