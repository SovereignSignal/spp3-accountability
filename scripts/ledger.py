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
# Superfluid ConstantFlowAgreementV1 on Ethereum mainnet (UUPS proxy). The
# address differs per network; 0x6EeE...074C is Polygon's and holds no code
# here, which silently hid every FlowUpdated event until 2026-10-07.
CFA_V1 = "0x2844c1BBdA121E9E43105630b9C8310e5c72744b"
# Checkpoint only blocks this deep. Post-merge finality is ~64 blocks; a
# checkpoint at the head can lock in a log that a reorg later removes.
FINALITY_DEPTH = 64
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
    if event["direction"] == "out" and event["to"].lower() == C.USDCX.lower():
        return "USDCx upgrade"
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


def _configured_receivers(providers, commitments):
    """Every receiver the data files say the pod may stream to: providers.json
    (cohort, continuing SPP2, committee) plus the marketplace award recipient,
    whose $310k stream opens only after committee verification."""
    out = {}
    for p in providers["providers"]:
        out[p["approved_wallet"].lower()] = {
            "slug": p["slug"], "name": p["name"], "cohort": p.get("cohort"),
            "receiver": p["approved_wallet"],
            "epoch_rate_wei_s": C.expected_rate(p["award_usd"])}
    market = (commitments or {}).get("marketplace_award") or {}
    if market.get("recipient"):
        out[market["recipient"].lower()] = {
            "slug": market.get("slug", "nomentum"),
            "name": market.get("name", "Nomentum Labs"), "cohort": "marketplace",
            "receiver": market["recipient"], "epoch_rate_wei_s": 0}
    return out


def _advance_history(client, providers, previous_history, latest, latest_ts, ts_cache,
                     commitments=None):
    checkpoint = int(previous_history["through_block"])
    prev_ts = int(previous_history["through_timestamp"])
    logs = _fetch_flow_events(client, checkpoint + 1, latest)
    events = [_decode_flow(x, client, ts_cache) for x in logs]
    by_slug = {s["slug"]: dict(s) for s in previous_history["streams"]}
    order = [s["slug"] for s in previous_history["streams"]]
    by_receiver = {s["receiver"].lower(): s["slug"] for s in previous_history["streams"]}
    configured = _configured_receivers(providers, commitments)
    retired = {p["approved_wallet"].lower(): p["slug"] for p in providers.get("retired", [])}
    unknown, retired_events = [], list(previous_history.get("retired_flow_events") or [])
    cursor = {slug: prev_ts for slug in by_slug}

    def stream_for(key):
        """Slug for a receiver, opening a stream entry on the first event to a
        configured receiver. A configured slug already tracked under another
        address (a wallet change) stays unknown for a human to resolve."""
        if key in by_receiver:
            return by_receiver[key]
        cfg = configured.get(key)
        if not cfg or cfg["slug"] in by_slug:
            return None
        slug = cfg["slug"]
        by_slug[slug] = dict(cfg, current_rate_wei_s=0, live_rate_wei_s=0,
                             last_updated=None, reconciled=True, delivered_wei=0,
                             delivered_usd=0.0, changes=[],
                             bootstrap_basis="opened by FlowUpdated after the SPP3 epoch")
        order.append(slug)
        by_receiver[key] = slug
        cursor[slug] = None
        return slug

    def apply(slug, event, ts):
        s = by_slug[slug]
        if cursor[slug] is not None:
            s["delivered_wei"] += s["current_rate_wei_s"] * max(0, ts - cursor[slug])
        s["current_rate_wei_s"] = event["flow_rate_wei_s"]
        s["changes"] = list(s.get("changes") or []) + [event]
        cursor[slug] = ts

    # Events parked as unknown in earlier runs replay first, at their own
    # timestamps, once their receiver is configured.
    for event in sorted(previous_history.get("unknown_flow_events") or [],
                        key=lambda e: (e["block_number"], e["log_index"])):
        slug = stream_for(event["receiver"].lower())
        if slug:
            apply(slug, event, event["timestamp_unix"])
        else:
            unknown.append(event)

    for event in events:
        key = event["receiver"].lower()
        if key in retired and key not in by_receiver:
            event["retired_slug"] = retired[key]
            retired_events.append(event)
            continue
        slug = stream_for(key)
        if slug:
            apply(slug, event, max(prev_ts, event["timestamp_unix"]))
        else:
            unknown.append(event)

    for slug in order:
        s = by_slug[slug]
        s["delivered_wei"] += s["current_rate_wei_s"] * max(0, latest_ts - cursor[slug])
        s["delivered_usd"] = s["delivered_wei"] / 10**18
        live = client.flowrate(C.USDCX, C.STREAM_POD, s["receiver"])
        s["live_rate_wei_s"] = live
        s["reconciled"] = s["current_rate_wei_s"] == live

    all_unknown = unknown
    streams = [by_slug[slug] for slug in order]
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


def _stream_history(client, providers, previous, latest, latest_ts, ts_cache, commitments=None):
    prev = (previous or {}).get("stream_history") or {}
    if not prev.get("streams"):
        return _bootstrap_history(client, providers, latest, latest_ts)
    return _advance_history(client, providers, prev, latest, latest_ts, ts_cache, commitments)


def _financials(providers, commitments, custody_events, history):
    by_slug = {s["slug"]: s for s in history["streams"]}
    awards = []
    cohort_authorized = cohort_delivered = 0.0
    for p in providers["providers"]:
        if p.get("cohort") != "spp3":
            continue
        stream = by_slug.get(p["slug"])
        delivered = stream["delivered_usd"] if stream else 0.0
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
    market_slug = market.get("slug", "nomentum")
    market_authorized = float(market.get("award_usd", 0))
    payments = sum(float(e.get("amount", 0)) for e in custody_events
                   if e.get("classification") == "marketplace payment")
    # Once the conditional stream opens it leaves "gated" and delivers
    # continuously; until then the whole $310k sits behind its gate.
    market_stream = by_slug.get(market_slug)
    conditional = float(market.get("conditional_stream_usd", 0))
    stream_delivered = market_stream["delivered_usd"] if market_stream else 0.0
    stream_rate = market_stream["current_rate_wei_s"] if market_stream else 0
    market_delivered = payments + stream_delivered
    held = max(0.0, market_authorized - market_delivered)
    gated = float(market.get("performance_reserve_usd", 0)) + (0.0 if market_stream else conditional)
    streaming_remaining = max(0.0, conditional - stream_delivered) if market_stream else 0.0
    scheduled = max(0.0, float(market.get("upfront_total_usd", 0)) - payments)
    awards.append({
        "slug": market_slug, "name": market.get("name", "Nomentum Labs"),
        "type": "milestone-gated", "authorized_usd": market_authorized,
        "delivered_usd": market_delivered, "held_usd": held,
        "gated_usd": gated, "scheduled_installments_usd": scheduled,
        "stream_open": bool(market_stream), "stream_delivered_usd": stream_delivered,
        "stream_remaining_usd": streaming_remaining,
        "current_annual_rate_usd": stream_rate * C.SECONDS_PER_YEAR / 10**18,
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
        "marketplace_streaming_remaining_usd": streaming_remaining,
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
        client, providers, previous, latest, latest_ts, ts_cache, commitments)
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
    if client.code(CFA_V1) in (None, "", "0x"):
        print("ERROR: no contract at CFA_V1 %s; refusing to index flows" % CFA_V1,
              file=sys.stderr)
        return 2
    latest = client.block_number() - FINALITY_DEPTH
    through = int(previous.get("through_block") or 0)
    if latest <= through:
        print("no finalized blocks past checkpoint %d (finalized head %d)" % (through, latest))
        return 0
    doc = build(client, commitments, providers, previous=previous, latest=latest)
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

    if not healthy:
        # Never publish or checkpoint numbers that do not reconcile: the site
        # would show them as fact and the gap would never be rescanned.
        for s in doc["stream_history"]["streams"]:
            if not s["reconciled"]:
                print("UNRECONCILED %s: tracked %d wei/s, live %d wei/s"
                      % (s["slug"], s["current_rate_wei_s"], s["live_rate_wei_s"]),
                      file=sys.stderr)
        for e in doc["stream_history"]["unknown_flow_events"]:
            print("UNKNOWN RECEIVER %s at block %d (%d wei/s)"
                  % (e["receiver"], e["block_number"], e["flow_rate_wei_s"]), file=sys.stderr)
        print("ledger not written: history does not reconcile", file=sys.stderr)
        return 2

    C.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    new = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    changed = comparable(old) != comparable(new)
    published = None
    if changed:
        C.LEDGER_PATH.write_text(new)
        import stream_monitor
        published = stream_monitor.publish(C.LEDGER_PATH, "chore(ledger): update on-chain financial history")
    print("changed=%s published=%s via=%s" % (changed, published, client.last_endpoint))
    return 3 if published == "local" else 0


if __name__ == "__main__":
    sys.exit(main())
