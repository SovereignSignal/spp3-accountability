import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'site')]
import grails_backfill as B
import grails_chain as G
import grails_measurements as M
from test_grails_chain import registrar, TIME
from test_grails_measurements import sale as api_sale


def explorer_row(block=256,index=3,contract=G.CONTROLLER):
    r=registrar();r.update(address=contract,blockNumber=hex(block),logIndex=hex(index))
    return {'address':{'hash':r['address']},'block_number':block,'block_hash':r['blockHash'],
            'transaction_hash':r['transactionHash'],'index':index,'topics':r['topics'], 'data':r['data']}


class Pages:
    def __init__(self,docs):self.docs=docs;self.calls=[]
    def page(self,contract,cursor):
        self.calls.append(cursor)
        return self.docs[len(self.calls)-1]


class ScanTests(unittest.TestCase):
    def test_two_pages_and_returned_cursor(self):
        cur={'block_number':300,'index':2,'items_count':50}
        ex=Pages([{'items':[explorer_row(300,2)],'next_page_params':cur},
                  {'items':[explorer_row(260),explorer_row(240)],'next_page_params':{'block_number':240,'index':3}}])
        logs,meta=B.scan_contract(ex,G.CONTROLLER,250,300,5)
        self.assertEqual(len(logs),2);self.assertEqual(ex.calls,[None,cur]);self.assertEqual(len(meta),2)
    def test_cap_is_not_complete(self):
        ex=Pages([{'items':[explorer_row(300)],'next_page_params':{'block_number':300,'index':3}}])
        with self.assertRaises(G.EvidenceError):B.scan_contract(ex,G.CONTROLLER,250,300,1)
    def test_repeated_cursor_rejected(self):
        doc={'items':[explorer_row(300)],'next_page_params':{'block_number':300,'index':3}}
        with self.assertRaises(G.EvidenceError):B.scan_contract(Pages([doc,doc]),G.CONTROLLER,250,300,3)
    def test_unknown_cursor_rejected(self):
        doc={'items':[explorer_row(300)],'next_page_params':{'url':'https://other.test'}}
        with self.assertRaises(G.EvidenceError):B.scan_contract(Pages([doc]),G.CONTROLLER,250,300,3)
    def test_forward_order_rejected(self):
        ex=Pages([{'items':[explorer_row(270),explorer_row(280)],'next_page_params':None}])
        with self.assertRaises(G.EvidenceError):B.scan_contract(ex,G.CONTROLLER,250,300,3)
    def test_empty_with_cursor_rejected(self):
        ex=Pages([{'items':[],'next_page_params':{'block_number':3}}])
        with self.assertRaises(G.EvidenceError):B.scan_contract(ex,G.CONTROLLER,250,300,3)
    def test_future_logs_excluded(self):
        ex=Pages([{'items':[explorer_row(301),explorer_row(290)],'next_page_params':None}])
        logs,_=B.scan_contract(ex,G.CONTROLLER,250,300,3)
        self.assertEqual([G.quantity(l['blockNumber']) for l in logs],[290])
    def test_duplicate_deduped(self):
        r=explorer_row();ex=Pages([{'items':[r,r],'next_page_params':None}])
        self.assertEqual(len(B.scan_contract(ex,G.CONTROLLER,250,300,3)[0]),1)
    def test_conflict_rejected(self):
        r=explorer_row();s=copy.deepcopy(r);s['data']='0x'+('00'*32)
        with self.assertRaises(G.EvidenceError):B.scan_contract(Pages([{'items':[r,s]}]),G.CONTROLLER,250,300,3)
    def test_wrong_emitter_rejected(self):
        with self.assertRaises(G.EvidenceError):B.raw_log(explorer_row(contract=G.ZERO),G.CONTROLLER)


class ReceiptReader:
    def __init__(self):
        self.log=B.raw_log(explorer_row(),G.CONTROLLER)
        self.h={'number':'0x100','hash':self.log['blockHash'],'timestamp':hex(TIME)}
        self.r={'transactionHash':self.log['transactionHash'],'blockNumber':'0x100',
                'blockHash':self.log['blockHash'],'status':'0x1','logs':[copy.deepcopy(self.log)]}
    def block(self,n):return self.h
    def receipt(self,tx):return self.r


class ReceiptTests(unittest.TestCase):
    def test_exact_receipt_match(self):
        r=ReceiptReader();stamp,proof=B.confirm_log(r,r.log,300)
        self.assertEqual(stamp,G.iso(TIME));self.assertEqual(len(proof),64)
    def test_content_mismatch(self):
        r=ReceiptReader();r.r['logs'][0]['data']='0x'
        with self.assertRaises(G.EvidenceError):B.confirm_log(r,r.log,300)
    def test_revert_rejected(self):
        r=ReceiptReader();r.r['status']='0x0'
        with self.assertRaises(G.EvidenceError):B.confirm_log(r,r.log,300)
    def test_nonfinal_rejected(self):
        r=ReceiptReader()
        with self.assertRaises(G.EvidenceError):B.confirm_log(r,r.log,200)
    def test_missing_event_rejected(self):
        r=ReceiptReader();r.r['logs']=[]
        with self.assertRaises(G.EvidenceError):B.confirm_log(r,r.log,300)
    def test_reorg_rejected(self):
        r=ReceiptReader();r.h['hash']='0x'+'d'*64
        with self.assertRaises(G.EvidenceError):B.confirm_log(r,r.log,300)


class AttributionTests(unittest.TestCase):
    def check(self,value=None,field=True):
        row=api_sale()
        if field:row['filled_via']=value
        event=M.normalize(row,G.iso(TIME))
        doc={'data':{'results':[row]}}; fp=M.digest(doc)
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/(fp+'.json')).write_text(json.dumps(doc))
            result=B.attribution([event],Path(d),{'source_manifests':[{'sha256':fp}]},G.iso(TIME))
        return result[event['key']]
    def test_null_venue_not_origin(self):
        v=self.check();self.assertEqual(v['order_origin'],'grails');self.assertEqual(v['fill_venue_basis'],'unknown')
        self.assertFalse(v['independent_venue_verified'])
    def test_filled_via_is_reported_not_chain_verified(self):
        v=self.check('grails');self.assertEqual(v['fill_venue_basis'],'authenticated_app_report')
        self.assertFalse(v['independent_venue_verified'])
    def test_missing_venue_unknown(self):self.assertIsNone(self.check(field=False)['filled_via'])
    def test_unknown_venue_rejected(self):
        with self.assertRaises(G.EvidenceError):self.check('fake')
    def test_missing_raw_source_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(G.EvidenceError):B.attribution([],Path(d),{'source_manifests':[{'sha256':'missing'}]},G.iso(TIME))
    def test_failed_run_preserves_lastgood(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'chain.json';f.write_text('{"sentinel":true}')
            self.assertEqual(B.main(['--data-dir',d,'--api-raw-dir',d]),1)
            self.assertEqual(json.loads(f.read_text()),{'sentinel':True})

if __name__=='__main__':unittest.main()
