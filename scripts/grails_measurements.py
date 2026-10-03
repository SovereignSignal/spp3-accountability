#!/usr/bin/env python3
"""Read-only Grails observations. API evidence never authorizes a payment.

Re-scan the award observation window, deduplicate sales, retain disappeared
records, and publish only a complete, stable API traversal. Receipt checks
establish transaction success only, not amounts, NFT fills or attribution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.grails.app/api/v1/analytics/sales"
DOCS = "https://docs.grails.app/docs/api/analytics/sales"
START = "2026-09-10T00:00:00Z"  # Execution date, not the unavailable signing date.
ETH = "0x0000000000000000000000000000000000000000"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")
RAW_FIELDS = ("id", "ens_name_id", "name", "token_id", "source", "sale_date",
              "seller_address", "buyer_address", "sale_price_wei",
              "currency_address", "transaction_hash", "order_hash", "block_number")


class CollectionError(RuntimeError):
    """An incomplete or contradictory source must not replace last-good data."""


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def timestamp(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone required")
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise CollectionError("invalid timezone-aware timestamp") from exc


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def units(amount: int) -> str:
    return (f"{amount // 10**18}.{amount % 10**18:018d}").rstrip("0").rstrip(".")


def normalize(row: dict, observed_at: str) -> dict:
    if not isinstance(row, dict) or row.get("source") != "grails":
        raise CollectionError("sales source is missing or is not grails")
    for field in ("buyer_address", "seller_address", "currency_address"):
        if not isinstance(row.get(field), str) or not ADDRESS.fullmatch(row[field]):
            raise CollectionError("invalid " + field)
    if not isinstance(row.get("transaction_hash"), str) or not HASH.fullmatch(row["transaction_hash"]):
        raise CollectionError("missing or invalid transaction hash")
    raw_amount = str(row.get("sale_price_wei", ""))
    if not re.fullmatch(r"[0-9]+", raw_amount):
        raise CollectionError("amount must be a non-negative integer in base units")
    dt = timestamp(row.get("sale_date"))
    # Keep asset identity, not just transaction hash: one tx can contain many sales.
    asset = row.get("token_id") or row.get("ens_name_id") or row.get("name")
    if asset is None:
        raise CollectionError("sale has no asset identity")
    tx = row["transaction_hash"].lower()
    order = row.get("order_hash") or ""
    if order and (not isinstance(order, str) or not HASH.fullmatch(order)):
        raise CollectionError("invalid order hash")
    key = [1, tx, str(asset), order.lower(), row["buyer_address"].lower(),
           row["seller_address"].lower()]
    return {
        "key": digest(key), "chain_id": 1, "source": "grails",
        "asset_id": str(asset), "name": str(row.get("name") or ""),
        "transaction_hash": tx, "order_hash": order.lower(),
        "buyer": row["buyer_address"].lower(), "seller": row["seller_address"].lower(),
        "currency": row["currency_address"].lower(), "amount_raw": str(int(raw_amount)),
        "sale_at": iso(dt), "source_record": {k: row[k] for k in RAW_FIELDS if k in row},
        "source_record_sha256": digest(row), "first_observed_at": observed_at,
        "last_observed_at": observed_at, "present_in_latest_scan": True,
        "verification": "api_reported", "receipt": {"status": "not_checked"},
    }


def parse_page(doc: dict, page: int) -> tuple[list, dict]:
    if not isinstance(doc, dict) or doc.get("success") is not True:
        raise CollectionError("API did not return success=true")
    data = doc.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise CollectionError("missing sales results array")
    p = data.get("pagination")
    if not isinstance(p, dict) or p.get("page") != page or type(p.get("hasNext")) is not bool:
        raise CollectionError("missing or inconsistent pagination")
    if not data["results"] and p["hasNext"]:
        raise CollectionError("empty page says more pages exist")
    return data["results"], p


def collect(get_page: Callable[[int], dict], start: str, as_of: str,
            max_pages: int = 100) -> tuple[list, dict]:
    lower, upper = timestamp(start), timestamp(as_of)
    if lower >= upper or max_pages < 1:
        raise CollectionError("invalid observation window or page limit")
    found, seen_pages, manifest = {}, set(), []
    first_fingerprint, boundary, duplicates = None, None, 0
    last_date = None
    for page in range(1, max_pages + 1):
        doc = get_page(page)
        rows, pagination = parse_page(doc, page)
        fp = digest(rows)
        if fp in seen_pages and rows:
            raise CollectionError("repeated page; pagination did not advance")
        seen_pages.add(fp)
        if first_fingerprint is None:
            first_fingerprint = fp
        manifest.append({"page": page, "sha256": digest(doc), "records": len(rows),
                         "reported_total": pagination.get("total")})
        older = False
        for row in rows:
            event = normalize(row, as_of)
            date = timestamp(event["sale_at"])
            if last_date is not None and date > last_date:
                raise CollectionError("sales are not sorted newest-first")
            last_date = date
            if date < lower:
                older = True
                continue
            if date > upper:
                raise CollectionError("sale after collection cutoff; retry a fresh scan")
            key = event["key"]
            if key in found:
                if found[key]["source_record_sha256"] != event["source_record_sha256"]:
                    raise CollectionError("conflicting duplicate sale")
                duplicates += 1
            found[key] = event
        if older or not pagination["hasNext"]:
            boundary = "start_boundary_reached" if older else "feed_exhausted"
            break
    if boundary is None:
        raise CollectionError("page cap reached before full observation window")
    # Offset pagination can shift if sales arrive while the scan runs.
    head, _ = parse_page(get_page(1), 1)
    if digest(head) != first_fingerprint:
        raise CollectionError("feed changed during traversal; retry a fresh scan")
    return sorted(found.values(), key=lambda x: (x["sale_at"], x["key"])), {
        "status": "complete_api_window", "start": start, "end": as_of,
        "basis": "award execution date; signing date not established",
        "boundary": boundary, "pages": len(manifest), "duplicates": duplicates,
        "source_manifests": manifest,
        "independent_chain_coverage": False,
    }


def merge_evidence(previous: dict, current: list, as_of: str) -> list:
    old = {e["key"]: e for e in previous.get("events", [])}
    combined = {key: dict(e, present_in_latest_scan=False) for key, e in old.items()}
    for e in current:
        prior = old.get(e["key"])
        if prior:
            e["first_observed_at"] = prior["first_observed_at"]
            e["receipt"] = prior.get("receipt", {"status": "not_checked"})
            e["revisions"] = list(prior.get("revisions", []))
            if prior["source_record_sha256"] != e["source_record_sha256"]:
                e["revisions"].append({"at": as_of, "prior_source_record": prior["source_record"],
                                       "prior_sha256": prior["source_record_sha256"]})
        combined[e["key"]] = e
    return sorted(combined.values(), key=lambda x: (x["sale_at"], x["key"]))


def check_receipts(events: list, reader: object, as_of: str, limit: int) -> None:
    """Bounded tx-success checks. Does not verify sale amount or Grails attribution."""
    checked = {}
    for e in reversed(events):
        if not e["present_in_latest_scan"]:
            continue
        tx = e["transaction_hash"]
        if tx in checked:
            e["receipt"] = checked[tx]
            continue
        if len(checked) >= limit:
            break
        try:
            r = reader._rpc("eth_getTransactionReceipt", [tx])
            if r is None:
                result = {"status": "pending"}
            elif r.get("transactionHash", "").lower() != tx:
                result = {"status": "unavailable", "reason": "receipt hash mismatch"}
            else:
                status = int(r.get("status", "0x0"), 16)
                result = {"status": "success" if status == 1 else "reverted",
                          "block_number": int(r["blockNumber"], 16),
                          "block_hash": r.get("blockHash"),
                          "sale_amount_verified": False, "attribution_verified": False}
        except Exception:
            # Never include RPC URLs/keys or raw transport errors in public records.
            result = {"status": "unavailable"}
        result["checked_at"] = as_of
        e["receipt"] = checked[tx] = result


def measure(events: list, start: str, end: str) -> dict:
    rows = [e for e in events if e["present_in_latest_scan"]
            and timestamp(start) <= timestamp(e["sale_at"]) < timestamp(end)]
    buyers, participants, totals, excluded = set(), set(), Counter(), Counter()
    order_counts = Counter((e["transaction_hash"], e["order_hash"]) for e in rows if e["order_hash"])
    for e in rows:
        flags = []
        if e["buyer"] == e["seller"]:
            flags.append("self_trade")
        if not int(e["amount_raw"]):
            flags.append("zero_value")
        if e["buyer"] == ETH or e["seller"] == ETH:
            flags.append("zero_address")
        if e["receipt"].get("status") in ("pending", "reverted"):
            flags.append("receipt_not_successful")
        if e["currency"] not in (ETH, WETH):
            flags.append("currency_not_eth_or_weth")
        # Bundles need allocation proof before a row price can safely be summed.
        if e["order_hash"] and order_counts[(e["transaction_hash"], e["order_hash"])] > 1:
            flags.append("bundle_allocation_unverified")
        e["candidate_exclusions"] = flags
        excluded.update(flags)
        if flags:
            continue
        buyers.add(e["buyer"])
        participants.update((e["buyer"], e["seller"]))
        totals[e["currency"]] += int(e["amount_raw"])
    return {
        "start": start, "end_exclusive": end, "api_sales": len(rows),
        "candidate_secondary_buyers": len(buyers), "candidate_sale_participants": len(participants),
        "native_eth_volume": units(totals[ETH]), "weth_volume": units(totals[WETH]),
        "exclusions": dict(excluded),
        "receipt_statuses": dict(Counter(e["receipt"].get("status", "not_checked") for e in rows)),
        "gate_eligible": False,
    }


def build_snapshot(events: list, coverage: dict, as_of: str) -> tuple[dict, dict]:
    observed = measure(events, coverage["start"], as_of)
    q1_start, q1_end = "2027-01-01T00:00:00Z", "2027-04-01T00:00:00Z"
    q1 = None if timestamp(as_of) <= timestamp(q1_start) else measure(
        events, q1_start, min(as_of, q1_end))
    warnings = [
        "API source attribution has not been independently verified from settlement logs.",
        "Receipt success verifies a transaction only, not the sale, amount or attribution.",
        "Candidate wallets cover secondary-sale buyers only; registrations and renewals are not collected.",
        "Self/zero-value exclusions are sanity filters, not a committee-approved anti-wash policy.",
        "Minimum-value rules, wallet roles and term-end window require committee approval.",
        "Execution date is an observation boundary, not the Award Notice signing date or revenue baseline.",
    ]
    snapshot_id = digest({"at": as_of, "events": events, "coverage": coverage})
    summary = {
        "schema_version": 1, "snapshot_id": snapshot_id, "observed_at": as_of,
        "source": {"api": API, "documentation": DOCS, "filter": "source=grails"},
        "coverage": coverage, "observed": observed,
        "removed_upstream_records": sum(not e["present_in_latest_scan"] for e in events),
        "gate_status": "observation_only", "committee_approved": False,
        "q1_2027": {"status": "not_started" if q1 is None else "partial_sources",
                    "target_wallets": 150, "candidate_observations": q1,
                    "verified_wallets": None, "passed": None},
        "secondary_volume_gate": {"target_eth": 250, "filtered_volume_eth": None, "passed": None},
        "revenue": {"status": "not_collected", "current": None},
        "warnings": warnings, "recent_sales": list(reversed(events[-10:])),
        "raw_archive": {"workflow": "grails measurements", "retention_days": 90,
                        "note": "Canonical evidence persists in Git. Full source pages are workflow artifacts."},
    }
    evidence = {"schema_version": 1, "snapshot_id": snapshot_id, "observed_at": as_of,
                "events": events}
    return summary, evidence


class SourceClient:
    def __init__(self, raw_dir: Path | None = None):
        self.raw_dir = raw_dir

    def page(self, page: int) -> dict:
        url = API + "?" + urllib.parse.urlencode({
            "period": "all", "source": "grails", "sortBy": "date", "sortOrder": "desc",
            "page": page, "limit": 100})
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "SPP3Accountability/1.0",
                                                          "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=25) as response:
                    raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise CollectionError("source page exceeds size limit")
                doc = json.loads(raw)
                if self.raw_dir:
                    self.raw_dir.mkdir(parents=True, exist_ok=True)
                    (self.raw_dir / (digest(doc) + ".json")).write_bytes(raw)
                return doc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 2:
                    raise CollectionError("Grails API unavailable after retries") from exc
                time.sleep(2 ** attempt)
            except ValueError as exc:
                raise CollectionError("Grails API response was not JSON") from exc
        raise CollectionError("API request failed")


def atomic_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, default=ROOT / "data" / "grails")
    p.add_argument("--raw-dir", type=Path)
    p.add_argument("--max-pages", type=int, default=100)
    p.add_argument("--receipt-limit", type=int, default=5)
    p.add_argument("--start", default=START)
    args = p.parse_args(argv)
    try:
        prior_path = args.output_dir / "sales.json"
        previous = json.loads(prior_path.read_text()) if prior_path.exists() else {}
        if previous and (previous.get("schema_version") != 1 or not isinstance(previous.get("events"), list)):
            raise CollectionError("invalid prior snapshot")
        source = SourceClient(args.raw_dir)
        # Retry a moving feed once; never silently truncate or publish partial pages.
        for attempt in range(2):
            as_of = iso(datetime.now(timezone.utc))
            try:
                rows, coverage = collect(source.page, args.start, as_of, args.max_pages)
                break
            except CollectionError as exc:
                if attempt == 0 and ("cutoff" in str(exc) or "changed during" in str(exc)):
                    continue
                raise
        events = merge_evidence(previous, rows, as_of)
        if args.receipt_limit > 0:
            import chain
            check_receipts(events, chain.Chain(), as_of, args.receipt_limit)
        summary, evidence = build_snapshot(events, coverage, as_of)
        # No Git operations here. Workflow commits both files together after tests.
        atomic_json(args.output_dir / "sales.json", evidence)
        atomic_json(args.output_dir / "observations.json", summary)
        print(json.dumps({"coverage": {k: v for k, v in coverage.items() if k != "source_manifests"},
                          "observed": summary["observed"], "q1_2027": summary["q1_2027"],
                          "snapshot_id": summary["snapshot_id"]}, indent=2))
        return 0
    except (CollectionError, OSError, ValueError) as exc:
        print("Grails collection failed; last-good data unchanged: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
