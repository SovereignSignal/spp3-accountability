import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'site'))
import grails_view as G


class TestEvidenceCopy(unittest.TestCase):
    def fixture(self):
        return {'now': 1791058211, 'grails': {
            'observed_at': '2026-10-03T20:10:11Z',
            'observed': {'api_sales': 50},
            'q1_2027': {'status': 'not_started'},
            'warnings': ['API snapshot excludes registrations and renewals.'],
        }}

    def test_api_receipt_limits_do_not_deny_separate_settlement_verification(self):
        html = G.page_measurements(self.fixture())
        self.assertIn('API-reported observations', html)
        self.assertIn('Independent settlement and registrar checks are reported separately', html)
        self.assertIn('API-only methodology limits', html)
        self.assertNotIn('Transaction receipt checks do not verify sale amounts', html)

    def test_revenue_gate_is_unscored_without_claiming_no_collector_exists(self):
        html = G.page_measurements(self.fixture())
        self.assertIn('Revenue gates', html)
        self.assertIn('No signing-date baseline or agreed USD comparison', html)
        self.assertIn('Unscored', html)
        self.assertNotIn('No signing-date baseline or revenue collector has been established here', html)


if __name__ == '__main__':
    unittest.main()
