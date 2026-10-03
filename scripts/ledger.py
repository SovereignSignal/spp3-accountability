#!/usr/bin/env python3
"""Build SPP3's public on-chain financial ledger from Ethereum.

Two independent event sets are reconciled:
- USDC Transfer logs involving the Stream Management Pod.
- Superfluid CFA FlowUpdated logs for USDCx streams sent by the pod.

The stream history starts at the SPP3 epoch. Rates at that exact epoch are the
ratified on-chain rates in providers.json; every subsequent change is taken
from Ethereum events and reconciled against a live CFA read.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acct_config as C
import chain

START_BLOCK = 25650000
CHUNK = 9000
CFA_V1 = "0x6EeE6060f715257b970700bc2656De21dEdF074C"
TRANSFER_TOPIC = "0x" + chain.keccak256(b"Transfer(address,address,uint256)").hex()
FLOW_UPDATED_TOPIC = "0x" + chain.keccak256(
    b"FlowUpdated(address,address,address,int96,int256,int256,bytes)").hex()


def _topic_address(addr):
    return "0x" + addr.lower().replace("0x", "").rjust(64, "0")


def _address(topic):
    return "0x" + topic[-40:]


def _signed_word(word):
    value = int(word, 16)
    return value - (1 << 256) if value >= 1 << 255 else value


def _fetch_token_transfers(client, from_block, to_block):
    pod = _topic_address(C.STREAM_POD)
    found = {}
    for start in range(from_block, to_block + 1, CHUNK):
        end = min(start + CHUNK - 1, to_block)
        for topics in ([TRANSFER_TOPIC, pod], [TRANSFER_TOPIC, None, pod]):
            for log in client.event_logs(C.USDC, topics, start, end):
                found[(log["transactionHash"], log["logIndex"])] = log
    return sorted(found.values(),
                  key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16)))


def _fetch_flow_events(client, from_block, to_block):
    topics = [FLOW_UPDATED_TOPIC, _topic_address(C.USDCX),
              _topic_address(C.STREAM_POD), None]
    out = []
    for start in range(from_block, to_block + 1, CHUNK):
        end = min(start + CHUNK - 1, to_block)
        out.extend(client.event_logs(CFA_V1, topics, start, end))
    return sorted(out,
                  key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16)))


def _classify_transfer(event, commitments):
    market = commitments.get("marketplace_award") or {}
    nomentum = (market.get("recipient") or
                "0xF8DD51A64942aAC80340a71fC22AF8d41591cE82").lower()
    if event["direction"] == "in" and event["from"].lower() == C.TIMELOCK.lower():
        return "treasury funding"
    if event["direction"] == "out" and event["to"].lower() == nomentum:
        return "marketplace payment"
    return "unclassified"


def _custody(client, commitments, latest, ts_cache):
    logs = _fetch_token_transfers(client, START_BLOCK, latest)
    events = []
    for log in logs:
        block = int(log["blockNumber"], 16)
        if block not in ts_cache:
            ts_cache[block] = client.block_timestamp(block)
        frm, to = _address(log["topics"][1]), _address(log["topics"][2])
        direction = "out" if frm.lower() == C.STREAM_POD.lower() else "in"
        event = {
            "block_number": block,
            "timestamp": datetime.fromtimestamp(ts_cache[block], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tx_hash": log["transactionHash"],
            "log_index": int(log["logIndex"], 16),
            "token": "USDC",
            "amount": int(log["data"], 16) / 10**6,
            "direction": direction,
            "from": frm,
            "to": to,
        }
        event["classification"] = _classify_transfer(event, commitments)
        events.append(event)
    inflow = sum(e["amount"] for e in events if e["direction"] == "in")
    outflow = sum(e["amount"] for e in events if e["direction"] == "out")
    return events, {
        "usdc_in": inflow,
        "usdc_out": outflow,
        "net_usdc": inflow - outflow,
        "unclassified_events": sum(1 for e in events if e["classification"] == "unclassified"),
    }


def _stream_history(client, providers, latest, latest_ts, ts_cache):
    epoch = providers["spp3_stream_start"]
    start_block = client.block_at_or_after(epoch, latest=latest)
    logs = _fetch_flow_events(client, start_block, latest)
    known = {p["approved_wallet"].lower(): p for p in providers["providers"]}
    retired = {p["approved_wallet"].lower(): p for p in providers.get("retired", [])}
    by_receiver = {k: [] for k in known}
    retired_events = []
    unknown = []

    for log in logs:
        block = int(log["blockNumber"], 16)
        if block not in ts_cache:
            ts_cache[block] = client.block_timestamp(block)
        receiver = _address(log["topics"][3])
        data = log["data"].replace("0x", "")
        flow_rate = _signed_word(data[0:64])
        event = {
            "block_number": block,
            "timestamp_unix": ts_cache[block],
            "timestamp": datetime.fromtimestamp(ts_cache[block], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tx_hash": log["transactionHash"],
            "log_index": int(log["logIndex"], 16),
            "receiver": receiver,
            "flow_rate_wei_s": flow_rate,
        }
        key = receiver.lower()
        if key in by_receiver:
            by_receiver[key].append(event)
        elif key in retired:
            event["retired_slug"] = retired[key]["slug"]
            retired_events.append(event)
        else:
            unknown.append(event)

    streams = []
    for key, p in known.items():
        rate = C.expected_rate(p["award_usd"])
        cursor = epoch
        delivered = 0
        changes = []
        for e in by_receiver[key]:
            ts = max(e["timestamp_unix"], epoch)
            if ts > cursor:
                delivered += rate * (ts - cursor)
            rate = e["flow_rate_wei_s"]
            cursor = ts
            changes.append(e)
        if latest_ts > cursor:
            delivered += rate * (latest_ts - cursor)
        live_rate = client.flowrate(C.USDCX, C.STREAM_POD, p["approved_wallet"])
        streams.append({
            "slug": p["slug"],
            "name": p["name"],
            "cohort": p["cohort"],
            "receiver": p["approved_wallet"],
            "epoch_rate_wei_s": C.expected_rate(p["award_usd"]),
            "final_event_rate_wei_s": rate,
            "live_rate_wei_s": live_rate,
            "reconciled": rate == live_rate,
            "delivered_wei": delivered,
            "delivered_usd": delivered / 10**18,
            "changes": changes,
        })

    return {
        "epoch": epoch,
        "epoch_block": start_block,
        "through_timestamp": latest_ts,
        "streams": streams,
        "retired_flow_events": retired_events,
        "unknown_flow_events": unknown,
        "all_reconciled": all(s["reconciled"] for s in streams),
    }


def _financials(providers, commitments, custody_events, custody_summary, history):
    by_slug = {s["slug"]: s for s in history["streams"]}
    awards = []
    cohort_authorized = cohort_delivered = 0.0
    for p in providers["providers"]:
        if p.get("cohort") != "spp3":
            continue
        delivered = by_slug[p["slug"]]["delivered_usd"]
        authorized = float(p["award_usd"])
        cohort_authorized += authorized
        cohort_delivered += delivered
        awards.append({
            "slug": p["slug"], "name": p["name"], "type": "continuous stream",
            "authorized_usd": authorized, "delivered_usd": delivered,
            "remaining_term_commitment_usd": max(0.0, authorized - delivered),
            "current_annual_rate_usd": authorized,
        })

    market = commitments.get("marketplace_award") or {}
    market_authorized = float(market.get("award_usd", 0))
    market_delivered = sum(
        float(e.get("amount", 0)) for e in custody_events
        if e.get("classification") == "marketplace payment")
    held = max(0.0, market_authorized - market_delivered)
    gated = float(market.get("conditional_stream_usd", 0)) + float(market.get("performance_reserve_usd", 0))
    scheduled = max(0.0, float(market.get("upfront_total_usd", 0)) - market_delivered)
    awards.append({
        "slug": market.get("slug", "nomentum"), "name": market.get("name", "Nomentum Labs"),
        "type": "milestone-gated", "authorized_usd": market_authorized,
        "delivered_usd": market_delivered, "held_usd": held,
        "gated_usd": gated, "scheduled_installments_usd": scheduled,
        "current_annual_rate_usd": 0,
    })

    return {
        "authorized_usd": cohort_authorized + market_authorized,
        "delivered_usd": cohort_delivered + market_delivered,
        "cohort_authorized_usd": cohort_authorized,
        "cohort_delivered_usd": cohort_delivered,
        "currently_streaming_annual_usd": cohort_authorized,
        "marketplace_held_usd": held,
        "marketplace_gated_usd": gated,
        "marketplace_scheduled_usd": scheduled,
        "awards": awards,
    }


def build(client, commitments, providers, latest=None):
    latest = latest if latest is not None else client.block_number()
    ts_cache = {}
    latest_ts = client.block_timestamp(latest)
    ts_cache[latest] = latest_ts
    custody_events, custody_summary = _custody(client, commitments, latest, ts_cache)
    history = _stream_history(client, providers, latest, latest_ts, ts_cache)
    financials = _financials(providers, commitments, custody_events, custody_summary, history)
    return {
        "_generated": True,
        "_source": "ledger.py: Ethereum USDC transfers + Superfluid CFA FlowUpdated events",
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "through_block": latest,
        "through_timestamp": datetime.fromtimestamp(latest_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pod": C.STREAM_POD,
        "summary": custody_summary,
        "events": custody_events,
        "stream_history": history,
        "financials": financials,
    }


def comparable(text):
    try:
        d = json.loads(text)
        d.pop("checked_at", None)
        d.pop("through_block", None)
        d.pop("through_timestamp", None)
        return json.dumps(d, sort_keys=True)
    except (ValueError, TypeError):
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    commitments = json.loads((C.DATA_DIR / "commitments.json").read_text())
    providers = json.loads(C.PROVIDERS_PATH.read_text())
    client = chain.Chain()
    doc = build(client, commitments, providers)
    print(json.dumps({
        "through_block": doc["through_block"],
        "all_streams_reconciled": doc["stream_history"]["all_reconciled"],
        "unknown_flow_events": len(doc["stream_history"]["unknown_flow_events"]),
        "financials": doc["financials"],
    }, indent=2, sort_keys=True))
    if args.dry_run:
        return 0 if doc["stream_history"]["all_reconciled"] else 2

    C.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    old = C.LEDGER_PATH.read_text() if C.LEDGER_PATH.exists() else ""
    new = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    changed = comparable(old) != comparable(new)
    if changed:
        C.LEDGER_PATH.write_text(new)
        import stream_monitor
        stream_monitor.publish(C.LEDGER_PATH, "chore(ledger): update on-chain financial history")
    print("changed=%s via=%s" % (changed, client.last_endpoint))
    return 0 if doc["stream_history"]["all_reconciled"] else 2


if __name__ == "__main__":
    sys.exit(main())
