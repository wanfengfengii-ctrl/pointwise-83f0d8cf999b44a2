"""Run this project's full unittest suite from the repository root."""
import json
import sys
import unittest
from pathlib import Path


def run_first_scan_fixture() -> None:
    # Exercise both the V1 marginal projection and the V2 first-scan hand
    # calculations against the hand-computed sample expectations.
    import check_scan_fixture

    fixture = Path(__file__).with_name('samples') / 'first_scan.json'
    data = json.loads(fixture.read_text(encoding='utf-8'))
    check_scan_fixture.check_v1(data)
    check_scan_fixture.check_v2(data)


def main():
    run_first_scan_fixture()
    suite = unittest.defaultTestLoader.discover('tests', top_level_dir='.')
    if suite.countTestCases():
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        print('No tests discovered in tests/', file=sys.stderr)
        result = unittest.TestResult()
    for status, failed_cases in (
        ('FAIL', (test for test, _ in result.failures)),
        ('ERROR', (test for test, _ in result.errors)),
        ('UNEXPECTED_SUCCESS', result.unexpectedSuccesses),
    ):
        for test in failed_cases:
            print(f'{status} {test.id()}')
    successful = result.testsRun > 0 and result.wasSuccessful()
    print(f'TEST SUMMARY total={result.testsRun} failures={len(result.failures)} '
          f'errors={len(result.errors)} skipped={len(result.skipped)} '
          f'expected_failures={len(result.expectedFailures)} '
          f'unexpected_successes={len(result.unexpectedSuccesses)} '
          f'result={"PASS" if successful else "FAIL"}')
    return 0 if successful else 1


if __name__ == '__main__':
    raise SystemExit(main())
