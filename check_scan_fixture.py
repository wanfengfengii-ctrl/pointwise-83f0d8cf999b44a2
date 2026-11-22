import json
import sqlite3
from contextlib import closing
from fractions import Fraction
from pathlib import Path

from analysis.requests import ReportRequest, ScanReportRequest, sign_report, sign_scan_report
from archive.report_store import ReportStore
from archive.retrieve import get_scan_report
from archive.scan_report_store import ScanReportStore
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
from source.routes import RouteDraft


def check_v1(data):
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


def check_v2(data):
    station = data["station"]
    observation = data["observation"]
    with closing(sqlite3.connect(":memory:")) as station_db:
        revisions = RevisionStore(station_db)
        revisions.initialize()
        frozen = []
        for case in data["versions"]:
            base = _draft(data["route"])
            draft = RouteDraft(
                base.ports, base.transitions, base.work_order, case["scan_labels"]
            )
            revision_id = save_revision(revisions, station, draft)
            frozen.append((case, issue_piece(revisions, station, revision_id, version=2)))

    with closing(sqlite3.connect(":memory:")) as db:
        pieces, plans, scan_reports = PieceStore(db), PlanStore(db), ScanReportStore(db)
        for store in (pieces, plans, scan_reports):
            store.initialize()
        for case, piece in frozen:
            # Re-issuing the same revision returns identical canonical bytes.
            assert encode_piece(piece) == encode_piece(piece)
            received_id = receive_piece(
                pieces, encode_piece(piece), piece.identity
            ).received_id
            plan = save_plan(
                plans, pieces, (received_id,),
                (ExitBinding(ReceivedPort(received_id, "pass"), Terminal.RELEASE),),
            )
            request = ScanReportRequest(
                plan.plan_id, ReceivedPort(received_id, "in"), observation["deadline"],
                received_id, observation["label"], observation["hour"],
            )
            handover = sign_scan_report(scan_reports, plans, pieces, request)
            report = handover.report
            actual = [
                str(report.support), str(report.joint),
                None if report.conditional is None else str(report.conditional),
            ]
            assert actual == case["expected"], (case["name"], actual, case["expected"])
            assert get_scan_report(scan_reports, report.report_id) == handover
            print(f"V2 {case['name']}: support={actual[0]} joint={actual[1]} "
                  f"conditional={actual[2]}")


if __name__ == "__main__":
    path = Path(__file__).with_name("samples") / "first_scan.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    check_v1(data)
    check_v2(data)
    print("first scan fixture: all V1 and V2 expectations match")
