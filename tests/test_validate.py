import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import validate


def minimal():
    return {
        "program": "spp3",
        "schema_version": 1,
        "pod": "0xB162Bf7A7fD64eF32b787719335d06B2780e31D1",
        "master_stream_wei_s": 101720934415475068,
        "spp3_stream_start": 1785561311,
        "providers": [{
            "slug": "namespace",
            "name": "Namespace",
            "award_usd": 500000,
            "categories": [1, 2],
            "approved_wallet": "0x168CAfEcFBE97dF85968Ea039CC11D10a9A44567",
            "cohort": "spp3",
            "recusals": [],
        }],
        "retired": [],
    }


class TestValidateProviders(unittest.TestCase):
    def test_minimal_document_is_valid(self):
        self.assertEqual(validate.validate_providers(minimal()), [])

    def test_missing_top_level_key_is_reported(self):
        doc = minimal()
        del doc["pod"]
        self.assertIn("missing top-level key: pod",
                      validate.validate_providers(doc))

    def test_bad_address_is_reported(self):
        doc = minimal()
        doc["providers"][0]["approved_wallet"] = "0x123"
        errs = validate.validate_providers(doc)
        self.assertTrue(any("approved_wallet" in e for e in errs), errs)

    def test_duplicate_slug_is_reported(self):
        doc = minimal()
        doc["providers"].append(copy.deepcopy(doc["providers"][0]))
        self.assertTrue(any("duplicate slug" in e
                            for e in validate.validate_providers(doc)))

    def test_duplicate_wallet_is_reported(self):
        doc = minimal()
        dup = copy.deepcopy(doc["providers"][0])
        dup["slug"] = "other"
        doc["providers"].append(dup)
        self.assertTrue(any("duplicate approved_wallet" in e
                            for e in validate.validate_providers(doc)))

    def test_negative_award_is_reported(self):
        doc = minimal()
        doc["providers"][0]["award_usd"] = -1
        self.assertTrue(any("award_usd" in e
                            for e in validate.validate_providers(doc)))

    def test_unknown_cohort_is_reported(self):
        doc = minimal()
        doc["providers"][0]["cohort"] = "spp9"
        self.assertTrue(any("cohort" in e
                            for e in validate.validate_providers(doc)))

    def test_retired_provider_in_active_list_is_reported(self):
        doc = minimal()
        doc["retired"] = [{"slug": "namespace", "name": "Namespace",
                           "approved_wallet":
                               "0x168CAfEcFBE97dF85968Ea039CC11D10a9A44567"}]
        self.assertTrue(any("both active and retired" in e
                            for e in validate.validate_providers(doc)))


class TestRealDataFile(unittest.TestCase):
    def test_shipped_providers_json_is_valid(self):
        doc = json.loads((ROOT / "data" / "providers.json").read_text())
        self.assertEqual(validate.validate_providers(doc), [])

    def test_shipped_file_has_four_spp3_providers(self):
        doc = json.loads((ROOT / "data" / "providers.json").read_text())
        spp3 = [p for p in doc["providers"] if p["cohort"] == "spp3"]
        self.assertEqual(len(spp3), 4)
        self.assertEqual(sum(p["award_usd"] for p in spp3), 1690000)


if __name__ == "__main__":
    unittest.main()
