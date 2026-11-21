import json
import sqlite3
from contextlib import closing
from fractions import Fraction
from pathlib import Path

from analysis.requests import ReportRequest, sign_report
from archive.report_store import ReportStore
from demo import _draft
from dispatch.plan_store import PlanStore
from dispatch.plans import save_plan
from dispatch.port_bindings import ExitBinding, ReceivedPort
from dispatch.terminals import Terminal
from exchange.issue import issue_piece
from exchange.piece_store import PieceStore
from exchange.receive import receive_piece
from exchange.wire import encode_piece
from source.revision_store import RevisionStore
from source.revisions import save_revision


def check(data):
    station = data["station"]
    draft = _draft(data["route"])
    with closing(sqlite3.connect(":memory:")) as db:
        revisions = RevisionStore(db)
        revisions.initialize()
        issued = []
        for case in data["versions"]:
            # Project the fixture onto V1, which has no scan labels.
            revision_id = save_revision(revisions, station, draft)
            piece = issue_piece(revisions, station, revision_id)
            issued.append((case["name"], piece))
    expected = Fraction(data["v1_expected"])
    with closing(sqlite3.connect(":memory:")) as db:
        pieces, plans, reports = PieceStore(db), PlanStore(db), ReportStore(db)
        for store in (pieces, plans, reports):
            store.initialize()
        for name, piece in issued:
            assert piece.evidence == issued[0][1].evidence, name
            received_id = receive_piece(pieces, encode_piece(piece), piece.identity).received_id
            outlet = ReceivedPort(received_id, "pass")
            plan = save_plan(plans, pieces, (received_id,), (ExitBinding(outlet, Terminal.RELEASE),))
            request = ReportRequest(plan.plan_id, ReceivedPort(received_id, "in"),
                                    data["observation"]["deadline"])
            actual = sign_report(reports, plans, pieces, request).report.probability
            assert actual == expected, (name, actual, expected)
            print(f"V1 {name}: release={actual}")


if __name__ == "__main__":
    path = Path(__file__).with_name("samples") / "first_scan.json"
    check(json.loads(path.read_text(encoding="utf-8")))
