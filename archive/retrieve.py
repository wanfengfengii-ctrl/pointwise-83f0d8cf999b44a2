from dataclasses import dataclass

from archive.receipts import FirstReceipt, read_first_receipt
from archive.report_store import ReportStore, StoredReport
from archive.scan_publish import ScanReportHandover
from archive.scan_report_store import ScanReportStore


@dataclass(frozen=True)
class ReportHandover:
    report: StoredReport
    first_receipt: FirstReceipt


def get_report(store: ReportStore, report_id: int) -> ReportHandover | None:
    if type(report_id) is not int or not 0 < report_id < 2**63:
        raise ValueError('invalid report number')
    report = store.get(report_id)
    if report is None:
        return None
    receipt = read_first_receipt(store.connection, report_id)
    if receipt is None:
        raise RuntimeError(f'report {report_id} is missing its first receipt')
    return ReportHandover(report, receipt)


def get_scan_report(store: ScanReportStore, report_id: int) -> ScanReportHandover | None:
    if type(report_id) is not int or not 0 < report_id < 2**63:
        raise ValueError('invalid report number')
    report = store.get(report_id)
    if report is None:
        return None
    receipt = store.get_receipt(report_id)
    if receipt is None:
        raise RuntimeError(f'scan report {report_id} is missing its first receipt')
    return ScanReportHandover(report, receipt)
