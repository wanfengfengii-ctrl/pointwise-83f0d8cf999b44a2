from collections.abc import Mapping, Set
from dataclasses import dataclass


@dataclass(frozen=True)
class PortDirectory:
    entries: Mapping[str, str]
    exits: tuple[str, ...]


def validate_ports(directory: PortDirectory, states: Set[str]) -> None:
    if not isinstance(directory, PortDirectory):
        raise ValueError('expected a port directory')
    if not isinstance(directory.entries, Mapping) or not directory.entries:
        raise ValueError('at least one public entry is required')
    if not isinstance(directory.exits, tuple):
        raise ValueError('public exits must be a tuple')

    for name, state in directory.entries.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError('invalid public entry name')
        if not isinstance(state, str) or state not in states:
            raise ValueError(f'entry {name!r} refers to an unknown state')

    for name in directory.exits:
        if not isinstance(name, str) or not name.strip():
            raise ValueError('invalid public exit name')
    if len(set(directory.exits)) != len(directory.exits):
        raise ValueError('duplicate public exit')
