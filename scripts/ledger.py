#!/usr/bin/env python3
"""Build the public SPP3 on-chain custody ledger from Ethereum logs.

This is intentionally narrower than an explorer: it records every USDC transfer
into or out of the Stream Management Pod, then combines that custody history
with the separately monitored Superfluid state. Unknown movements stay unknown
instead of being guessed into a program category.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acct_config as C
import chain

START_BLOCK = 25650000
CHUNK = 25000
TRANSFER_TOPIC = "0x" + chain.keccak256(b"Transfer(address,address,uint256)").hex()


def _topic_address(addr):
    return "0x" + addr.lower().replace("0x", "").rjust(64, "0")


def _address(topic):
    return "0x" + topic[-40:]


def _fetch(client, from_block, to_block):
    pod = _topic_address(C.STREAM_POD)
    found = {}
    for start in range(from_block, to_block + 1, CHUNK):
        end = min(start + CHUNK - 1, to_block)
        # ERC-20 Transfer has indexed from and to. Read each direction and merge.
        for topics in ([TRANSFER_TOPIC, pod], [TRANSFER_TOPIC, None, pod]):
            for log in client.event_logs(C.USDC, topics, start, end):
                key = (log["transactionHash"], log["logIndex"])
                found[key] = log
    return sorted(found.values(),
                  key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16)))


def _classify(event, commitments):
    market = commitments.get("marketplace_award") or {}
    nomentum = (market.get("recipient") or
                "0xF8DD51A64942aAC80340a71fC22AF8d41591cE82").lower()
    if event["direction"] == "in" and event["from"].lower() == C.TIMELOCK.lower():
        return "treasury funding"
    if event["direction"] == "out" and event["to"].lower() == nomentum:
        return "marketplace payment"
    return "unclassified"


def build(client, commitments, latest=None):
    latest = latest if latest is not None else client.block_number()
    logs = _fetch(client, START_BLOCK, latest)
    ts_cache = {}
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
        event["classification"] = _classify(event, commitments)
        events.append(event)

    inflow = sum(e["amount"] for e in events if e["direction"] == "in")
    outflow = sum(e["amount"] for e in events if e["direction"] == "out")
    return {
        "_generated": True,
        "_source": "ledger.py: Ethereum mainnet USDC Transfer logs involving stream.mg.wg.ens.eth",
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "through_block": latest,
        "pod": C.STREAM_POD,
        "summary": {
            "usdc_in": inflow,
            "usdc_out": outflow,
            "net_usdc": inflow - outflow,
            "unclassified_events": sum(1 for e in events if e["classification"] == "unclassified"),
        },
        "events": events,
    }


def main(argv=None):
    commitments = json.loads((C.DATA_DIR / "commitments.json").read_text())
    client = chain.Chain()
    doc = build(client, commitments)
    C.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    old = C.LEDGER_PATH.read_text() if C.LEDGER_PATH.exists() else ""
    new = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    # checked_at changes every run; publish only when block-backed event content changes.
    def comparable(text):
        try:
            d = json.loads(text)
            d.pop("checked_at", None)
            d.pop("through_block", None)
            return json.dumps(d, sort_keys=True)
        except (ValueError, TypeError):
            return ""
    changed = comparable(old) != comparable(new)
    if changed:
        C.LEDGER_PATH.write_text(new)
    print("events=%d unclassified=%d changed=%s via=%s" % (
        len(doc["events"]), doc["summary"]["unclassified_events"], changed, client.last_endpoint))
    if changed:
        import stream_monitor
        stream_monitor.publish(C.LEDGER_PATH, "chore(ledger): update on-chain custody events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
