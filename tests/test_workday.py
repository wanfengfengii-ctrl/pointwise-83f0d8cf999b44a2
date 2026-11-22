import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from demo import run_demo, run_scan_demo


class WorkdayTests(unittest.TestCase):
    def test_workday_from_route_registration_to_original_handover(self):
        with TemporaryDirectory() as directory:
            day = {case['case_id']: case for case in run_demo(Path(directory))}
        self.assertEqual(set(day), {'v5', 'v8'})
        self.assertEqual(day['v5']['input']['deadlines'], [1, 2])
        self.assertEqual(day['v5']['actual'], ['0', '1/2'])
        self.assertEqual(day['v5']['downstream_actual'], {'expected_releases': '6', 'receipt_linked': True})
        self.assertEqual(day['v8']['actual'], '1/2')
        original_ids = day['v8']['downstream_actual']['received_ids']
        self.assertEqual(len(original_ids), 2)
        self.assertNotIn(day['v8']['input']['corrected_receipt'], original_ids)
        self.assertIs(day['v8']['downstream_actual']['original_receipt'], True)

    def test_scan_demo_closes_station_and_reopens_by_original_id(self):
        with TemporaryDirectory() as directory:
            cases = run_scan_demo(Path(directory))
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case['case_id'], 'scan-v2')
        self.assertEqual(case['actual'], ['1/2', '0', '0'])
        self.assertIs(case['downstream_actual']['receipt_linked'], True)
        self.assertEqual(
            case['downstream_actual']['received_ids'],
            case['downstream_expected']['received_ids'],
        )
