import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import grails_seed as S
import grails_chain as G

class TestSeed(unittest.TestCase):
    def test_existing_v2_checkpoint_is_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'dest.json';dest.write_text('{"schema_version":2,"sentinel":true}')
            self.assertEqual(S.seed(Path(d)/'missing',dest),'existing_indexed_checkpoint')
            self.assertEqual(json.loads(dest.read_text())['sentinel'],True)
    def test_missing_artifact_falls_back_without_replacing_legacy(self):
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'dest.json';dest.write_text('{"schema_version":1}')
            self.assertEqual(S.seed(Path(d)/'missing',dest),'artifact_unavailable_full_scan_required')
            self.assertEqual(json.loads(dest.read_text())['schema_version'],1)
    def test_wrong_digest_fails_and_preserves_destination(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source.json';source.write_text('{}')
            dest=Path(d)/'dest.json';dest.write_text('{"schema_version":1}')
            with self.assertRaises(G.EvidenceError):S.seed(source,dest)
            self.assertEqual(json.loads(dest.read_text())['schema_version'],1)
    def fixture(self):
        return {'schema_version':2,'referrer':G.REFERRER,'start':G.START,
                'coverage':'current_known_contracts','through_block':300,'target_finalized_block':300,
                'contracts':[G.CONTROLLER,G.HELPER],'events':[{'receipt_event_verified':True}]}
    def test_validated_seed_still_requires_live_recheck(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source.json';source.write_text(json.dumps(self.fixture()));dest=Path(d)/'dest.json'
            with patch.object(S,'VALIDATED_SHA256',hashlib.sha256(source.read_bytes()).hexdigest()):
                self.assertEqual(S.seed(source,dest),'validated_bootstrap_installed_live_recheck_required')
            self.assertEqual(json.loads(dest.read_text()),self.fixture())
    def test_incomplete_or_unverified_documents_rejected(self):
        for key,value in [('coverage','backfill_in_progress'),('events',[{'receipt_event_verified':False}]),('referrer','bad')]:
            with self.subTest(key=key),tempfile.TemporaryDirectory() as d:
                doc=self.fixture();doc[key]=value;source=Path(d)/'source.json';source.write_text(json.dumps(doc));dest=Path(d)/'dest.json'
                with patch.object(S,'VALIDATED_SHA256',hashlib.sha256(source.read_bytes()).hexdigest()):
                    with self.assertRaises(G.EvidenceError):S.seed(source,dest)
                self.assertFalse(dest.exists())

if __name__=='__main__':unittest.main()
