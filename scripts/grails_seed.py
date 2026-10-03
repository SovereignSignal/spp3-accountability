#!/usr/bin/env python3
"""One-time migration from the completed, receipt-validated PR backfill artifact.

Only the registrar checkpoint is reused. The collector still checks its canonical
block hash and scans the overlap/new finalized range before publishing anything.
An existing v2 checkpoint is never replaced; an expired artifact triggers a full scan.
"""
import argparse
import hashlib
import json
from pathlib import Path
import grails_chain as G

VALIDATED_SHA256 = '11847df617da0059b2fe0ec3e062c88c9cf9d6f9f7320463207b98b1407ecdc3'
VALIDATED_RUN = 37156753227


def seed(source: Path, destination: Path) -> str:
    previous = G.load(destination)
    if previous.get('schema_version') == 2:
        return 'existing_indexed_checkpoint'
    if not source.is_file():
        return 'artifact_unavailable_full_scan_required'
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != VALIDATED_SHA256:
        raise G.EvidenceError('bootstrap artifact digest mismatch')
    doc = json.loads(raw)
    if (doc.get('schema_version') != 2 or doc.get('referrer') != G.REFERRER
        or doc.get('start') != G.START or doc.get('coverage') != 'current_known_contracts'
        or doc.get('through_block') != doc.get('target_finalized_block')
        or doc.get('contracts') != [G.CONTROLLER, G.HELPER]
        or not isinstance(doc.get('events'), list)
        or not all(e.get('receipt_event_verified') is True for e in doc['events'])):
        raise G.EvidenceError('bootstrap artifact failed validation')
    G.save(destination, doc)
    return 'validated_bootstrap_installed_live_recheck_required'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    args = p.parse_args()
    print(seed(args.source, args.destination))


if __name__ == '__main__':
    main()
