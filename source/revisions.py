from source.revision_store import RevisionStore
from source.route_checks import validate_route
from source.routes import RouteDraft, RouteRevision


def save_revision(store: RevisionStore, station: str, draft: RouteDraft) -> int:
    validate_route(station, draft)
    return store.insert(station, draft)


def get_revision(store: RevisionStore, station: str, revision_id: int) -> RouteRevision:
    if not isinstance(station, str) or not station.strip():
        raise ValueError('station is required')
    if type(revision_id) is not int or not 0 < revision_id < 2**63:
        raise ValueError('invalid revision number')
    revision = store.get(station, revision_id)
    if revision is None:
        raise LookupError(f'unknown revision {revision_id} for station {station!r}')
    return revision
