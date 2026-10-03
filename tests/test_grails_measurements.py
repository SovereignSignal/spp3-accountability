import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'site'))
import grails_measurements as G
import grails_view as V

NOW = '2026-10-03T20:00:00Z'
A, B = '0x' + '1' * 40, '0x' + '2' * 40


def sale(n=1, **kw):
    data = {'id': n, 'ens_name_id': n, 'name': 'example.eth', 'source': 'grails',
            'sale_date': '2026-09-30T12:00:00.000Z', 'transaction_hash': '0x' + f'{n:064x}',
            'buyer_address': A, 'seller_address': B, 'currency_address': G.ETH,
            'sale_price_wei': '1000000000000000001', 'block_number': '0'}
    data.update(kw)
    return data


def page(rows, number=1, more=False):
    return {'success': True, 'data': {'results': rows, 'pagination': {
        'page': number, 'hasNext': more, 'total': len(rows)}}}


class TestGrailsCollector(unittest.TestCase):
    def scan(self, rows):
        return G.collect(lambda n: page(rows, n), G.START, NOW)

    def test_source_filter_is_not_trusted_blindly(self):
        with self.assertRaises(G.CollectionError):
            self.scan([sale(source='opensea')])

    def test_exact_integer_price_and_full_tx_evidence(self):
        rows, cov = self.scan([sale()])
        self.assertEqual(rows[0]['amount_raw'], '1000000000000000001')
        self.assertEqual(rows[0]['source_record']['block_number'], '0')
        self.assertEqual(cov['status'], 'complete_api_window')
        summary, _ = G.build_snapshot(rows, cov, NOW)
        self.assertEqual(summary['observed']['native_eth_volume'], '1.000000000000000001')
        self.assertIsNone(summary['q1_2027']['verified_wallets'])
        self.assertIsNone(summary['q1_2027']['passed'])

    def test_future_window_has_null_not_zero(self):
        rows, cov = self.scan([])
        summary, _ = G.build_snapshot(rows, cov, NOW)
        self.assertEqual(summary['q1_2027']['status'], 'not_started')
        self.assertIsNone(summary['q1_2027']['candidate_observations'])
        self.assertIsNone(summary['secondary_volume_gate']['filtered_volume_eth'])
        self.assertFalse(summary['committee_approved'])

    def test_duplicate_sale_dedupes(self):
        rows, cov = self.scan([sale(), sale()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(cov['duplicates'], 1)

    def test_two_assets_in_one_tx_are_retained(self):
        rows, _ = self.scan([sale(), sale(2, transaction_hash=sale()['transaction_hash'])])
        self.assertEqual(len(rows), 2)

    def test_bundle_volume_requires_allocation(self):
        order = '0x' + 'a' * 64
        tx = sale()['transaction_hash']
        rows, cov = self.scan([sale(order_hash=order), sale(2, transaction_hash=tx, order_hash=order)])
        summary, _ = G.build_snapshot(rows, cov, NOW)
        self.assertEqual(summary['observed']['native_eth_volume'], '0')
        self.assertEqual(summary['observed']['exclusions']['bundle_allocation_unverified'], 2)

    def test_conflicting_duplicate_fails(self):
        with self.assertRaises(G.CollectionError):
            self.scan([sale(), sale(sale_price_wei='2')])

    def test_pagination_cap_fails(self):
        with self.assertRaises(G.CollectionError):
            G.collect(lambda n: page([sale(n)], n, True), G.START, NOW, 1)

    def test_missing_pagination_fails(self):
        with self.assertRaises(G.CollectionError):
            G.collect(lambda n: {'success': True, 'data': {'results': []}}, G.START, NOW)

    def test_repeated_page_fails(self):
        with self.assertRaises(G.CollectionError):
            G.collect(lambda n: page([sale()], n, True), G.START, NOW, 3)

    def test_unordered_feed_fails(self):
        with self.assertRaises(G.CollectionError):
            self.scan([sale(sale_date='2026-09-29T00:00:00Z'), sale(2)])

    def test_moving_head_fails(self):
        calls = iter([page([sale()]), page([sale(2)])])
        with self.assertRaises(G.CollectionError):
            G.collect(lambda n: next(calls), G.START, NOW)

    def test_older_boundary_stops(self):
        rows, cov = self.scan([sale(), sale(2, sale_date='2026-09-01T00:00:00Z')])
        self.assertEqual(len(rows), 1)
        self.assertEqual(cov['boundary'], 'start_boundary_reached')

    def test_self_zero_unknown_currency_exclusions(self):
        rows, cov = self.scan([sale(seller_address=A), sale(2, sale_price_wei='0'),
                               sale(3, currency_address='0x' + '3' * 40)])
        summary, _ = G.build_snapshot(rows, cov, NOW)
        obs = summary['observed']
        self.assertEqual(obs['candidate_secondary_buyers'], 0)
        self.assertEqual(len(obs['exclusions']), 3)

    def test_eth_weth_separate_and_buyers_deduped(self):
        rows, cov = self.scan([sale(), sale(2, currency_address=G.WETH)])
        summary, _ = G.build_snapshot(rows, cov, NOW)
        obs = summary['observed']
        self.assertEqual(obs['candidate_secondary_buyers'], 1)
        self.assertEqual(obs['candidate_sale_participants'], 2)
        self.assertEqual(obs['weth_volume'], '1.000000000000000001')

    def test_disappeared_and_amended_records_preserve_evidence(self):
        rows, _ = self.scan([sale(), sale(2)])
        amended = G.normalize(sale(sale_price_wei='5'), NOW)
        new = G.merge_evidence({'events': rows}, [amended], NOW)
        self.assertEqual(len(new), 2)
        removed = [e for e in new if not e['present_in_latest_scan']]
        self.assertEqual(len(removed), 1)
        changed = [e for e in new if e['present_in_latest_scan']][0]
        self.assertEqual(changed['revisions'][0]['prior_source_record']['sale_price_wei'], '1000000000000000001')

    def test_receipt_success_never_implies_verified_sale(self):
        rows, cov = self.scan([sale()])
        class Reader:
            def _rpc(self, method, args):
                return {'transactionHash': args[0], 'status': '0x1', 'blockNumber': '0x123', 'blockHash': '0x' + 'a' * 64}
        G.check_receipts(rows, Reader(), NOW, 1)
        self.assertEqual(rows[0]['receipt']['status'], 'success')
        self.assertFalse(rows[0]['receipt']['sale_amount_verified'])
        self.assertEqual(rows[0]['verification'], 'api_reported')
        summary, _ = G.build_snapshot(rows, cov, NOW)
        self.assertIsNone(summary['q1_2027']['passed'])

    def test_bad_source_does_not_replace_last_good_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'observations.json'
            path.write_text('{"prior":"keep"}')
            with patch.object(G.SourceClient, 'page', return_value={'success': False}):
                self.assertEqual(G.main(['--output-dir', tmp, '--receipt-limit', '0']), 1)
            self.assertEqual(path.read_text(), '{"prior":"keep"}')

    def test_no_signature_or_financial_release_fields(self):
        rows, cov = self.scan([sale()])
        summary, evidence = G.build_snapshot(rows, cov, NOW)
        self.assertEqual(summary['gate_status'], 'observation_only')
        self.assertEqual(summary['snapshot_id'], evidence['snapshot_id'])
        self.assertNotIn('signed', json.dumps(summary))


class TestGrailsView(unittest.TestCase):
    def test_missing_data_is_unavailable_not_zero(self):
        html = V.page_measurements({})
        self.assertIn('Collector data unavailable', html)
        self.assertNotIn('>0<', html)

    def test_current_snapshot_is_not_a_gate_pass(self):
        rows, cov = G.collect(lambda n: page([sale()]), G.START, NOW)
        doc, _ = G.build_snapshot(rows, cov, NOW)
        html = V.page_measurements({'grails': doc, 'now': G.timestamp(NOW).timestamp()})
        self.assertIn('Unscored', html)
        self.assertIn('Measurement window has not started', html)
        self.assertIn('etherscan.io/tx/', html)
        self.assertNotIn('gate passed', html.lower())

    def test_staleness_and_html_escaping(self):
        rows, cov = G.collect(lambda n: page([sale(name='<script>alert(1)</script>')]), G.START, NOW)
        doc, _ = G.build_snapshot(rows, cov, NOW)
        html = V.page_measurements({'grails': doc, 'now': G.timestamp(NOW).timestamp() + 86400})
        self.assertIn('data is stale', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
