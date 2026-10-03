#!/usr/bin/env python3
"""Complete indexed registrar windows, validate tagged logs against finalized RPC receipts.

Explorer pagination establishes source coverage. RPC receipts establish event
integrity. Neither proves website use, payer identity, or a payment-gate verdict.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

import grails_chain as G
import grails_measurements as M

EXPLORER = 'https://eth.blockscout.com/api/v2'
SOURCE_COMMIT = 'c354ce0ad11e9016b190dcebc9c0c72344fa35fd'
VENUE_SOURCE = ('https://github.com/grailsmarket/backend/blob/' + SOURCE_COMMIT +
                '/services/api/migrations/seq/0894_add_grails_fill_attribution.sql')


class Explorer:
    def __init__(self, raw_dir=None):
        self.raw_dir = raw_dir

    def get(self, path, params=None):
        url = EXPLORER + path + ('?' + urllib.parse.urlencode(params) if params else '')
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'SPP3Accountability/1.0', 'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=25) as response:
                    raw = response.read(12_000_001)
                if len(raw) > 12_000_000:
                    raise G.EvidenceError('explorer page too large')
                doc = json.loads(raw)
                if self.raw_dir:
                    self.raw_dir.mkdir(parents=True, exist_ok=True)
                    (self.raw_dir / (G.digest(doc) + '.json')).write_text(json.dumps({'url': url, 'result': doc}))
                return doc
            except (OSError, ValueError):
                if attempt == 3:
                    raise G.EvidenceError('explorer unavailable or invalid JSON') from None
                time.sleep(2 ** attempt)
        raise G.EvidenceError('explorer unavailable')

    def page(self, contract, cursor):
        return self.get('/addresses/' + contract + '/logs', cursor)


def raw_log(row, contract):
    if not isinstance(row, dict) or (row.get('address') or {}).get('hash', '').lower() != contract:
        raise G.EvidenceError('wrong explorer emitter')
    topics = row.get('topics')
    if not isinstance(topics, list):
        raise G.EvidenceError('missing explorer topics')
    topics = [t for t in topics if t is not None]
    if not topics or not all(isinstance(t, str) and G.HEX32.fullmatch(t) for t in topics):
        raise G.EvidenceError('invalid explorer topics')
    tx = row.get('transaction_hash', '').lower()
    block_hash = row.get('block_hash', '').lower()
    if not G.HEX32.fullmatch(tx) or not G.HEX32.fullmatch(block_hash):
        raise G.EvidenceError('invalid explorer identity')
    return {'address': contract, 'topics': [t.lower() for t in topics], 'data': row['data'],
            'blockNumber': hex(G.quantity(row['block_number'])), 'blockHash': block_hash,
            'transactionHash': tx, 'logIndex': hex(G.quantity(row['index'])), 'removed': False}


def scan_contract(explorer, contract, lower, target, page_budget):
    """Traverse exact returned keyset cursors. A cap/failure never means complete."""
    cursor = None
    cursors, identities, manifests = set(), {}, []
    prior_position = None
    for page in range(1, page_budget + 1):
        doc = explorer.page(contract, cursor)
        if not isinstance(doc, dict) or not isinstance(doc.get('items'), list):
            raise G.EvidenceError('invalid explorer page')
        rows = doc['items']
        older = False
        for row in rows:
            log = raw_log(row, contract)
            position = (G.quantity(log['blockNumber']), G.quantity(log['logIndex']))
            if prior_position is not None and position > prior_position:
                raise G.EvidenceError('explorer order moved forward')
            prior_position = position
            if position[0] < lower:
                older = True
                continue
            if position[0] > target:
                continue
            key = (log['transactionHash'], log['logIndex'])
            if key in identities and identities[key] != log:
                raise G.EvidenceError('conflicting explorer duplicate')
            identities[key] = log
        nxt = doc.get('next_page_params')
        manifests.append({'contract': contract, 'page': page, 'cursor': cursor,
                          'records': len(rows), 'sha256': G.digest(doc)})
        if older or nxt is None:
            return list(identities.values()), manifests
        if not rows or not isinstance(nxt, dict) or not nxt:
            raise G.EvidenceError('empty or inconsistent explorer cursor')
        if set(nxt) - {'index', 'block_number', 'items_count'}:
            raise G.EvidenceError('unexpected explorer cursor fields')
        if any(type(v) is not int or v < 0 for v in nxt.values()):
            raise G.EvidenceError('invalid explorer cursor values')
        fp = G.digest(nxt)
        if fp in cursors:
            raise G.EvidenceError('repeated explorer cursor')
        cursors.add(fp)
        cursor = nxt
    raise G.EvidenceError('explorer page budget reached before boundary')


def confirm_log(reader, log, target):
    """Exact emitter/topics/data/index comparison to canonical successful receipt."""
    block = G.quantity(log['blockNumber'])
    if block > target:
        raise G.EvidenceError('event is not finalized')
    header = reader.block(block)
    receipt = reader.receipt(log['transactionHash'])
    if (header['hash'].lower() != log['blockHash'].lower() or not receipt or
        receipt.get('transactionHash', '').lower() != log['transactionHash'] or
        G.quantity(receipt.get('status')) != 1 or G.quantity(receipt['blockNumber']) != block or
        receipt['blockHash'].lower() != header['hash'].lower()):
        raise G.EvidenceError('canonical receipt mismatch')
    matches = [r for r in receipt.get('logs', []) if G.quantity(r['logIndex']) == G.quantity(log['logIndex'])]
    if len(matches) != 1:
        raise G.EvidenceError('receipt event absent or ambiguous')
    actual = matches[0]
    if (actual.get('removed') or actual['address'].lower() != log['address'] or
        [x.lower() for x in actual['topics']] != log['topics'] or
        actual['data'].lower() != log['data'].lower()):
        raise G.EvidenceError('receipt event content mismatch')
    return G.iso(G.quantity(header['timestamp'])), G.digest(receipt)


def backfill(reader, explorer, previous, finalized, page_budget, as_of):
    target = G.quantity(finalized['number'])
    target_header = reader.block(target)
    if target_header['hash'].lower() != finalized['hash'].lower():
        raise G.EvidenceError('finalized head changed')
    indexed = explorer.get('/blocks/' + str(target))
    if indexed.get('hash', '').lower() != finalized['hash'].lower():
        raise G.EvidenceError('explorer has not indexed finalized target')
    wanted = int(datetime.fromisoformat(G.START.replace('Z', '+00:00')).timestamp())
    start = G.first_block(reader, wanted, target)
    migrated = bool(previous and previous.get('schema_version') != 2)
    prior_events = []
    lower = start
    if previous and previous.get('schema_version') == 2:
        if previous.get('referrer') != G.REFERRER or previous.get('start') != G.START or previous.get('start_block') != start:
            raise G.EvidenceError('incompatible indexed checkpoint')
        cursor = previous['through_block']
        if cursor > target or reader.block(cursor)['hash'].lower() != previous['through_block_hash'].lower():
            raise G.EvidenceError('indexed checkpoint no longer canonical')
        lower = max(start, cursor - 127)
        prior_events = [e for e in previous['events'] if e['block_number'] < lower]
    records = {e['key']: dict(e) for e in prior_events}
    manifests, sentinels = [], []
    for contract in (G.CONTROLLER, G.HELPER):
        logs, pages = scan_contract(explorer, contract, lower, target, page_budget)
        manifests.extend(pages)
        relevant = [l for l in logs if l['topics'][0] in (G.REGISTERED, G.RENEWED, G.REFERRED)]
        if relevant:
            stamp, proof = confirm_log(reader, relevant[0], target)
            sentinels.append({'contract': contract, 'transaction_hash': relevant[0]['transactionHash'],
                              'log_index': G.quantity(relevant[0]['logIndex']), 'receipt_sha256': proof})
        for log in relevant:
            event = G.decode_registrar(log)
            if event is None:
                continue
            stamp, proof = confirm_log(reader, log, target)
            event.update(timestamp=stamp, receipt_sha256=proof, receipt_event_verified=True)
            records[event['key']] = event
        print('registrar', contract, 'pages', len(pages), 'tagged_total', len(records), flush=True)
    collisions = Counter((e['transaction_hash'], e['labelhash'], e['kind']) for e in records.values())
    for e in records.values():
        e['ambiguous_duplicate'] = collisions[(e['transaction_hash'], e['labelhash'], e['kind'])] > 1
    return {'schema_version': 2, 'observed_at': as_of, 'start': G.START, 'start_block': start,
            'through_block': target, 'through_block_hash': finalized['hash'],
            'through_timestamp': G.iso(G.quantity(finalized['timestamp'])), 'target_finalized_block': target,
            'coverage': 'current_known_contracts', 'retrieval': 'blockscout_keyset_rpc_receipts',
            'completeness_basis': 'complete indexed-source traversal, not an independent exhaustive RPC scan',
            'legacy_checkpoint_rescanned': migrated, 'scan_from_block': lower,
            'referrer': G.REFERRER, 'mapping_source': 'https://dune.com/queries/8064446',
            'contracts': [G.CONTROLLER, G.HELPER], 'scan_manifests': manifests, 'source_sentinels': sentinels,
            'events': sorted(records.values(), key=lambda e: (e['block_number'], e['log_index'])),
            'limitations': ['Source completeness depends on the Blockscout index; matching events are independently receipt-validated.',
                           'Covers only the two configured ENSv1 contracts; no signing-date baseline.',
                           'Caller-supplied referrer tags do not prove website use or paying-user identity.']}


def attribution(events, raw_dir, coverage, as_of):
    """Read the same retained API pages; never infer venue from order origin."""
    source_rows = {}
    for item in coverage.get('source_manifests', []):
        path = raw_dir / (item['sha256'] + '.json')
        if not path.is_file():
            raise G.EvidenceError('attribution source page missing')
        doc = json.loads(path.read_text())
        if M.digest(doc) != item['sha256']:
            raise G.EvidenceError('attribution source hash mismatch')
        for row in doc['data']['results']:
            e = M.normalize(row, as_of)
            key = e['key']
            if key in source_rows and source_rows[key] != row:
                raise G.EvidenceError('attribution source conflict')
            source_rows[key] = row
    result = {}
    for e in events:
        if not e.get('present_in_latest_scan'):
            continue
        row = source_rows.get(e['key'])
        if row is None or M.digest(row) != e['source_record_sha256']:
            raise G.EvidenceError('attribution snapshot mismatch')
        value = row.get('filled_via')
        if value not in (None, 'grails'):
            raise G.EvidenceError('unknown filled_via value')
        result[e['key']] = {'order_origin': row['source'], 'order_origin_basis': 'api_reported',
                           'filled_via': value, 'fill_venue_basis': 'authenticated_app_report' if value else 'unknown',
                           'independent_venue_verified': False, 'source_record_sha256': e['source_record_sha256'],
                           'methodology_source': VENUE_SOURCE}
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path, default=G.ROOT / 'data/grails')
    p.add_argument('--api-raw-dir', type=Path, required=True)
    p.add_argument('--raw-dir', type=Path)
    p.add_argument('--max-pages', type=int, default=750)
    args = p.parse_args(argv)
    if args.max_pages < 1:
        p.error('page budget must be positive')
    try:
        source = G.load(args.data_dir / 'sales.json')
        observations = G.load(args.data_dir / 'observations.json')
        if not source.get('snapshot_id') or source['snapshot_id'] != observations.get('snapshot_id'):
            raise G.EvidenceError('API snapshots differ')
        reader = G.Reader(args.raw_dir)
        if G.quantity(reader.rpc('eth_chainId', [])) != 1:
            raise G.EvidenceError('wrong chain')
        finalized = reader.rpc('eth_getBlockByNumber', ['finalized', False])
        as_of = G.iso(int(datetime.now(timezone.utc).timestamp()))
        venues = attribution(source['events'], args.api_raw_dir, observations['coverage'], as_of)
        registrations = backfill(reader, Explorer(args.raw_dir), G.load(args.data_dir / 'registrar.json'),
                                 finalized, args.max_pages, as_of)
        results = []
        for sale in source['events']:
            if not sale.get('present_in_latest_scan'):
                continue
            tx = sale['transaction_hash']
            receipt = reader.receipt(tx)
            h = reader.block(G.quantity(receipt['blockNumber'])) if receipt else finalized
            st = G.verify_sale(sale, receipt, h, G.quantity(finalized['number']))
            st.update(checked_at=as_of, receipt_sha256=G.digest(receipt))
            results.append(dict(sale, settlement=st, venue=venues[sale['key']]))
        rs = G.registrar_summary(registrations)
        rs.update(retrieval=registrations['retrieval'], completeness_basis=registrations['completeness_basis'],
                  receipt_validated_events=len(registrations['events']))
        summary = {'schema_version': 2, 'observed_at': as_of, 'source_snapshot_id': source['snapshot_id'],
                   'finalized_block': G.quantity(finalized['number']), 'finalized_block_hash': finalized['hash'],
                   'settlements': G.settlement_summary(results), 'registrar': rs,
                   'venue_attribution': {'methodology_source': VENUE_SOURCE, 'order_origin': 'grails',
                      'order_origin_basis': 'api_reported', 'fill_venue_reports': sum(v['filled_via'] == 'grails' for v in venues.values()),
                      'unknown_fill_venues': sum(v['filled_via'] is None for v in venues.values()),
                      'independent_venue_verified': False,
                      'scope': 'Grails-origin API records only; foreign-origin fills through Grails may be absent.'},
                   'gate_status': 'observation_only', 'committee_approved': False,
                   'q1_2027': {'passed': None, 'verified_paying_wallets': None},
                   'warnings': ['Order origin and fill venue are different: source is API-reported; filled_via is an app report.',
                                'Complete indexed-source coverage is not independent proof of all website activity.',
                                'Registered owners and transaction initiators are not verified paying users.',
                                'Unknown signing baseline, USD methodology and committee policies block gate scoring.'],
                   'recent_results': sorted(results, key=lambda e: e['sale_at'], reverse=True)[:10]}
        summary['snapshot_id'] = G.digest(summary)
        evidence = {'schema_version': 2, 'observed_at': as_of, 'snapshot_id': summary['snapshot_id'],
                    'source_snapshot_id': source['snapshot_id'], 'results': results}
        G.save(args.data_dir / 'settlements.json', evidence)
        G.save(args.data_dir / 'registrar.json', registrations)
        G.save(args.data_dir / 'chain.json', summary)
        print(json.dumps({k:v for k,v in summary.items() if k != 'recent_results'}, indent=2))
        return 0
    except Exception as exc:
        print('Indexed chain collection failed; previous published evidence unchanged ('+type(exc).__name__+')', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
