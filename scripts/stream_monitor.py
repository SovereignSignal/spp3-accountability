#!/usr/bin/env python3
"""stream_monitor.py — daily on-chain health check of the SPP3 streams.

Compares wei/s integers against the ratified rates. Dollar figures in the
output are display-only and never drive a verdict: the master stream's
nominal $3.21M/yr is $3,207,871 once integer truncation is applied, so a
dollar comparison would alert every day on a healthy system.
"""
import json
import subprocess
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


def reconcile_net_flow(providers, reader):
    """Detect streams we do not know about.

    Checking known receivers only proves the streams we know about are correct;
    it is blind to a receiver nobody recorded. The pod's NET flowrate must equal
    master inflow minus the sum of known outflows. Any difference is an
    unaccounted stream. One extra call, and no event indexer or subgraph.
    """
    pod = providers["pod"]
    master_in = providers["master_stream_wei_s"]
    known_out = sum(C.expected_rate(p["award_usd"]) for p in providers["providers"])
    expected_net = master_in - known_out
    actual_net = reader.account_flowrate(C.USDCX, pod)
    unaccounted = actual_net - expected_net
    return {
        "pod_net_wei_s": actual_net,
        "expected_net_wei_s": expected_net,
        "unaccounted_wei_s": unaccounted,
        "master_in_wei_s": master_in,
        "known_out_wei_s": known_out,
        "ok": unaccounted == 0,
    }


def check_runway(providers, reader):
    """Days of funding before the master stream cannot be sustained.

    USDCx is what the stream actually spends; USDC at the timelock is what
    autowrap converts into USDCx. Both count, scaled to 18dp. This is the
    signal that failed in SPP2: streams liquidated because wrapping stopped.
    """
    pod = providers["pod"]
    master_in = providers["master_stream_wei_s"]
    pod_usdcx = reader.balance_of(C.USDCX, pod)
    tl_usdcx = reader.balance_of(C.USDCX, C.TIMELOCK)
    tl_usdc = reader.balance_of(C.USDC, C.TIMELOCK)

    available = tl_usdcx + tl_usdc * 10**12   # USDC is 6dp, USDCx is 18dp
    daily_burn = master_in * 86400
    combined_days = available / daily_burn if daily_burn else float("inf")

    if combined_days < C.RUNWAY_CRITICAL_DAYS:
        level = "critical"
    elif combined_days < C.RUNWAY_WARNING_DAYS:
        level = "warning"
    else:
        level = "ok"

    return {
        "pod_usdcx": pod_usdcx,
        "timelock_usdcx": tl_usdcx,
        "timelock_usdc": tl_usdc,
        "daily_burn_wei": daily_burn,
        "combined_days": combined_days,
        "level": level,
        "ok": level == "ok",
    }


def build_status(providers, reader, block_number, checked_at):
    streams = check_streams(providers, reader)
    retired = check_retired(providers, reader)
    net = reconcile_net_flow(providers, reader)
    runway = check_runway(providers, reader)

    critical = (any(not s["ok"] for s in streams)
                or any(not r["ok"] for r in retired)
                or not net["ok"]
                or runway["level"] == "critical")
    overall = "critical" if critical else (
        "warning" if runway["level"] == "warning" else "healthy")

    return {
        "_generated": True,
        "_source": "stream_monitor.py",
        "checked_at": checked_at,
        "block_number": block_number,
        "overall": overall,
        "streams": streams,
        "retired": retired,
        "net_flow": net,
        "runway": runway,
    }


def findings(status):
    """Actionable problems, most severe first. Empty means healthy."""
    out = []
    for s in status["streams"]:
        if s["ok"]:
            continue
        out.append({
            "severity": "critical",
            "code": s["state"],
            "subject": s["slug"],
            "detail": "%s: expected %d wei/s (~$%.0f/yr), on-chain %d wei/s"
                      % (s["name"], s["expected_wei_s"], s["expected_usd_yr"],
                         s["actual_wei_s"]),
        })
    for r in status["retired"]:
        if r["ok"]:
            continue
        out.append({
            "severity": "critical",
            "code": "unexpected_active",
            "subject": r["slug"],
            "detail": "%s is retired but still streaming %d wei/s"
                      % (r["name"], r["actual_wei_s"]),
        })
    if not status["net_flow"]["ok"]:
        out.append({
            "severity": "critical",
            "code": "unaccounted_flow",
            "subject": "pod",
            "detail": "pod net flow differs from known streams by %d wei/s; "
                      "an unrecorded stream exists"
                      % status["net_flow"]["unaccounted_wei_s"],
        })
    if status["runway"]["level"] != "ok":
        out.append({
            "severity": "critical" if status["runway"]["level"] == "critical"
                        else "warning",
            "code": "low_runway",
            "subject": "timelock",
            "detail": "%.1f days of combined USDCx+USDC runway remaining"
                      % status["runway"]["combined_days"],
        })
    out.sort(key=lambda f: 0 if f["severity"] == "critical" else 1)
    return out


def _comparable(status):
    """Status minus the fields that change on every run regardless of health."""
    d = dict(status)
    d.pop("checked_at", None)
    d.pop("block_number", None)
    return json.dumps(d, sort_keys=True)


def write_status(status, path):
    """Write status.json. Returns True only if the meaningful content changed,
    so an unchanged daily run produces no commit and the git history stays a
    record of real state changes rather than timestamp noise."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if _comparable(json.loads(path.read_text())) == _comparable(status):
                return False
        except (ValueError, OSError):
            pass
    path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    return True


def publish(path, message):
    """Commit and push the status file. Returns False if there was nothing
    to commit. Never raises on a push failure: the alert is the product, and
    a git outage must not suppress it."""
    root = str(C.REPO_ROOT)
    subprocess.run(["git", "-C", root, "add", str(path)], check=True)
    r = subprocess.run(["git", "-C", root, "diff", "--cached", "--quiet"])
    if r.returncode == 0:
        return False
    subprocess.run(["git", "-C", root, "commit", "-m", message], check=True)
    try:
        subprocess.run(["git", "-C", root, "push", "-q", "origin", "HEAD"],
                       check=True)
    except subprocess.CalledProcessError as e:
        print("WARN: push failed (%s); commit is local" % e, file=sys.stderr)
    return True
