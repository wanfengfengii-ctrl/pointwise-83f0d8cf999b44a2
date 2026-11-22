from exchange.first_exit import compute_first_exits
from exchange.scan_evidence import compute_scan_evidence
from exchange.wire import StationPiece
from source.revision_store import RevisionStore
from source.revisions import get_revision


def issue_piece(
    store: RevisionStore, station: str, revision_id: int, version: int = 1
) -> StationPiece:
    if type(version) is not int or version not in (1, 2):
        raise ValueError('unsupported station piece version')
    revision = get_revision(store, station, revision_id)
    marginal = compute_first_exits(revision)
    if version == 1:
        return StationPiece(revision.station, revision.revision_id, marginal)
    scan = compute_scan_evidence(revision)
    return StationPiece(revision.station, revision.revision_id, marginal, 2, scan)
