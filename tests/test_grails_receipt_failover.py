import sys
from pathlib import Path
import types
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import grails_backfill as B
import grails_chain as G

class TestReceiptFailover(unittest.TestCase):
    def reader(self,values):
        r=B.ReceiptReader.__new__(B.ReceiptReader)
        r.receipts={};r.client=types.SimpleNamespace(rpcs=['one','two','three'])
        r.rpc=Mock(side_effect=values)
        return r
    @patch.object(B.time,'sleep')
    def test_null_is_retried_without_claiming_absence(self,sleep):
        r=self.reader([None,{'transactionHash':'x'}]);self.assertEqual(r.receipt('x'),{'transactionHash':'x'})
        self.assertEqual(r.rpc.call_count,2);self.assertEqual(r.client.rpcs[0],'two')
        self.assertEqual(r.receipt('x'),{'transactionHash':'x'});self.assertEqual(r.rpc.call_count,2)
    @patch.object(B.time,'sleep')
    def test_all_nulls_fail_closed(self,sleep):
        r=self.reader([None]*4)
        with self.assertRaises(G.EvidenceError):r.receipt('x')
        self.assertEqual(r.rpc.call_count,4);self.assertNotIn('x',r.receipts)
    @patch.object(B.time,'sleep')
    def test_old_null_cache_is_not_authoritative(self,sleep):
        r=self.reader([{'transactionHash':'x'}]);r.receipts['x']=None
        self.assertIsNotNone(r.receipt('x'))

if __name__=='__main__':unittest.main()
