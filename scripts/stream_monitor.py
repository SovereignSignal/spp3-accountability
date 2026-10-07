#!/usr/bin/env python3
"""stream_monitor.py — daily on-chain health check of the SPP3 streams.

Compares wei/s integers against the ratified rates. Dollar figures in the
output are display-only and never drive a verdict: the master stream's
nominal $3.21M/yr is $3,207,871 once integer truncation is applied, so a
dollar comparison would alert every day on a healthy system.
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acct_config as C


def _usd_yr(wei_s):
    return wei_s * C.SECONDS_PER_YEAR / 10**18


def _result(slug, name, cohort, address, expected, actual, since=0):
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
        "since": since,
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
        info = reader.flow_info(C.USDCX, pod, p["approved_wallet"])
        out.append(_result(p["slug"], p["name"], p["cohort"],
                           p["approved_wallet"], expected, info["flowrate"],
                           since=info["last_updated"]))
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
            "detail": "expected %d wei/s (~$%.0f/yr), on-chain %d wei/s"
                      % (s["expected_wei_s"], s["expected_usd_yr"],
                         s["actual_wei_s"]),
        })
    for r in status["retired"]:
        if r["ok"]:
            continue
        out.append({
            "severity": "critical",
            "code": "unexpected_active",
            "subject": r["slug"],
            "detail": "retired but still streaming %d wei/s"
                      % r["actual_wei_s"],
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


UNCHANGED, PUSHED, LOCAL = "unchanged", "pushed", "local"
PUSH_ATTEMPTS = 3
GIT_TIMEOUT = 120


def _git(root, *args, check=True):
    return subprocess.run(["git", "-C", root] + list(args), check=check,
                          capture_output=True, text=True, timeout=GIT_TIMEOUT)


def _sync(root, branch):
    """Rebase onto the remote branch and push, retrying when another writer
    lands in between. master has several writers (this VM's cron and the
    GitHub Actions refresh jobs), so pushing without pulling is rejected."""
    error = "no attempt made"
    for attempt in range(PUSH_ATTEMPTS):
        if attempt:
            time.sleep(5 * attempt)
        try:
            _git(root, "pull", "-q", "--rebase", "--autostash", "origin", branch)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            # Leave the repo as it was: local commit intact, no rebase in
            # progress, autostash restored. Harmless when no rebase started.
            try:
                _git(root, "rebase", "--abort", check=False)
            except subprocess.TimeoutExpired:
                pass
            return "pull --rebase failed: %s" % (getattr(e, "stderr", None) or str(e)).strip()
        try:
            _git(root, "push", "-q", "origin", "HEAD:" + branch)
            return None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            error = (getattr(e, "stderr", None) or str(e)).strip()
    return "push rejected %d times: %s" % (PUSH_ATTEMPTS, error)


def publish(path, message, root=None):
    """Commit one file and push it. Returns UNCHANGED when there is nothing
    to commit, PUSHED when the commit reached origin, LOCAL when it did not.
    The commit is scoped to path so anything else staged never rides along.
    Never raises on a sync failure (a broken local repo still raises on add or
    commit). A LOCAL result is announced on Telegram, because the site only
    shows what reaches origin."""
    root = str(root or C.REPO_ROOT)
    path = str(path)
    _git(root, "add", "--", path)
    if _git(root, "diff", "--cached", "--quiet", "--", path, check=False).returncode == 0:
        return UNCHANGED
    _git(root, "commit", "-q", "-m", message, "--", path)
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    error = "detached HEAD" if branch == "HEAD" else _sync(root, branch)
    if error is None:
        return PUSHED
    print("WARN: publish failed (%s); commit is local" % error, file=sys.stderr)
    from notify import send, escape_html
    send("<b>[SPP3] publish failed</b>\n%s committed locally, not pushed: %s"
         % (escape_html(Path(path).name), escape_html(error[:300])))
    return LOCAL


ALERT_FLAG = C.LOG_DIR / "stream-alert.flag"


def _finding_keys(finding_list):
    return sorted("%s|%s|%s" % (f.get("severity"), f.get("code"), f.get("subject"))
                  for f in finding_list)


def _load_alert_keys(path=ALERT_FLAG):
    """Read the distinct faults already announced. Legacy flag files upgrade
    on the next active fault rather than suppressing it."""
    try:
        doc = json.loads(Path(path).read_text())
        return doc.get("keys", []) if isinstance(doc, dict) else []
    except (OSError, ValueError):
        return []


def alert_decision(finding_list, previous_keys):
    """Speak when the distinct fault set changes, stay silent on repeats."""
    keys = _finding_keys(finding_list)
    previous = sorted(previous_keys or [])
    if keys and keys != previous:
        return {"send": True, "kind": "alert", "keys": keys}
    if not keys and previous:
        return {"send": True, "kind": "recovery", "keys": []}
    return {"send": False, "kind": "none", "keys": keys}

def _format_alert(status, finding_list):
    lines = ["<b>[SPP3 STREAMS] %s</b>" % status["overall"].upper()]
    for f in finding_list:
        lines.append("%s <b>%s</b>: %s"
                     % ("[!]" if f["severity"] == "critical" else "[~]",
                        f["subject"], f["detail"]))
    lines.append("Block %s, checked %s" % (status["block_number"],
                                           status["checked_at"]))
    return "\n".join(lines)


def _format_heartbeat(status):
    r = status["runway"]
    return ("<b>[SPP3 STREAMS] weekly heartbeat: %s</b>\n"
            "%d provider streams at ratified rates, %d retired streams stopped, "
            "no unaccounted flow.\nRunway %.0f days. Block %s."
            % (status["overall"], len(status["streams"]), len(status["retired"]),
               r["combined_days"], status["block_number"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="SPP3 stream health monitor")
    ap.add_argument("--dry-run", action="store_true",
                    help="check and print; write nothing, send nothing")
    ap.add_argument("--no-notify", action="store_true", help="suppress Telegram")
    ap.add_argument("--heartbeat", action="store_true",
                    help="send the weekly all-clear even when healthy")
    args = ap.parse_args(argv)

    import chain
    import validate
    from notify import send as tg_send

    providers = validate.load_providers(C.PROVIDERS_PATH)
    client = chain.Chain()
    block = client.block_number()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    status = build_status(providers, client, block, now)
    problems = findings(status)

    print("overall=%s block=%d findings=%d via=%s"
          % (status["overall"], block, len(problems), client.last_endpoint))
    for f in problems:
        print("  [%s] %s %s: %s"
              % (f["severity"], f["code"], f["subject"], f["detail"]))

    if args.dry_run:
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0

    C.LOG_DIR.mkdir(parents=True, exist_ok=True)
    changed = write_status(status, C.STATUS_PATH)

    # Alert before publishing: a git sync can retry for minutes, and a stream
    # fault must not wait on it.
    decision = alert_decision(problems, _load_alert_keys())
    if not args.no_notify:
        if decision["kind"] == "alert":
            tg_send(_format_alert(status, problems))
        elif decision["kind"] == "recovery":
            tg_send("<b>[SPP3 STREAMS] recovered</b>\nAll streams back at "
                    "ratified rates. Block %d." % block)
        elif args.heartbeat:
            tg_send(_format_heartbeat(status))
    ALERT_FLAG.write_text(json.dumps({"keys": decision["keys"]}) + "\n")

    if changed:
        publish(C.STATUS_PATH, "chore(streams): status %s at block %d"
                % (status["overall"], block))

    return 2 if status["overall"] == "critical" else 0


if __name__ == "__main__":
    sys.exit(main())
