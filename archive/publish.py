from archive.receipts import insert_first_receipt
from archive.report_store import ReportDraft as ReportDraft
from archive.report_store import ReportStore
from archive.retrieve import ReportHandover


def publish_report(store: ReportStore, draft: ReportDraft) -> ReportHandover:
    with store.transaction() as connection:
        report = store.insert(connection, draft)
        receipt = insert_first_receipt(connection, report.report_id)
    return ReportHandover(report, receipt)
