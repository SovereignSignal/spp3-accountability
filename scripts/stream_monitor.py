#!/usr/bin/env python3
"""stream_monitor.py — daily on-chain health check of the SPP3 streams.

Compares wei/s integers against the ratified rates. Dollar figures in the
output are display-only and never drive a verdict: the master stream's
nominal $3.21M/yr is $3,207,871 once integer truncation is applied, so a
dollar comparison would alert every day on a healthy system.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acct_config as C


def _usd_yr(wei_s):
    return wei_s * C.SECONDS_PER_YEAR / 10**18


def _result(slug, name, cohort, address, expected, actual):
    if expected == actual:
        state = "ok"
    elif expected > 0 and actual == 0:
        state = "stopped"
    elif expected == 0 and actual != 0:
        state = "unexpected_active"
    else:
        state = "rate_mismatch"
    return {
        "slug": slug,
        "name": name,
        "cohort": cohort,
        "address": address,
        "expected_wei_s": expected,
        "actual_wei_s": actual,
        "expected_usd_yr": _usd_yr(expected),
        "actual_usd_yr": _usd_yr(actual),
        "state": state,
        "ok": state == "ok",
    }


def check_streams(providers, reader):
    """Compare every active provider stream against its ratified rate."""
    pod = providers["pod"]
    out = []
    for p in providers["providers"]:
        expected = C.expected_rate(p["award_usd"])
        actual = reader.flowrate(C.USDCX, pod, p["approved_wallet"])
        out.append(_result(p["slug"], p["name"], p["cohort"],
                           p["approved_wallet"], expected, actual))
    return out


def check_retired(providers, reader):
    """Confirm every retired SPP2 stream is stopped. A retired stream still
    running means the DAO is paying someone it stopped funding."""
    pod = providers["pod"]
    out = []
    for r in providers.get("retired") or []:
        actual = reader.flowrate(C.USDCX, pod, r["approved_wallet"])
        out.append(_result(r["slug"], r["name"], "retired",
                           r["approved_wallet"], 0, actual))
    return out
