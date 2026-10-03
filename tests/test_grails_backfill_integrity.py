import copy
import unittest
from unittest.mock import patch
from test_grails_backfill import ReceiptReader, explorer_row
from test_grails_chain import TIME
import grails_chain as G
import grails_backfill as B

class BackfillReader(ReceiptReader):
    def block(self,n):
        return {'number':hex(n),'hash':self.log['blockHash'],'timestamp':hex(TIME+n)}

class BackfillExplorer:
    def __init__(self):self.calls=[]
    def get(self,path):return {'hash':ReceiptReader().log['blockHash']}
    def page(self,contract,cursor):
        self.calls.append((contract,cursor))
        return {'items':[explorer_row()] if contract==G.CONTROLLER else [],'next_page_params':None}

class BackfillTests(unittest.TestCase):
    def run_scan(self,previous=None,reader=None,explorer=None,target=300):
        reader=reader or BackfillReader();explorer=explorer or BackfillExplorer()
        with patch.object(G,'first_block',return_value=250):
            return B.backfill(reader,explorer,previous,reader.block(target),10,G.iso(TIME))
    def test_complete_receipt_validated_backfill(self):
        d=self.run_scan();self.assertEqual(d['through_block'],300);self.assertEqual(d['coverage'],'current_known_contracts')
        self.assertEqual(len(d['events']),1);self.assertTrue(d['events'][0]['receipt_event_verified'])
        self.assertIn('indexed-source',d['completeness_basis'])
    def test_legacy_checkpoint_is_rescanned(self):
        d=self.run_scan({'schema_version':1,'events':[],'through_block':299})
        self.assertTrue(d['legacy_checkpoint_rescanned']);self.assertEqual(d['scan_from_block'],250)
        self.assertEqual(len(d['events']),1)
    def test_resume_rechecks_overlap_without_double_counting(self):
        a=self.run_scan();b=self.run_scan(a,target=310)
        self.assertEqual(len(b['events']),1);self.assertEqual(b['through_block'],310)
        self.assertFalse(b['legacy_checkpoint_rescanned'])
    def test_bad_previous_hash_fails_and_does_not_mutate(self):
        a=self.run_scan();a['through_block_hash']='0x'+'e'*64;before=copy.deepcopy(a)
        with self.assertRaises(G.EvidenceError):self.run_scan(a,target=310)
        self.assertEqual(a,before)
    def test_index_lag_fails_closed(self):
        ex=BackfillExplorer();ex.get=lambda path:{'hash':'0x'+'e'*64}
        with self.assertRaises(G.EvidenceError):self.run_scan(explorer=ex)
    def test_missing_index_records_cannot_claim_receipt_proof(self):
        ex=BackfillExplorer();ex.page=lambda a,c:{'items':[explorer_row(index=999)],'next_page_params':None}
        with self.assertRaises(G.EvidenceError):self.run_scan(explorer=ex)

if __name__=='__main__':unittest.main()
