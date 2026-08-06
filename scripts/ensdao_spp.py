#!/usr/bin/env python3
"""ensdao_spp.py — emit SPP3 entries for the community data layer.

github.com/ensdao/spp is the neutral dataset the ecosystem already reads:
Anticapture renders it, and SPP1 and SPP2 are in it while SPP3 is not. This
generates the SPP3 additions from data this repo already holds and verifies,
merges them into a checkout of that repo, and leaves a diff to open as a PR.

Generated rather than hand-written on purpose. When the per-provider forum
report threads exist, re-running adds each landed report to `reports` and
produces the next PR, so the upstream dataset tracks what this system observes
instead of drifting from it.

Usage:
    ensdao_spp.py --repo /path/to/ensdao-spp-checkout [--write]

Without --write it prints the planned changes and touches nothing.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acct_config as C  # noqa: E402

PROGRAM_KEY = "SPP3"

# Quarters SPP3 providers report on. Program Terms clause 6.3 requires a report
# within 30 days of each calendar quarter end, and streams opened 1 Aug 2026,
# two months into 2026/Q3, so Q3 is a real reporting quarter. SPP2 excluded its
# own start quarter because its streams opened 26 May, five days before that
# quarter closed. The term ends 31 Jul 2027, so 2027/Q2 (due 30 Jul 2027) is the
# last regular report; the July tail is covered by term reconciliation.
YEAR1_QUARTERS = ["2026/Q3", "2026/Q4", "2027/Q1", "2027/Q2"]

PROGRAM = {
    "name": "Service Provider Program Season 3",
    "year1Quarters": YEAR1_QUARTERS,
    # No year2Quarters: every SPP3 stream is a 12-month stream. The two-year
    # streams still running belong to SPP2 (blockful, eth.limo).
    "budget": 3_400_000,
    "startDate": "2026-08-01",
    "discussionUrl": "https://discuss.ens.domains/t/22086",
    "budgetProposal": {
        "id": "EP 6.42",
        "title": "SPP3: Program Authorization and Committee Model",
        "description": ("Authorized a third season, named the selection committee, "
                        "and capped the budget at 20% of trailing 12-month protocol "
                        "revenue, approximately $3.4M."),
        "date": "2026-05-10",
        "forumUrl": "https://discuss.ens.domains/t/22086",
        "snapshotUrl": ("https://snapshot.box/#/s:ens.eth/proposal/"
                        "0x3e523451340e987e9d3745fb33585bd7136bb6fa8519691ba7b04fa160d4ab7b"),
        "docsUrl": "https://docs.ens.domains/dao/proposals/6.42/",
    },
    "selectionProposal": {
        "id": "EP 6.49",
        "title": "SPP3: Cohort Recommendation",
        "description": ("Ratified a four-provider cohort totaling $1,690,000, "
                        "recommended by the SPP3 committee and executed on-chain. "
                        "A fifth selectee, EthID, declined."),
        "date": "2026-07-16",
        # SPP3 replaced ranked-choice Snapshot selection with a committee
        # recommendation ratified by an on-chain executable, so there is no
        # Snapshot vote for this one. The schema requires `snapshotUrl`, so the
        # canonical vote URL goes here; see the PR body.
        "snapshotUrl": ("https://vote.ensdao.org/#/onchain/"
                        "30153206728472299340257495645753485226870528642942223493225654414745632348879"),
        "forumUrl": "https://discuss.ens.domains/t/22237",
        "docsUrl": "https://docs.ens.domains/dao/proposals/6.49/",
    },
}

# Application URLs exactly as published in the ratified EP 6.49 cohort table.
PROPOSAL_URLS = {
    "namespace": "https://bronze-accused-porpoise-217.mypinata.cloud/ipfs/"
                 "bafybeiee73y2jwinefaszeng3frn47wmqini562qtcibzgnh42cnqiu2la",
    "goldsky": "https://bronze-accused-porpoise-217.mypinata.cloud/ipfs/"
               "bafybeidblcll7pt7s4jzlte6khv7twiqnckdoalanj2aoemd27afefhyou",
    "unruggable": "https://bronze-accused-porpoise-217.mypinata.cloud/ipfs/"
                  "bafybeiedajocrvgfnj3y6ec2mrqyt62d46uiyfb6xyqraxyyosvto5tmyq",
    "fluidkey": "https://bronze-accused-porpoise-217.mypinata.cloud/ipfs/"
                "bafkreiaplckhk72bu3wuxofgkbdoky7jb3o24kk7olbb6urfaquz2os4sm",
}

# Upstream uses its own display names and slugs; ours must match where a
# provider already exists, or a duplicate row gets created.
UPSTREAM_NAME = {
    "namespace": ("Namespace", "namespace", "https://namespace.ninja"),
    "goldsky": ("Goldsky", "goldsky", "https://goldsky.com"),
    "unruggable": ("Unruggable", "unruggable", "https://unruggable.com"),
    "fluidkey": ("Fluidkey", "fluidkey", "https://fluidkey.com"),
}

QUARTER_RE = re.compile(r"^\d{4}/Q[1-4]$")


def _our_cohort():
    """The four providers the chain says are funded, never the committee board."""
    doc = json.loads(C.PROVIDERS_PATH.read_text())
    return [p for p in doc["providers"] if p.get("cohort") == "spp3"]


def _reports_for(slug):
    """Landed quarterly reports, keyed by upstream quarter format.

    Empty until the per-provider forum threads exist and a report is observed.
    Re-running then produces the next PR.
    """
    path = C.DATA_DIR / "commitments.json"
    if not path.exists():
        return {}
    prov = json.loads(path.read_text()).get("providers", {}).get(slug, {})
    out = {}
    for r in prov.get("reports") or []:
        q, url = r.get("quarter"), r.get("url")
        if not q or not url:
            continue
        up = "%s/Q%s" % (q[:4], q[-1])           # 2026Q3 -> 2026/Q3
        if QUARTER_RE.match(up) and up in YEAR1_QUARTERS:
            out[up] = url
    return out


def build(upstream_programs, upstream_providers):
    """Return (programs, providers, changelog) with SPP3 merged in."""
    log = []
    programs = json.loads(json.dumps(upstream_programs))
    providers = json.loads(json.dumps(upstream_providers))

    if PROGRAM_KEY in programs["programs"]:
        log.append("programs.json: %s already present, leaving as-is" % PROGRAM_KEY)
    else:
        programs["programs"][PROGRAM_KEY] = PROGRAM
        log.append("programs.json: add %s (%s, budget $%s, streams from %s)" % (
            PROGRAM_KEY, PROGRAM["name"], "{:,}".format(PROGRAM["budget"]),
            PROGRAM["startDate"]))

    by_slug = {p["slug"]: p for p in providers["providers"]}
    for p in _our_cohort():
        slug = p["slug"]
        name, up_slug, website = UPSTREAM_NAME[slug]
        entry = {
            "proposalUrl": PROPOSAL_URLS[slug],
            "budget": p["award_usd"],
            "streamDuration": 1,
        }
        reports = _reports_for(slug)
        if up_slug in by_slug:
            row = by_slug[up_slug]
            if PROGRAM_KEY in row["programs"]:
                log.append("providers.json: %s already has %s" % (name, PROGRAM_KEY))
            else:
                row["programs"][PROGRAM_KEY] = entry
                log.append("providers.json: %s -> add %s ($%s)"
                           % (name, PROGRAM_KEY, "{:,}".format(p["award_usd"])))
            for q, url in reports.items():
                if q not in row["reports"]:
                    row["reports"][q] = url
                    log.append("providers.json: %s -> report %s" % (name, q))
        else:
            providers["providers"].append({
                "name": name,
                "slug": up_slug,
                "website": website,
                "programs": {PROGRAM_KEY: entry},
                "reports": reports,
            })
            log.append("providers.json: NEW provider %s ($%s)"
                       % (name, "{:,}".format(p["award_usd"])))

    # Upstream CI errors if providers are not sorted case-insensitively by name.
    providers["providers"].sort(key=lambda r: r["name"].lower())
    return programs, providers, log


def _dump(doc):
    """Serialise matching upstream's formatting.

    Upstream keeps quarter arrays on a single line. json.dumps explodes them,
    which rewrites SPP1 and SPP2 lines this change has no business touching and
    buries the real diff in noise. Collapse them back.
    """
    text = json.dumps(doc, indent=2) + "\n"
    return re.sub(
        r'\[\s*\n\s*((?:"\d{4}/Q[1-4]",?\s*\n\s*)+)\]',
        lambda m: "[" + ", ".join(
            q.strip().rstrip(",") for q in m.group(1).strip().splitlines()) + "]",
        text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="checkout of github.com/ensdao/spp")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo)
    programs = json.loads((repo / "programs.json").read_text())
    providers = json.loads((repo / "providers.json").read_text())

    new_programs, new_providers, log = build(programs, providers)

    print("MODE: %s\n" % ("WRITE" if args.write else "DRY RUN"))
    for line in log:
        print("  " + line)
    print("\n  %d programs, %d providers after merge" % (
        len(new_programs["programs"]), len(new_providers["providers"])))

    if args.write:
        (repo / "programs.json").write_text(_dump(new_programs))
        (repo / "providers.json").write_text(_dump(new_providers))
        print("\n  written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
