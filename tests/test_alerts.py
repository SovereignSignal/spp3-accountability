import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import stream_monitor as M

CRIT = [{"severity": "critical", "code": "rate_mismatch",
         "subject": "namespace", "detail": "..."}]


class TestAlertDecision(unittest.TestCase):
    def test_first_problem_sends(self):
        d = M.alert_decision(CRIT, previous_keys=[])
        self.assertTrue(d["send"])
        self.assertEqual(d["kind"], "alert")

    def test_repeat_problem_is_suppressed(self):
        d = M.alert_decision(CRIT, previous_keys=M._finding_keys(CRIT))
        self.assertFalse(d["send"])

    def test_new_problem_is_not_hidden_by_existing_problem(self):
        newer = CRIT + [{"severity": "critical", "code": "stopped",
                         "subject": "goldsky", "detail": "..."}]
        d = M.alert_decision(newer, previous_keys=M._finding_keys(CRIT))
        self.assertTrue(d["send"])
        self.assertEqual(d["kind"], "alert")
        self.assertEqual(len(d["keys"]), 2)

    def test_recovery_sends_once(self):
        d = M.alert_decision([], previous_keys=M._finding_keys(CRIT))
        self.assertTrue(d["send"])
        self.assertEqual(d["kind"], "recovery")
        self.assertEqual(d["keys"], [])

    def test_healthy_with_no_prior_problem_is_silent(self):
        d = M.alert_decision([], previous_keys=[])
        self.assertFalse(d["send"])
        self.assertEqual(d["kind"], "none")


if __name__ == "__main__":
    unittest.main()
