"""acct_config.py — SPP3 accountability constants.

Addresses verified on-chain 2026-08-04 at block 25,685,582. Source for the
address set: blockful's ep-6-49/podStreamSetup.t.sol at commit 04d349a.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PROVIDERS_PATH = DATA_DIR / "providers.json"
STATUS_PATH = DATA_DIR / "streams" / "status.json"
LOG_DIR = REPO_ROOT / ".cron-logs"

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDCX = "0x1BA8603DA702602A8657980e825A6DAa03Dee93a"
STREAM_POD = "0xB162Bf7A7fD64eF32b787719335d06B2780e31D1"
TIMELOCK = "0xFe89cc7aBB2C4183683ab71653C4cdc9B02D44b7"

SECONDS_PER_YEAR = 31_536_000

# Runway thresholds. SPP2's outage ran 33.7 days before full reactivation, so a
# threshold below that is too late to be actionable. 60 days gives lead time to
# raise a Safe transaction; 21 days is the last point at which that is routine.
RUNWAY_WARNING_DAYS = 60
RUNWAY_CRITICAL_DAYS = 21


def expected_rate(annual_usd):
    """Superfluid flowrate for an annual USD figure, matching the executable's
    integer truncation exactly. Compare these integers, never dollars."""
    return annual_usd * 10**18 // SECONDS_PER_YEAR
