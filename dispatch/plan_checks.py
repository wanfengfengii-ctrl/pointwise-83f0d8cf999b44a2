from collections.abc import Sequence

from dispatch.port_bindings import ExitBinding, PortCatalog, ReceivedPort, Transfer
from dispatch.terminals import Terminal


def validate_plan(catalog: PortCatalog, bindings: Sequence[ExitBinding]) -> None:
    seen = set()
    for binding in bindings:
        if not isinstance(binding, ExitBinding) or not isinstance(binding.source, ReceivedPort):
            raise ValueError('invalid outlet binding')
        if binding.source not in catalog.exits:
            raise ValueError(f'outlet outside the selected pieces: {binding.source}')
        if binding.source in seen:
            raise ValueError(f'duplicate binding for outlet {binding.source}')
        seen.add(binding.source)

        destination = binding.destination
        if isinstance(destination, Transfer):
            if not isinstance(destination.target, ReceivedPort) or destination.target not in catalog.entries:
                raise ValueError(f'transfer target outside the selected entries: {destination.target}')
        elif not isinstance(destination, Terminal):
            raise ValueError('destination must be a transfer or terminal')

    missing = catalog.exits - seen
    if missing:
        raise ValueError(f'missing outlet bindings: {sorted(missing)}')
