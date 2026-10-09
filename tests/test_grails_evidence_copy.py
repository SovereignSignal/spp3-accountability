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


class TestZeroPriceRecords(unittest.TestCase):
    """27 of the API's records are zero-price transfers through other
    contracts. Shown unexplained, they read as sales that never settled."""

    def test_headline_counts_zero_price_records_separately(self):
        doc = {'now': 1791058211, 'grails': {'observed_at': '2026-10-03T20:10:11Z',
               'observed': {'api_sales': 52, 'exclusions': {'zero_value': 27}}}}
        html = G.marketplace_summary(doc)
        self.assertIn('52 API sale records observed, 27 of them zero-price and excluded from volume', html)

    def test_zero_price_rows_are_labelled_not_unsettled(self):
        import grails_chain_view as V
        tx = '0x' + 'a' * 64
        doc = {'now': 1791058211, 'grails': {'snapshot_id': 's'}, 'grails_chain': {
            'observed_at': '2026-10-03T20:10:11Z', 'source_snapshot_id': 's',
            'settlements': {'statuses': {'matched': 1, 'no_matching_fill': 1}},
            'recent_results': [
                {'name': 'free.eth', 'transaction_hash': tx, 'amount_raw': '0',
                 'settlement': {'status': 'no_matching_fill'}},
                {'name': 'paid.eth', 'transaction_hash': tx, 'amount_raw': '5',
                 'settlement': {'status': 'no_matching_fill'}}]}}
        html = V.chain_section(doc)
        self.assertIn('zero price, excluded', html)
        self.assertIn('not counted in totals', html)
        # A priced record with no fill is still reported as unmatched.
        self.assertIn('>no_matching_fill</span>', html)
