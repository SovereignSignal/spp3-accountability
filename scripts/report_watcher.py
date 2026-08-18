#!/usr/bin/env python3
"""report_watcher.py — watch the ENS forum for SPP3 quarterly reports.

Each provider keeps one forum thread whose opening post lists its reports.
This polls those threads, records which quarters have actually been filed,
and nudges the committee privately before a due date passes. No credentials
are involved: Discourse serves topic JSON publicly, so this runs unattended
like the stream monitor.

Two thread shapes exist in the wild and both are parsed:

    * [Q3 2024](url)                 SPP2 providers, quarter IS the link
    * **Q4 2026** — [Pending](url)   the SPP3 template, link is a status

Namespace and Unruggable are SPP2 returning and may keep their old format,
so guessing one shape would silently miss their reports.

Tone follows the plan: the committee hears first and the Chair speaks to the
provider. Nothing here posts anywhere public.
"""
import argparse
import calendar
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acct_config as C  # noqa: E402

DISCOURSE = "https://discuss.ens.domains"
UA = "spp3-accountability (+https://github.com/SovereignSignal/spp3-accountability)"

QUARTER_RE = re.compile(r"\bQ([1-4])\s+(\d{4})\b", re.I)
BOLD_RE = re.compile(r"\*\*\s*(Q[1-4]\s+\d{4})\s*\*\*", re.I)
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
LIST_RE = re.compile(r"^\s*[*\-+]\s+")
HEADING_RE = re.compile(r"^\s*#{1,6}\s")
REPORTS_HEADING_RE = re.compile(r"^\s*#{1,6}\s.*\breports\b", re.I)
# A real reference is /t/<slug>/<id> or /t/<id>, optionally /<post_no>.
REAL_TOPIC_RE = re.compile(r"/t/(?:[^/\s]+/)?\d+(?:/\d+)?")

STATE_PATH = C.LOG_DIR / "reports-state.json"
DUE_SOON_DAYS = 7


# ---------------------------------------------------------------- parsing

def normalise_quarter(text):
    """'Q4 2026' -> '2026Q4'. Returns None for anything ambiguous.

    Combined labels like 'Q1/Q2 2024' are deliberately rejected rather than
    guessed at: a report covering two quarters cannot be credited to one.
    """
    if not text:
        return None
    if re.search(r"Q[1-4]\s*/\s*Q[1-4]", text, re.I):
        return None
    m = QUARTER_RE.search(text)
    if not m:
        return None
    return "%sQ%s" % (m.group(2), m.group(1))


def is_filed(url):
    """True when the link points at a real topic rather than a placeholder."""
    if not url:
        return False
    return bool(REAL_TOPIC_RE.search(url))


def parse_reports(markdown):
    """Extract the Reports list from a thread's opening post.

    Collects from EVERY heading matching "reports", not the first. EthID's
    real thread opens with "## Previous Reports" (prose, no links) and only
    then "## Reports" with the actual list; locking onto the first match found
    nothing at all. Sections without links contribute nothing, so scanning all
    of them is safe and survives whatever headings a provider invents.
    """
    lines = (markdown or "").splitlines()
    spans = []
    for i, line in enumerate(lines):
        if REPORTS_HEADING_RE.match(line):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if HEADING_RE.match(lines[j]):
                    end = j
                    break
            spans.append((i + 1, end))
    if not spans:
        return []

    out = []
    seen = set()
    for start, end in spans:
      for line in lines[start:end]:
          if not LIST_RE.match(line):
              continue
          links = LINK_RE.findall(line)
          if not links:
              continue
          bold = BOLD_RE.search(line)
          quarter = normalise_quarter(bold.group(1) if bold else links[0][0])
          if not quarter:
              continue
          if quarter in seen:
              continue
          seen.add(quarter)
          label, url = links[0]
          out.append({"quarter": quarter, "url": url, "label": label.strip(),
                      "filed": is_filed(url)})
    return out


# ---------------------------------------------------------------- fetching

def _topic_json_url(ref):
    """Accept a full topic URL, a /t/... path, or a bare topic id."""
    ref = str(ref).strip()
    if not ref:
        return None
    m = re.search(r"/t/(?:[^/\s]+/)?(\d+)", ref)
    tid = m.group(1) if m else (ref if ref.isdigit() else None)
    return "%s/t/%s.json?include_raw=true" % (DISCOURSE, tid) if tid else None


def fetch_thread(ref, opener=None):
    """Opening-post markdown for a thread, or None if unreachable."""
    url = _topic_json_url(ref)
    if not url:
        return None
    opener = opener or _default_opener
    try:
        doc = json.loads(opener(url))
        posts = doc.get("post_stream", {}).get("posts") or []
        return posts[0].get("raw") if posts else None
    except Exception:                       # noqa: BLE001 - a dead thread is data
        return None


def _default_opener(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


# ---------------------------------------------------------------- state

def _due_ts(datestr):
    return calendar.timegm(time.strptime(datestr, "%Y-%m-%d"))


def build_state(providers, commitments, now, fetcher=None):
    """Per provider, what is filed and what is owed."""
    fetcher = fetcher or fetch_thread
    quarters = commitments.get("quarters", [])
    cohort = [p for p in providers.get("providers", [])
              if p.get("cohort") == "spp3"]

    out = []
    for p in cohort:
        prov = commitments.get("providers", {}).get(p["slug"], {})
        thread = prov.get("report_thread")
        markdown = fetcher(thread) if thread else None
        found = {r["quarter"]: r for r in parse_reports(markdown or "") if r["filed"]}

        rows = []
        for q in quarters:
            qk, due = q["quarter"], q["report_due"]
            days = (_due_ts(due) - now) / 86400.0
            if qk in found:
                state = "filed"
            elif days < 0:
                state = "overdue"
            elif days <= DUE_SOON_DAYS:
                state = "due soon"
            else:
                state = "upcoming"
            rows.append({"quarter": qk, "due": due, "days": days, "state": state,
                         "url": found.get(qk, {}).get("url")})
        out.append({"slug": p["slug"], "name": p["name"], "thread": thread,
                    "thread_reachable": markdown is not None if thread else None,
                    "quarters": rows})
    return out


def nudges(state):
    """What the committee should act on. Private channel only."""
    out = []
    for prov in state:
        if prov["thread"] and prov["thread_reachable"] is False:
            out.append({"severity": "warning", "code": "thread_unreachable",
                        "subject": prov["name"],
                        "detail": "report thread could not be fetched"})
        for q in prov["quarters"]:
            if q["state"] == "overdue":
                out.append({"severity": "critical", "code": "report_overdue",
                            "subject": prov["name"],
                            "detail": "%s report was due %s, %d days ago"
                                      % (q["quarter"], q["due"], abs(round(q["days"])))})
            elif q["state"] == "due soon":
                out.append({"severity": "warning", "code": "report_due_soon",
                            "subject": prov["name"],
                            "detail": "%s report due %s, in %d days"
                                      % (q["quarter"], q["due"], round(q["days"]))})
    out.sort(key=lambda n: 0 if n["severity"] == "critical" else 1)
    return out


def record_filed(commitments, state):
    """Fold newly seen reports into commitments.json. Returns what changed."""
    added = []
    for prov in state:
        entry = commitments.setdefault("providers", {}).setdefault(prov["slug"], {})
        existing = {r.get("quarter") for r in (entry.get("reports") or [])}
        for q in prov["quarters"]:
            if q["state"] == "filed" and q["quarter"] not in existing:
                entry.setdefault("reports", []).append(
                    {"quarter": q["quarter"], "url": q["url"]})
                added.append("%s %s" % (prov["name"], q["quarter"]))
    return added


# ---------------------------------------------------------------- alerting

def _load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def alert_decision(nudge_list, previous_keys):
    """Speak when the picture changes, not every run.

    A daily repeat of "still overdue" trains the committee to ignore the
    channel, which is the failure this whole system exists to avoid.
    """
    keys = sorted("%s|%s|%s" % (n["code"], n["subject"], n["detail"][:40])
                  for n in nudge_list)
    if keys and keys != sorted(previous_keys or []):
        return {"send": True, "kind": "alert", "keys": keys}
    if not keys and previous_keys:
        return {"send": True, "kind": "recovery", "keys": []}
    return {"send": False, "kind": "none", "keys": keys}


def _format(nudge_list):
    lines = ["<b>[SPP3 REPORTS]</b>"]
    for n in nudge_list:
        lines.append("%s <b>%s</b>: %s" % (
            "[!]" if n["severity"] == "critical" else "[~]",
            n["subject"], n["detail"]))
    lines.append("Private to the committee. The Chair speaks to the provider "
                 "before anything appears anywhere public.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="SPP3 quarterly report watcher")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args(argv)

    import validate
    providers = validate.load_providers(C.PROVIDERS_PATH)
    cpath = C.DATA_DIR / "commitments.json"
    commitments = json.loads(cpath.read_text())

    now = time.time()
    state = build_state(providers, commitments, now)
    problems = nudges(state)

    watched = sum(1 for p in state if p["thread"])
    print("providers=%d watched=%d nudges=%d"
          % (len(state), watched, len(problems)))
    for p in state:
        if not p["thread"]:
            print("  %-12s no report thread configured yet" % p["slug"])
            continue
        filed = [q["quarter"] for q in p["quarters"] if q["state"] == "filed"]
        print("  %-12s thread=%s filed=%s"
              % (p["slug"], "ok" if p["thread_reachable"] else "UNREACHABLE",
                 ",".join(filed) or "none"))
    for n in problems:
        print("  [%s] %s: %s" % (n["severity"], n["subject"], n["detail"]))

    if args.dry_run:
        print(json.dumps(state, indent=2, sort_keys=True))
        return 0

    added = record_filed(commitments, state)
    if added:
        cpath.write_text(json.dumps(commitments, indent=2) + "\n")
        print("  recorded: %s" % ", ".join(added))
        import stream_monitor
        stream_monitor.publish(cpath, "chore(reports): %s" % ", ".join(added))

    C.LOG_DIR.mkdir(parents=True, exist_ok=True)
    prev = _load_state()
    decision = alert_decision(problems, prev.get("keys"))
    if decision["send"] and not args.no_notify:
        from notify import send as tg_send
        tg_send(_format(problems) if decision["kind"] == "alert"
                else "<b>[SPP3 REPORTS]</b> all outstanding reports resolved.")
    STATE_PATH.write_text(json.dumps({"keys": decision["keys"]}) + "\n")

    return 2 if any(n["severity"] == "critical" for n in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
