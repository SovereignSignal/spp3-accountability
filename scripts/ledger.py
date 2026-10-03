#!/usr/bin/env python3
"""Build SPP3's event-sourced financial ledger from Ethereum.

The committed ledger is a checkpoint. Each run reads only blocks after that
checkpoint, applies new USDC Transfer and Superfluid FlowUpdated events, then
reconciles the resulting stream rates against fresh CFA reads.

Bootstrap is safe for the current SPP3 streams because CFA getFlowInfo exposes
lastUpdated. If a stream's lastUpdated is at or before the SPP3 epoch, its
current rate has not changed during the term, so rate × elapsed is exact for
the bootstrap interval. Any stream changed after the epoch without prior
history makes bootstrap fail closed.
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
CHUNK = 750
CFA_V1 = "0x6EeE6060f715257b970700bc2656De21dEdF074C"
TRANSFER_TOPIC = "0x" + chain.keccak256(b"Transfer(address,address,uint256)").hex()
FLOW_UPDATED_TOPIC = "0x" + chain.keccak256(
    b"FlowUpdated(address,address,address,int96,int256,int256,bytes)").hex()


class BootstrapUnsafe(RuntimeError):
    pass


def _topic_address(addr):
    return "0x" + addr.lower().replace("0x", "").rjust(64, "0")


def _address(topic):
    return "0x" + topic[-40:]


def _signed_word(word):
    value = int(word, 16)
    return value - (1 << 256) if value >= 1 << 255 else value


def _fetch_token_transfers(client, from_block, to_block):
    if from_block > to_block:
        return []
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
    if from_block > to_block:
        return []
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


def _decode_transfer(log, client, commitments, ts_cache):
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
    return event


def _custody(client, commitments, previous, latest, ts_cache):
    previous_events = list((previous or {}).get("events") or [])
    checkpoint = int((previous or {}).get("through_block") or (START_BLOCK - 1))
    new_logs = _fetch_token_transfers(client, max(START_BLOCK, checkpoint + 1), latest)
    new_events = [_decode_transfer(x, client, commitments, ts_cache) for x in new_logs]
    merged = {(e["tx_hash"], int(e["log_index"])): e for e in previous_events}
    for e in new_events:
        merged[(e["tx_hash"], int(e["log_index"]))] = e
    events = sorted(merged.values(), key=lambda e: (e["block_number"], e["log_index"]))
    inflow = sum(e["amount"] for e in events if e["direction"] == "in")
    outflow = sum(e["amount"] for e in events if e["direction"] == "out")
    return events, {
        "usdc_in": inflow,
        "usdc_out": outflow,
        "net_usdc": inflow - outflow,
        "unclassified_events": sum(1 for e in events if e["classification"] == "unclassified"),
    }


def _decode_flow(log, client, ts_cache):
    block = int(log["blockNumber"], 16)
    if block not in ts_cache:
        ts_cache[block] = client.block_timestamp(block)
    data = log["data"].replace("0x", "")
    return {
        "block_number": block,
        "timestamp_unix": ts_cache[block],
        "timestamp": datetime.fromtimestamp(ts_cache[block], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tx_hash": log["transactionHash"],
        "log_index": int(log["logIndex"], 16),
        "receiver": _address(log["topics"][3]),
        "flow_rate_wei_s": _signed_word(data[0:64]),
    }


def _bootstrap_history(client, providers, latest, latest_ts):
    epoch = providers["spp3_stream_start"]
    streams = []
    for p in providers["providers"]:
        info = client.flow_info(C.USDCX, C.STREAM_POD, p["approved_wallet"])
        if info["last_updated"] > epoch:
            raise BootstrapUnsafe(
                "%s changed after SPP3 epoch at %d; historical events required"
                % (p["slug"], info["last_updated"]))
        rate = info["flowrate"]
        delivered = rate * max(0, latest_ts - epoch)
        streams.append({
            "slug": p["slug"], "name": p["name"], "cohort": p["cohort"],
            "receiver": p["approved_wallet"],
            "epoch_rate_wei_s": C.expected_rate(p["award_usd"]),
            "current_rate_wei_s": rate,
            "live_rate_wei_s": rate,
            "last_updated": info["last_updated"],
            "reconciled": rate == C.expected_rate(p["award_usd"]),
            "delivered_wei": delivered,
            "delivered_usd": delivered / 10**18,
            "changes": [],
            "bootstrap_basis": "CFA lastUpdated <= SPP3 epoch; no in-term rate change",
        })
    return {
        "epoch": epoch,
        "through_block": latest,
        "through_timestamp": latest_ts,
        "streams": streams,
        "retired_flow_events": [],
        "unknown_flow_events": [],
        "all_reconciled": all(s["reconciled"] for s in streams),
        "bootstrap": True,
    }


def _advance_history(client, providers, previous_history, latest, latest_ts, ts_cache):
    checkpoint = int(previous_history["through_block"])
    prev_ts = int(previous_history["through_timestamp"])
    logs = _fetch_flow_events(client, checkpoint + 1, latest)
    events = [_decode_flow(x, client, ts_cache) for x in logs]
    by_slug = {s["slug"]: dict(s) for s in previous_history["streams"]}
    by_receiver = {s["receiver"].lower(): s["slug"] for s in previous_history["streams"]}
    retired = {p["approved_wallet"].lower(): p["slug"] for p in providers.get("retired", [])}
    unknown, retired_events = [], list(previous_history.get("retired_flow_events") or [])

    cursor = {slug: prev_ts for slug in by_slug}
    for event in events:
        key = event["receiver"].lower()
        slug = by_receiver.get(key)
        if slug:
            s = by_slug[slug]
            ts = max(prev_ts, event["timestamp_unix"])
            s["delivered_wei"] += s["current_rate_wei_s"] * max(0, ts - cursor[slug])
            s["current_rate_wei_s"] = event["flow_rate_wei_s"]
            s["changes"] = list(s.get("changes") or []) + [event]
            cursor[slug] = ts
        elif key in retired:
            event["retired_slug"] = retired[key]
            retired_events.append(event)
        else:
            unknown.append(event)

    for slug, s in by_slug.items():
        s["delivered_wei"] += s["current_rate_wei_s"] * max(0, latest_ts - cursor[slug])
        s["delivered_usd"] = s["delivered_wei"] / 10**18
        live = client.flowrate(C.USDCX, C.STREAM_POD, s["receiver"])
        s["live_rate_wei_s"] = live
        s["reconciled"] = s["current_rate_wei_s"] == live

    all_unknown = list(previous_history.get("unknown_flow_events") or []) + unknown
    streams = [by_slug[s["slug"]] for s in previous_history["streams"]]
    return {
        "epoch": previous_history["epoch"],
        "through_block": latest,
        "through_timestamp": latest_ts,
        "streams": streams,
        "retired_flow_events": retired_events,
        "unknown_flow_events": all_unknown,
        "all_reconciled": all(s["reconciled"] for s in streams),
        "bootstrap": False,
    }


def _stream_history(client, providers, previous, latest, latest_ts, ts_cache):
    prev = (previous or {}).get("stream_history") or {}
    if not prev.get("streams"):
        return _bootstrap_history(client, providers, latest, latest_ts)
    return _advance_history(client, providers, prev, latest, latest_ts, ts_cache)


def _financials(providers, commitments, custody_events, history):
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
    market_delivered = sum(float(e.get("amount", 0)) for e in custody_events
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


def build(client, commitments, providers, previous=None, latest=None):
    latest = latest if latest is not None else client.block_number()
    ts_cache = {}
    latest_ts = client.block_timestamp(latest)
    ts_cache[latest] = latest_ts
    custody_events, custody_summary = _custody(
        client, commitments, previous, latest, ts_cache)
    history = _stream_history(
        client, providers, previous, latest, latest_ts, ts_cache)
    financials = _financials(providers, commitments, custody_events, history)
    return {
        "_generated": True,
        "_source": "ledger.py: Ethereum USDC transfers + incremental Superfluid CFA events",
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
        return json.dumps(d, sort_keys=True)
    except (ValueError, TypeError):
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    commitments = json.loads((C.DATA_DIR / "commitments.json").read_text())
    providers = json.loads(C.PROVIDERS_PATH.read_text())
    old = C.LEDGER_PATH.read_text() if C.LEDGER_PATH.exists() else ""
    try:
        previous = json.loads(old) if old else {}
    except ValueError:
        previous = {}
    client = chain.Chain()
    doc = build(client, commitments, providers, previous=previous)
    print(json.dumps({
        "through_block": doc["through_block"],
        "all_streams_reconciled": doc["stream_history"]["all_reconciled"],
        "unknown_flow_events": len(doc["stream_history"]["unknown_flow_events"]),
        "financials": doc["financials"],
    }, indent=2, sort_keys=True))
    healthy = (doc["stream_history"]["all_reconciled"]
               and not doc["stream_history"]["unknown_flow_events"])
    if args.dry_run:
        return 0 if healthy else 2

    C.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    new = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    changed = comparable(old) != comparable(new)
    if changed:
        C.LEDGER_PATH.write_text(new)
        import stream_monitor
        stream_monitor.publish(C.LEDGER_PATH, "chore(ledger): update on-chain financial history")
    print("changed=%s via=%s" % (changed, client.last_endpoint))
    return 0 if healthy else 2


if __name__ == "__main__":
    sys.exit(main())
