import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'site')]
import grails_chain as C

SELLER, BUYER, FEE = ('0x' + x * 40 for x in ('1', '2', '3'))
TX, ORDER, BLOCK_HASH = ('0x' + x * 64 for x in ('a', 'b', 'c'))
PORT = '0x0000000000000068f116a894984e2db1123eb395'
TIME = 1791050400


def w(v):
    return int(v, 16).to_bytes(32, 'big') if isinstance(v, str) else v.to_bytes(32, 'big')


def t(v):
    return '0x' + w(v).hex()


def arr(entries):
    return w(len(entries)) + b''.join(w(v) for row in entries for v in row)


def fill(offer=None, cons=None, offerer=SELLER, recipient=BUYER):
    offer = offer if offer is not None else [(2, C.BASE, 7, 1)]
    cons = cons if cons is not None else [(1, C.WETH, 0, 95, SELLER), (1, C.WETH, 0, 5, FEE)]
    a, b = arr(offer), arr(cons)
    data = w(ORDER) + w(recipient) + w(128) + w(128+len(a)) + a + b
    return {'address': PORT, 'topics': [C.FULFILLED, t(offerer), t(C.ZERO)],
            'data': '0x' + data.hex(), 'logIndex': '0x3'}


def fixture():
    sale = {'key': 'k', 'source_record': {'token_id': '7'}, 'asset_id': '7',
            'source_record_sha256': 's', 'transaction_hash': TX, 'order_hash': ORDER,
            'currency': C.WETH, 'buyer': BUYER, 'seller': SELLER,
            'amount_raw': '100', 'sale_at': C.iso(TIME+2)}
    receipt = {'transactionHash': TX, 'status': '0x1', 'blockNumber': '0x100',
               'blockHash': BLOCK_HASH, 'logs': [fill(),
                {'address': C.BASE, 'topics': [C.TRANSFER, t(SELLER), t(BUYER), t(7)], 'data': '0x', 'logIndex': '0x2'}]}
    header = {'number': '0x100', 'hash': BLOCK_HASH, 'timestamp': hex(TIME)}
    return sale, receipt, header


class TestSeaportEvidence(unittest.TestCase):
    def check(self, mutate=None):
        s, r, h = fixture()
        if mutate:
            mutate(s, r, h)
        return C.verify_sale(s, r, h, 512)

    def test_listing_and_fee_total(self):
        v = self.check()
        self.assertEqual(v['status'], 'matched')
        self.assertEqual(v['onchain_amount_raw'], '100')
        self.assertTrue(v['sale_amount_verified'])
        self.assertFalse(v['attribution_verified'])
        self.assertEqual(v['api_timestamp_delta_seconds'], 2)

    def test_bid_offer_is_gross_not_gross_plus_fees(self):
        def change(s, r, h):
            r['logs'][0] = fill([(1, C.WETH, 0, 100)], [(2, C.BASE, 7, 1, BUYER), (1, C.WETH, 0, 5, FEE)], BUYER, SELLER)
        self.assertEqual(self.check(change)['status'], 'matched')

    def test_native_eth(self):
        def change(s, r, h):
            s['currency'] = C.ZERO
            r['logs'][0] = fill(cons=[(0, C.ZERO, 0, 100, SELLER)])
        self.assertEqual(self.check(change)['status'], 'matched')

    def test_amount_mismatch_does_not_overwrite_api_amount(self):
        s, r, h = fixture(); s['amount_raw'] = '0'
        v = C.verify_sale(s, r, h, 512)
        self.assertEqual(v['status'], 'amount_or_currency_mismatch')
        self.assertEqual(v['onchain_amount_raw'], '100')
        self.assertEqual(s['amount_raw'], '0')
        self.assertFalse(v['sale_amount_verified'])

    def test_spoofed_emitter_not_accepted(self):
        self.assertEqual(self.check(lambda s,r,h: r['logs'][0].update(address=FEE))['status'], 'no_matching_fill')

    def test_wrong_order(self):
        self.assertEqual(self.check(lambda s,r,h: s.update(order_hash='0x'+'d'*64))['status'], 'no_matching_fill')

    def test_wrong_nft(self):
        self.assertEqual(self.check(lambda s,r,h: s['source_record'].update(token_id='8'))['status'], 'asset_mismatch')

    def test_no_token_id_is_unproven(self):
        self.assertEqual(self.check(lambda s,r,h: s.update(source_record={}))['status'], 'asset_identity_unproven')

    def test_wrong_buyer(self):
        self.assertEqual(self.check(lambda s,r,h: s.update(buyer=FEE))['status'], 'participant_mismatch')

    def test_nft_transfer_required(self):
        self.assertEqual(self.check(lambda s,r,h: r.update(logs=[r['logs'][0]]))['status'], 'nft_transfer_not_matched')

    def test_failed_transaction(self):
        self.assertEqual(self.check(lambda s,r,h: r.update(status='0x0'))['status'], 'reverted')

    def test_reorg_invalidates(self):
        self.assertEqual(self.check(lambda s,r,h: h.update(hash='0x'+'d'*64))['status'], 'canonical_hash_mismatch')

    def test_finality_required(self):
        s,r,h=fixture()
        self.assertEqual(C.verify_sale(s,r,h,100)['status'], 'not_finalized')

    def test_missing_receipt(self):
        s,r,h=fixture(); self.assertEqual(C.verify_sale(s,None,h,512)['status'], 'pending')

    def test_bundle_not_allocated(self):
        def change(s,r,h): r['logs'][0]=fill(offer=[(2,C.BASE,7,1),(2,C.BASE,8,1)])
        self.assertEqual(self.check(change)['status'], 'unsupported_structure')

    def test_malformed_array_fails_closed(self):
        def change(s,r,h): r['logs'][0]['data']='0x'+(w(ORDER)+w(BUYER)+w(2**200)+w(128)).hex()
        self.assertEqual(self.check(change)['status'], 'malformed_seaport_log')

    def test_multiple_identical_fills_ambiguous(self):
        def change(s,r,h): r['logs'].append(copy.deepcopy(r['logs'][0]))
        self.assertEqual(self.check(change)['status'], 'ambiguous_fills')

    def test_out_of_window_not_counted(self):
        v=self.check(lambda s,r,h: h.update(timestamp=hex(1)))
        self.assertEqual(v['status'],'outside_observation_window')

    def test_one_fill_cannot_be_counted_twice(self):
        s,r,h=fixture(); s['settlement']=C.verify_sale(s,r,h,512)
        summary=C.settlement_summary([s,copy.deepcopy(s)])
        self.assertEqual(summary['weth_wei'],'100')
        self.assertEqual(summary['positive_nonself_matches'],1)
        self.assertEqual(summary['duplicate_fill_references'],1)


def registrar(kind='registration', ref=None):
    label=b'example'; labelhash='0x'+C.keccak(label).hex()
    registration=kind=='registration'
    parts=[w(160 if registration else 128), w(100)]
    if registration: parts.append(w(20))
    parts.extend([w(1800000000),w(ref or C.REFERRER)])
    parts.extend([w(len(label)), label.ljust(32,b'\0')])
    topics=[C.REGISTERED if registration else C.RENEWED,labelhash]
    if registration: topics.append(t(BUYER))
    return {'address':C.CONTROLLER,'topics':topics,'data':'0x'+b''.join(parts).hex(),
            'blockNumber':'0x100','blockHash':BLOCK_HASH,'transactionHash':TX,'logIndex':'0x4'}


class TestRegistrarEvidence(unittest.TestCase):
    def test_registration_owner_cost_premium_and_referrer(self):
        e=C.decode_registrar(registrar())
        self.assertEqual(e['cost_wei'],'120')
        self.assertEqual(e['registered_owner'],BUYER)
        self.assertTrue(e['referrer_tag_verified'])
        self.assertFalse(e['payer_verified'])
        self.assertIsNone(e['transaction_initiator'])

    def test_renewal_does_not_invent_owner_or_payer(self):
        e=C.decode_registrar(registrar('renewal'))
        self.assertEqual(e['cost_wei'],'100'); self.assertIsNone(e['registered_owner'])
        self.assertFalse(e['payer_verified'])

    def test_helper_path(self):
        log=registrar('renewal'); log['address']=C.HELPER; log['topics'][0]=C.REFERRED
        self.assertEqual(C.decode_registrar(log)['kind'],'renewal')

    def test_other_referrer_filtered(self):
        self.assertIsNone(C.decode_registrar(registrar(ref='0x'+'d'*64)))

    def test_wrong_labelhash_rejected(self):
        log=registrar();log['topics'][1]='0x'+'d'*64
        with self.assertRaises(C.EvidenceError):C.decode_registrar(log)

    def test_wrong_contract_rejected(self):
        log=registrar();log['address']=BUYER
        with self.assertRaises(C.EvidenceError):C.decode_registrar(log)

    def test_method_never_claims_gate_approval(self):
        e=C.decode_registrar(registrar()); e['ambiguous_duplicate']=False
        doc={'events':[e],'coverage':'backfill_in_progress','through_timestamp':C.iso(TIME),'through_block':256,'target_finalized_block':512}
        m=C.registrar_summary(doc)
        self.assertEqual(m['registrations'],1)
        self.assertFalse(m['gate_eligible']);self.assertIsNone(m['verified_paying_wallets'])
        self.assertIsNone(m['usd_revenue'])

    def test_failed_refresh_leaves_published_files_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'chain.json'; f.write_text('{"sentinel":true}')
            self.assertEqual(C.main(['--data-dir',d]),1)
            self.assertEqual(json.loads(f.read_text()),{'sentinel':True})


class FakeReader:
    def __init__(self):
        self.requests=[]
    def block(self,n):
        return {'number':hex(n),'hash':'0x'+f'{n:064x}','timestamp':hex(TIME+n)}
    def rpc(self,method,params):
        self.requests.append((method,params))
        if method!='eth_getLogs':raise AssertionError(method)
        p=params[0]; left=int(p['fromBlock'],16);right=int(p['toBlock'],16)
        if left<=256<=right:
            log=registrar();log['blockHash']=self.block(256)['hash'];return [log]
        return []


class TestRegistrarCheckpoint(unittest.TestCase):
    def test_budget_is_partial_and_resume_is_exact(self):
        reader=FakeReader()
        with patch.object(C,'first_block',return_value=256):
            initial=C.index_registrar(reader,{},reader.block(300),10,C.iso(TIME))
        self.assertEqual(initial['through_block'],265)
        self.assertEqual(initial['coverage'],'backfill_in_progress')
        self.assertEqual(len(initial['events']),1)
        nxt=C.index_registrar(reader,initial,reader.block(300),100,C.iso(TIME+100))
        self.assertEqual(nxt['coverage'],'current_known_contracts')
        self.assertEqual(nxt['through_block'],300)
        self.assertEqual(len(nxt['events']),1)
        self.assertEqual(int(reader.requests[-1][1][0]['fromBlock'],16),266)

    def test_reorg_checkpoint_is_rejected(self):
        reader=FakeReader()
        with patch.object(C,'first_block',return_value=256):
            initial=C.index_registrar(reader,{},reader.block(300),10,C.iso(TIME))
        initial['through_block_hash']='0x'+'f'*64
        with self.assertRaises(C.EvidenceError):
            C.index_registrar(reader,initial,reader.block(300),100,C.iso(TIME+100))

    def test_failed_page_does_not_mutate_previous_checkpoint(self):
        reader=FakeReader()
        with patch.object(C,'first_block',return_value=256):
            initial=C.index_registrar(reader,{},reader.block(300),10,C.iso(TIME))
        old=copy.deepcopy(initial)
        with patch.object(reader,'rpc',side_effect=C.EvidenceError('failed')):
            with self.assertRaises(C.EvidenceError): C.index_registrar(reader,initial,reader.block(300),100,C.iso(TIME))
        self.assertEqual(initial,old)


class TestChainView(unittest.TestCase):
    def test_missing_is_unavailable(self):
        import grails_chain_view as V
        h=V.chain_section({})
        self.assertIn('unavailable',h);self.assertNotIn('0 ETH',h)

    def test_stale_and_mixed_snapshots_and_escaping(self):
        import grails_chain_view as V
        c={'now':TIME+20000,'grails':{'snapshot_id':'new'},'grails_chain':{
            'observed_at':C.iso(TIME),'source_snapshot_id':'old','finalized_block':100,
            'recent_results':[{'name':'<script>bad</script>','transaction_hash':'javascript:alert(1)'}]}}
        h=V.chain_section(c)
        self.assertIn('stale',h);self.assertIn('snapshots differ',h)
        self.assertNotIn('<script>',h);self.assertNotIn('href="javascript:',h)
        self.assertIn('No gate is approved',h)


if __name__=='__main__': unittest.main()
