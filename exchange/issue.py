from exchange.first_exit import compute_first_exits
from exchange.wire import StationPiece
from source.revision_store import RevisionStore
from source.revisions import get_revision


def issue_piece(store: RevisionStore, station: str, revision_id: int) -> StationPiece:
    revision = get_revision(store, station, revision_id)
    return StationPiece(revision.station, revision.revision_id, compute_first_exits(revision))
