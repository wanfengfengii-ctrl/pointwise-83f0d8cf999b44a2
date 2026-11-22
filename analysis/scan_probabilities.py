from collections import defaultdict
from fractions import Fraction

from analysis.horizon import validate_deadline
from analysis.rational_system import solve_rational_system
from dispatch.plan_store import StoredPlan
from dispatch.port_bindings import ReceivedPort
from dispatch.terminals import Terminal
from exchange.piece_store import ReceivedPiece


def _instant_matrix(entries, instant_transfers):
    """Settled-mass closure: x_s = seed_s + sum of instant inflows into s."""
    size = len(entries)
    positions = {entry: index for index, entry in enumerate(entries)}
    matrix = [[Fraction(row == column) for column in range(size)] for row in range(size)]
    for source, index in positions.items():
        for target, mass in instant_transfers[source].items():
            matrix[index][positions[target]] -= mass
    return matrix


def _solve(matrix, constants):
    if not any(constants):
        return solve_rational_system(matrix, constants)
    return constants


def scan_report_probabilities(
    plan: StoredPlan,
    pieces: tuple[ReceivedPiece, ...],
    start: ReceivedPort,
    deadline: int,
    scan_received_id: int,
    scan_label: str,
    scan_hour: int,
):
    """Exact (support, joint) probabilities for the whole-route first scan.

    The wired route is simulated under one absolute clock with two mass pools.
    Unscanned mass (U) can still produce the route's first scan. Matched mass
    (M) already produced the requested first scan, but whenever it enters a new
    piece visit that visit is still resolved through the same frozen evidence:
    its own first scans cannot change the established match, while only the
    matched tag's joint exits count toward the on-time release.

    Every entry visit partitions its entry mass by the frozen V2 evidence:
    first-scan support rows (each carrying joint first-exit outcomes) and an
    unscanned residual following the plain marginal exits. Instantaneous
    transfer classes settle through exact rational linear systems.
    """
    validate_deadline(deadline)
    if type(scan_hour) is not int or not 0 <= scan_hour <= deadline:
        raise ValueError('scan hour must be an integer from 0 through the deadline')
    if not isinstance(scan_label, str) or not scan_label.strip():
        raise ValueError('scan label is required')
    if type(scan_received_id) is not int or not 0 < scan_received_id < 2**63:
        raise ValueError('invalid scan received-piece number')

    bindings = {binding.source: binding.destination for binding in plan.bindings}
    by_id = {item.received_id: item for item in pieces}
    if scan_received_id not in by_id:
        raise LookupError(f'unknown received piece {scan_received_id}')

    entries: list[ReceivedPort] = []
    for item in pieces:
        entries.extend(
            ReceivedPort(item.received_id, name) for name in item.piece.evidence.entries
        )
    entries = sorted(set(entries))
    if start not in entries:
        raise ValueError('starting entry is outside the dispatch plan')

    # marginal[entry][(exit, relative hour)] = destination -> exact probability
    marginal: dict[ReceivedPort, dict[tuple[str, int], dict[object, Fraction]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(Fraction))
    )
    # joints[entry][(label, relative scan hour)][(relative exit hour, destination)]
    joints: dict[ReceivedPort, dict[tuple[str, int], dict[tuple[int, object], Fraction]]] = (
        defaultdict(lambda: defaultdict(lambda: defaultdict(Fraction)))
    )
    # support_index[entry][(label, relative scan hour)] = exact support mass
    support_index: dict[ReceivedPort, dict[tuple[str, int], Fraction]] = defaultdict(dict)
    for item in pieces:
        received_id = item.received_id
        for row in item.piece.evidence.rows:
            entry = ReceivedPort(received_id, row.entry)
            destination = bindings[ReceivedPort(received_id, row.exit)]
            target = destination.target if hasattr(destination, 'target') else destination
            marginal[entry][(row.exit, row.hours)][target] += row.probability
        scan_evidence = getattr(item.piece, 'scan_evidence', None)
        if scan_evidence is None:
            continue
        for row in scan_evidence.support_rows:
            support_index[ReceivedPort(received_id, row.entry)][(row.label, row.hour)] = \
                row.probability
        for row in scan_evidence.joint_rows:
            entry = ReceivedPort(received_id, row.entry)
            destination = bindings[ReceivedPort(received_id, row.exit)]
            target = destination.target if hasattr(destination, 'target') else destination
            joints[entry][(row.label, row.scan_hour)][(row.exit_hour, target)] += row.probability

    zero = Fraction(0)

    # Plain marginal exits after removing every first-scan joint portion. The
    # removed portion belongs to scanned (matched or open) mass; the remainder
    # is what genuinely unscanned mass can follow out of a visit.
    plain_rows: dict[ReceivedPort, dict[tuple[str, int], dict[object, Fraction]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(Fraction))
    )
    # Matched-only exits: union of joint outcomes for the target label at an
    # entry, regardless of relative scan hour. M mass follows these.
    matched_rows: dict[ReceivedPort, dict[tuple[str, int], dict[object, Fraction]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(Fraction))
    )
    for entry in entries:
        any_joint: dict[tuple[int, object], Fraction] = defaultdict(Fraction)
        target_joint: dict[tuple[int, object], Fraction] = defaultdict(Fraction)
        for (label, _relative), outcomes in joints[entry].items():
            for coordinate, mass in outcomes.items():
                any_joint[coordinate] += mass
                if label == scan_label:
                    target_joint[coordinate] += mass
        for coordinate, destinations in marginal[entry].items():
            exit_name, row_hour = coordinate
            for destination, mass in destinations.items():
                joint_mass = any_joint.get((row_hour, destination), zero)
                if joint_mass > mass:
                    raise ValueError('joint scan mass exceeds its marginal exit mass')
                plain_rows[entry][coordinate][destination] = mass - joint_mass
                matched_rows[entry][coordinate][destination] = target_joint.get(
                    (row_hour, destination), zero
                )

    # Instant transfer coefficients per pool.
    instant_u: dict[ReceivedPort, dict[ReceivedPort, Fraction]] = defaultdict(
        lambda: defaultdict(Fraction)
    )
    instant_m: dict[ReceivedPort, dict[ReceivedPort, Fraction]] = defaultdict(
        lambda: defaultdict(Fraction)
    )
    for entry in entries:
        for coordinate, destinations in marginal[entry].items():
            if coordinate[1] == 0:
                for target, mass in destinations.items():
                    if isinstance(target, ReceivedPort):
                        instant_u[entry][target] += mass
        for coordinate, destinations in plain_rows[entry].items():
            if coordinate[1] == 0:
                for target, mass in destinations.items():
                    if isinstance(target, ReceivedPort) and mass:
                        instant_m[entry][target] += mass

    u_matrix = _instant_matrix(entries, instant_u)
    m_matrix = _instant_matrix(entries, instant_m)
    positions = {entry: index for index, entry in enumerate(entries)}

    def is_target(entry: ReceivedPort, label: str, absolute_hour: int) -> bool:
        return (
            label == scan_label
            and entry.received_id == scan_received_id
            and absolute_hour == scan_hour
        )

    pending_u: dict[int, dict[ReceivedPort, Fraction]] = defaultdict(
        lambda: defaultdict(Fraction)
    )
    pending_m: dict[int, dict[ReceivedPort, Fraction]] = defaultdict(
        lambda: defaultdict(Fraction)
    )
    # Delayed scan approaches: absolute hour -> entry ->
    # (label, relative scan hour, visit landing hour) -> mass.
    pending_scan: dict[
        int, dict[ReceivedPort, dict[tuple[str, int, int], Fraction]]
    ] = defaultdict(lambda: defaultdict(lambda: defaultdict(Fraction)))
    pending_u[0][start] = Fraction(1)
    support = zero
    joint_release = zero

    def route_scan_joint(entry, key, landing_hour, event_mass, m_constants):
        """Route a matching first scan's joint outcomes as matched mass.

        A scan on another label, piece or absolute hour is not the route's
        target first scan, so its mass leaves the unscanned pool without ever
        entering the matched pool or contributing a joint release.
        """
        nonlocal joint_release
        label, _relative_scan_hour = key
        if not is_target(entry, label, landing_hour + _relative_scan_hour):
            return
        scan_support = support_index[entry][key]
        for (exit_hour, destination), outcome_mass in joints[entry][key].items():
            absolute_exit = landing_hour + exit_hour
            if absolute_exit > deadline:
                continue
            share = event_mass * outcome_mass / scan_support
            if isinstance(destination, ReceivedPort):
                if exit_hour == 0:
                    m_constants[positions[destination]] += share
                else:
                    pending_m[absolute_exit][destination] += share
            elif destination is Terminal.RELEASE:
                joint_release += share

    for hour in range(deadline + 1):
        # 1. Settle instantaneous U and M classes over this hour's arrivals.
        u_values = _solve(u_matrix, [pending_u[hour].get(entry, zero) for entry in entries])
        m_constants = [pending_m[hour].get(entry, zero) for entry in entries]

        # 2. Resolve first scans on each live U visit, scheduling later scans.
        for entry, u_mass in zip(entries, u_values):
            if not u_mass:
                continue
            supports = support_index[entry]
            total_support = sum(supports.values(), zero)
            if total_support > 1:
                raise ValueError('entry scan support exceeds one')
            for key, scan_support in supports.items():
                _label, relative_hour = key
                event_mass = u_mass * scan_support
                if relative_hour == 0:
                    if is_target(entry, key[0], hour):
                        support += event_mass
                    route_scan_joint(entry, key, hour, event_mass, m_constants)
                elif hour + relative_hour <= deadline:
                    pending_scan[hour + relative_hour][entry][
                        (key[0], relative_hour, hour)
                    ] += event_mass

            residual_u = u_mass * (1 - total_support)
            for coordinate, destinations in plain_rows[entry].items():
                _exit_name, row_hour = coordinate
                if row_hour == 0:
                    continue
                when = hour + row_hour
                if when > deadline:
                    continue
                for destination, mass in destinations.items():
                    share = residual_u * mass
                    if share and isinstance(destination, ReceivedPort):
                        pending_u[when][destination] += share

        # 3. Positive-hour scans land at their marked state this absolute hour.
        for entry, per_scan in pending_scan[hour].items():
            for (label, relative_hour, landing_hour), event_mass in per_scan.items():
                if not event_mass:
                    continue
                if is_target(entry, label, hour):
                    support += event_mass
                route_scan_joint(
                    entry, (label, relative_hour), landing_hour, event_mass, m_constants
                )

        # 4. Settle matched mass and route it only through target-label joint
        #    exits. A new visit reached by M mass contributes a release only if
        #    that visit's evidence tags the path with the matched label.
        m_values = _solve(m_matrix, m_constants)
        for entry, m_mass in zip(entries, m_values):
            if not m_mass:
                continue
            for coordinate, destinations in matched_rows[entry].items():
                _exit_name, row_hour = coordinate
                when = hour + row_hour
                if when > deadline:
                    continue
                for destination, mass in destinations.items():
                    share = m_mass * mass
                    if not share:
                        continue
                    if isinstance(destination, Terminal):
                        if destination is Terminal.RELEASE:
                            joint_release += share
                    elif row_hour > 0:
                        pending_m[when][destination] += share

    return support, joint_release
