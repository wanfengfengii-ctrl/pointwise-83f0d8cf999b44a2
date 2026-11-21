from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from source.ports import PortDirectory


@dataclass(frozen=True)
class Transition:
    target_kind: Literal['state', 'exit']
    target: str
    hours: int
    probability: Fraction


@dataclass(frozen=True)
class RouteDraft:
    ports: PortDirectory
    transitions: Mapping[str, Sequence[Transition]]
    work_order: str


@dataclass(frozen=True)
class RouteRevision:
    station: str
    revision_id: int
    draft: RouteDraft
