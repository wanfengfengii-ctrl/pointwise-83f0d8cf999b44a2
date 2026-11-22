from dataclasses import dataclass

from archive.receipts import FirstReceipt
from archive.scan_report_store import ScanReportDraft, ScanReportStore, StoredScanReport


@dataclass(frozen=True)
class ScanReportHandover:
    report: StoredScanReport
    first_receipt: FirstReceipt


def publish_scan_report(store: ScanReportStore, draft: ScanReportDraft) -> ScanReportHandover:
    with store.transaction() as connection:
        report = store.insert(connection, draft)
        receipt = store.insert_receipt(connection, report.report_id)
    return ScanReportHandover(report, receipt)
