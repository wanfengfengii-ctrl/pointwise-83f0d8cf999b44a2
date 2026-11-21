from enum import Enum


class Terminal(Enum):
    RELEASE = 'release'
    REJECT = 'reject'


def parse_terminal(label: str) -> Terminal:
    if not isinstance(label, str):
        raise ValueError('terminal label must be a string')
    try:
        return Terminal(label)
    except ValueError as error:
        raise ValueError(f'unknown terminal {label!r}') from error
