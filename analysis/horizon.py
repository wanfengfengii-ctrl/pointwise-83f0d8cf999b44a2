from exchange.evidence import MAX_EVIDENCE_HOUR


def validate_deadline(deadline: int) -> None:
    if type(deadline) is not int or not 0 <= deadline <= MAX_EVIDENCE_HOUR:
        raise ValueError(f'deadline must be an integer from 0 to {MAX_EVIDENCE_HOUR}')


def remaining_hours(available: int, elapsed: int) -> int | None:
    remaining = available - elapsed
    if remaining < 0:
        return None
    return remaining
