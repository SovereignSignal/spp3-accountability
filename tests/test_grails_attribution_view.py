import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'site'))
import grails_chain_view as V

class TestAttributionView(unittest.TestCase):
    def doc(self):
        return {'settlements':{'records':50},'venue_attribution':{'fill_venue_reports':0,'unknown_fill_venues':50,
                'scope':'Grails-origin API records only; foreign-origin fills through Grails may be absent.',
                'methodology_source':'https://github.com/grailsmarket/backend/blob/abc/example.sql'}}
    def test_venue_is_not_inferred_from_origin(self):
        h=V.venue_section(self.doc())
        for s in ('Order origin and fill venue','Fill venue unknown','authenticated app report',
                  'Neither field','foreign-origin fills','not evidence that Grails was unused'):
            self.assertIn(s,h)
    def test_missing_venue_is_not_zero(self):
        d=self.doc();d['venue_attribution'].pop('fill_venue_reports')
        self.assertIn('unavailable',V.venue_section(d))
    def test_bad_source_url_and_html_not_injected(self):
        d=self.doc();d['venue_attribution'].update(methodology_source='javascript:alert(1)',scope='<script>oops</script>')
        h=V.venue_section(d)
        self.assertNotIn('href="javascript:',h);self.assertNotIn('<script>',h)
    def test_old_feed_still_renders(self):self.assertEqual(V.venue_section({}),'')
    def test_index_completeness_is_qualified(self):
        d=self.doc();d.update(observed_at='2026-10-03T20:00:00Z',source_snapshot_id='x',registrar={
            'retrieval':'blockscout_keyset_rpc_receipts','completeness_basis':'complete indexed-source traversal'})
        h=V.chain_section({'grails_chain':d,'grails':{'snapshot_id':'x'}})
        self.assertIn('Coverage basis:',h);self.assertIn('The index supplies completeness',h)
    def test_future_snapshot_not_current(self):
        d=self.doc();d['observed_at']='2026-10-03T20:00:00Z'
        self.assertIn('Snapshot is stale.',V.chain_section({'grails_chain':d,'now':1}))

if __name__=='__main__':unittest.main()
