import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import stream_monitor as M

CRIT = [{"severity": "critical", "code": "rate_mismatch",
         "subject": "namespace", "detail": "..."}]


class TestAlertDecision(unittest.TestCase):
    def test_first_problem_sends_and_sets_flag(self):
        d = M.alert_decision(CRIT, flag_exists=False)
        self.assertTrue(d["send"])
        self.assertEqual(d["kind"], "alert")
        self.assertTrue(d["set_flag"])

    def test_repeat_problem_is_suppressed(self):
        # A daily repeat of the same unresolved fault must not re-alert.
        # Alert fatigue is how SPP2's streams ran dead for 33 days.
        d = M.alert_decision(CRIT, flag_exists=True)
        self.assertFalse(d["send"])
        self.assertEqual(d["kind"], "none")

    def test_recovery_sends_once_and_clears_flag(self):
        d = M.alert_decision([], flag_exists=True)
        self.assertTrue(d["send"])
        self.assertEqual(d["kind"], "recovery")
        self.assertTrue(d["clear_flag"])

    def test_healthy_with_no_prior_problem_is_silent(self):
        d = M.alert_decision([], flag_exists=False)
        self.assertFalse(d["send"])
        self.assertEqual(d["kind"], "none")
        self.assertFalse(d["set_flag"])
        self.assertFalse(d["clear_flag"])


if __name__ == "__main__":
    unittest.main()
