#!/usr/bin/env python3
"""Read-only Seaport settlement checks and referrer-tagged ENS event indexing.

Settlement proves the fill, not which website originated an order. Registrar
referrer tags prove the on-chain tag, not the identity of a paying end-user.
All results remain observations; this module cannot approve or release funds.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
ZERO = '0x' + '0' * 40
WETH = '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'
BASE = '0x57f1887a8bf19b14fc0df6fd9b2acc9af147ea85'
WRAPPER = '0xd4416b13d2b3a9abae7acd5d6c2bbdbe25686401'
CONTROLLER = '0x59e16fccd424cc24e280be16e11bcd56fb0ce547'
HELPER = '0xf55575bde5953ee4272d5ce7cdd924c74d8fa81a'
REFERRER = '0x' + '0' * 24 + '7e491cde0fbf08e51f54c4fb6b9e24afbd18966d'
START = '2026-09-10T00:00:00Z'
SEAPORTS = {
    '0x00000000006c3852cbef3e08e8df289169ede581',
    '0x00000000000006c7676171937c444f6bde3d6282',
    '0x0000000000000ad24e80fd803c6ac37206a45f15',
    '0x00000000000001ad428e4906ae43d8f9852d0dd6',
    '0x00000000000000adc04c56bf30ac9d3c0aaf14dc',
    '0x0000000000000068f116a894984e2db1123eb395',
}
HEX32 = re.compile(r'0x[0-9a-fA-F]{64}\Z')


def keccak(data: bytes) -> bytes:
    from chain import keccak256
    return keccak256(data)


def topic(signature: str) -> str:
    return '0x' + keccak(signature.encode()).hex()


FULFILLED = topic('OrderFulfilled(bytes32,address,address,address,(uint8,address,uint256,uint256)[],(uint8,address,uint256,uint256,address)[])')
TRANSFER = topic('Transfer(address,address,uint256)')
SINGLE = topic('TransferSingle(address,address,address,uint256,uint256)')
BATCH = topic('TransferBatch(address,address,address,uint256[],uint256[])')
REGISTERED = topic('NameRegistered(string,bytes32,address,uint256,uint256,uint256,bytes32)')
RENEWED = topic('NameRenewed(string,bytes32,uint256,uint256,bytes32)')
REFERRED = topic('RenewalReferred(string,bytes32,uint256,uint256,bytes32)')


class EvidenceError(ValueError):
    pass


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def quantity(value) -> int:
    if isinstance(value, str) and re.fullmatch(r'0x[0-9a-fA-F]+', value):
        return int(value, 16)
    if type(value) is int and value >= 0:
        return value
    raise EvidenceError('invalid chain quantity')


def word(data: bytes, offset: int) -> int:
    if offset < 0 or offset % 32 or offset + 32 > len(data):
        raise EvidenceError('ABI word outside payload')
    return int.from_bytes(data[offset:offset+32], 'big')


def address(value: int) -> str:
    if value < 0 or value >= 2**160:
        raise EvidenceError('noncanonical address')
    return '0x' + f'{value:040x}'


def topic_address(value: str) -> str:
    if not HEX32.fullmatch(value):
        raise EvidenceError('invalid address topic')
    return address(int(value, 16))


def payload(log: dict) -> bytes:
    value = log.get('data', '')
    if not isinstance(value, str) or not value.startswith('0x') or len(value) > 131074:
        raise EvidenceError('invalid ABI payload')
    try:
        data = bytes.fromhex(value[2:])
    except ValueError as exc:
        raise EvidenceError('invalid ABI hex') from exc
    if len(data) % 32:
        raise EvidenceError('unaligned ABI payload')
    return data


def items(data: bytes, offset: int, width: int) -> tuple[list, int]:
    if offset < 128:
        raise EvidenceError('array overlaps event header')
    count = word(data, offset)
    if count > 64 or offset + 32 + count * width * 32 > len(data):
        raise EvidenceError('invalid item array')
    out = []
    for n in range(count):
        p = offset + 32 + n * width * 32
        item = {'type': word(data, p), 'token': address(word(data, p+32)),
                'id': word(data, p+64), 'amount': word(data, p+96)}
        if item['type'] > 5:
            raise EvidenceError('invalid item type')
        if width == 5:
            item['recipient'] = address(word(data, p+128))
        out.append(item)
    return out, offset + 32 + count * width * 32


def decode_fill(log: dict) -> dict:
    topics = log.get('topics', [])
    if log.get('address', '').lower() not in SEAPORTS or len(topics) != 3 or topics[0].lower() != FULFILLED:
        raise EvidenceError('not a canonical Seaport fill')
    data = payload(log)
    first, second = word(data, 64), word(data, 96)
    offer, end1 = items(data, first, 4)
    consideration, end2 = items(data, second, 5)
    if max(first, second) < min(end1, end2):
        raise EvidenceError('overlapping arrays')
    return {'order_hash': '0x' + data[:32].hex(),
            'offerer': topic_address(topics[1]), 'zone': topic_address(topics[2]),
            'recipient': address(word(data, 32)), 'offer': offer,
            'consideration': consideration, 'emitter': log['address'].lower(),
            'log_index': quantity(log['logIndex'])}


def nft_transfer(receipt: dict, nft: dict, seller: str, buyer: str) -> bool:
    for log in receipt.get('logs', []):
        if log.get('address', '').lower() != nft['token']:
            continue
        t = log.get('topics', [])
        try:
            if nft['type'] == 2 and len(t) == 4 and t[0].lower() == TRANSFER:
                if topic_address(t[1]) == seller and topic_address(t[2]) == buyer and int(t[3], 16) == nft['id']:
                    return True
            if nft['type'] == 3 and len(t) == 4 and topic_address(t[2]) == seller and topic_address(t[3]) == buyer:
                data = payload(log)
                if t[0].lower() == SINGLE and word(data, 0) == nft['id'] and word(data, 32) == 1:
                    return True
                if t[0].lower() == BATCH:
                    a, b = word(data, 0), word(data, 32)
                    n = word(data, a)
                    if n > 64 or n != word(data, b):
                        continue
                    if any(word(data, a+32+i*32) == nft['id'] and word(data, b+32+i*32) == 1 for i in range(n)):
                        return True
        except (EvidenceError, ValueError, TypeError):
            continue
    return False


def fill_match(sale: dict, fill: dict, receipt: dict) -> dict:
    """Strict single-asset matching; complex bundles are deliberately unscored."""
    out = {'status': 'unsupported_structure', 'sale_amount_verified': False,
           'attribution_verified': False, 'marketplace_attribution': 'api_reported',
           'order_hash': fill['order_hash'], 'emitter': fill['emitter'],
           'log_index': fill['log_index']}
    token_id = (sale.get('source_record') or {}).get('token_id')
    if token_id is None or not str(token_id).isdigit():
        return dict(out, status='asset_identity_unproven')
    offer, cons = fill['offer'], fill['consideration']
    nfts = [x for x in offer + cons if x['type'] >= 2]
    if len(nfts) != 1 or nfts[0]['amount'] != 1:
        return out
    nft = nfts[0]
    if nft['id'] != int(token_id) or (nft['type'], nft['token']) not in ((2, BASE), (3, WRAPPER)):
        return dict(out, status='asset_mismatch')
    buyer, seller = sale['buyer'], sale['seller']
    if nft in offer:
        money = cons
        roles = fill['offerer'] == seller and fill['recipient'] in (buyer, ZERO)
        roles = roles and any(x.get('recipient') == seller and x['amount'] > 0 for x in money)
        if len(offer) != 1:
            return out
    else:
        money = offer
        roles = fill['offerer'] == buyer and nft.get('recipient') == buyer and fill['recipient'] == seller
        fees = [x for x in cons if x is not nft]
        if len(money) != 1 or any(x['type'] >= 2 for x in fees):
            return out
        if any(x['token'] != money[0]['token'] for x in fees) or sum(x['amount'] for x in fees) > money[0]['amount']:
            return out
    if not roles:
        return dict(out, status='participant_mismatch')
    if not money or any((x['type'], x['token'], x['id']) not in ((0, ZERO, 0), (1, WETH, 0)) for x in money):
        return out
    currencies = {x['token'] for x in money}
    if len(currencies) != 1:
        return out
    currency = next(iter(currencies))
    total = sum(x['amount'] for x in money)
    out.update({'onchain_amount_raw': str(total), 'onchain_currency': currency,
                'asset_contract': nft['token'], 'asset_id': str(nft['id'])})
    if not nft_transfer(receipt, nft, seller, buyer):
        return dict(out, status='nft_transfer_not_matched')
    out.update({'asset_transfer_verified': True, 'participants_verified': True})
    if currency != sale['currency'] or total != int(sale['amount_raw']):
        return dict(out, status='amount_or_currency_mismatch')
    return dict(out, status='matched', sale_amount_verified=True)


def verify_sale(sale: dict, receipt: dict | None, header: dict, finalized: int) -> dict:
    base = {'status': 'unavailable', 'sale_amount_verified': False, 'attribution_verified': False}
    if receipt is None:
        return dict(base, status='pending')
    if receipt.get('transactionHash', '').lower() != sale['transaction_hash']:
        return dict(base, status='receipt_hash_mismatch')
    if quantity(receipt.get('status')) != 1:
        return dict(base, status='reverted')
    block = quantity(receipt['blockNumber'])
    base.update({'block_number': block, 'block_hash': receipt['blockHash'],
                 'chain_timestamp': iso(quantity(header['timestamp']))})
    if block > finalized:
        return dict(base, status='not_finalized')
    if quantity(header['number']) != block or header['hash'].lower() != receipt['blockHash'].lower():
        return dict(base, status='canonical_hash_mismatch')
    fills = []
    for log in receipt.get('logs', []):
        if log.get('address', '').lower() not in SEAPORTS or not log.get('topics') or log['topics'][0].lower() != FULFILLED:
            continue
        try:
            fill = decode_fill(log)
        except EvidenceError:
            return dict(base, status='malformed_seaport_log')
        if sale.get('order_hash') and sale['order_hash'] != fill['order_hash']:
            continue
        result = fill_match(sale, fill, receipt)
        if sale.get('order_hash') or result['status'] not in ('asset_mismatch', 'unsupported_structure'):
            fills.append(result)
    if len(fills) != 1:
        return dict(base, status='no_matching_fill' if not fills else 'ambiguous_fills')
    result = dict(base, **fills[0])
    result['api_timestamp_delta_seconds'] = int(datetime.fromisoformat(sale['sale_at'].replace('Z', '+00:00')).timestamp()) - quantity(header['timestamp'])
    if result['chain_timestamp'] < START:
        result['status'] = 'outside_observation_window'
        result['sale_amount_verified'] = False
    return result


def decode_registrar(log: dict) -> dict | None:
    t = log.get('topics', [])
    emitter = log.get('address', '').lower()
    if not t:
        raise EvidenceError('missing registrar topics')
    signature = t[0].lower()
    if emitter == CONTROLLER and signature in (REGISTERED, RENEWED):
        registration = signature == REGISTERED
    elif emitter == HELPER and signature == REFERRED:
        registration = False
    else:
        raise EvidenceError('unrecognized registrar emitter/event')
    if len(t) != (3 if registration else 2):
        raise EvidenceError('invalid registrar topics')
    data = payload(log)
    slot = 128 if registration else 96
    ref = '0x' + word(data, slot).to_bytes(32, 'big').hex()
    if ref != REFERRER:
        return None
    offset = word(data, 0)
    if offset < slot + 32:
        raise EvidenceError('label overlaps header')
    n = word(data, offset)
    if n > 1024 or offset + 32 + n > len(data):
        raise EvidenceError('invalid registrar label')
    label_bytes = data[offset+32:offset+32+n]
    label = label_bytes.decode('utf-8')
    if '0x' + keccak(label_bytes).hex() != t[1].lower():
        raise EvidenceError('label hash mismatch')
    tx = log['transactionHash'].lower()
    cost = word(data, 32) + (word(data, 64) if registration else 0)
    return {'key': f'1:{tx}:{quantity(log["logIndex"])}', 'kind': 'registration' if registration else 'renewal',
            'transaction_hash': tx, 'log_index': quantity(log['logIndex']),
            'block_number': quantity(log['blockNumber']), 'block_hash': log['blockHash'].lower(),
            'contract': emitter, 'name': label + '.eth', 'labelhash': t[1].lower(),
            'registered_owner': topic_address(t[2]) if registration else None,
            'cost_wei': str(cost), 'referrer_tag': ref, 'referrer_tag_verified': True,
            'payer_verified': False, 'transaction_initiator': None,
            'source_log': log, 'source_log_sha256': digest(log)}


class Reader:
    def __init__(self, raw_dir: Path | None = None):
        from chain import Chain
        self.client = Chain()
        self.raw_dir = raw_dir
        self.blocks = {}
        self.receipts = {}

    def rpc(self, method, params):
        value = self.client._rpc(method, params)
        # Avoid retrying known-incompatible endpoints for every small log page.
        last = self.client.last_endpoint
        if last in self.client.rpcs:
            self.client.rpcs = [last] + [x for x in self.client.rpcs if x != last]
        if self.raw_dir is not None:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            doc = {'method': method, 'params': params, 'result': value}
            (self.raw_dir / (digest(doc) + '.json')).write_text(json.dumps(doc))
        return value

    def block(self, number):
        if number not in self.blocks:
            h = self.rpc('eth_getBlockByNumber', [hex(number), False])
            if not isinstance(h, dict) or quantity(h['number']) != number or not HEX32.fullmatch(h['hash']):
                raise EvidenceError('block unavailable')
            self.blocks[number] = h
        return self.blocks[number]

    def receipt(self, tx):
        if tx not in self.receipts:
            self.receipts[tx] = self.rpc('eth_getTransactionReceipt', [tx])
        return self.receipts[tx]


def first_block(reader, ts, latest):
    lo, hi = 0, latest
    while lo < hi:
        mid = (lo + hi) // 2
        if quantity(reader.block(mid)['timestamp']) < ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def index_registrar(reader, previous, finalized, budget, as_of):
    target = quantity(finalized['number'])
    wanted = int(datetime.fromisoformat(START.replace('Z', '+00:00')).timestamp())
    if previous:
        if previous.get('schema_version') != 1 or previous.get('referrer') != REFERRER:
            raise EvidenceError('incompatible registrar checkpoint')
        start = previous['start_block']
        cursor = previous['through_block']
        if cursor > target or reader.block(cursor)['hash'].lower() != previous['through_block_hash'].lower():
            raise EvidenceError('registrar checkpoint no longer canonical')
    else:
        start = first_block(reader, wanted, target)
        cursor = start - 1
    end = min(target, cursor + budget)
    records = {e['key']: dict(e) for e in (previous or {}).get('events', [])}
    manifests = []
    for left in range(cursor+1, end+1, 750):
        right = min(left+749, end)
        logs = reader.rpc('eth_getLogs', [{'address': [CONTROLLER, HELPER],
            'topics': [[REGISTERED, RENEWED, REFERRED]], 'fromBlock': hex(left), 'toBlock': hex(right)}])
        if not isinstance(logs, list) or len(logs) >= 10000:
            raise EvidenceError('invalid or potentially truncated registrar logs')
        manifests.append({'from_block': left, 'to_block': right, 'logs': len(logs), 'sha256': digest(logs)})
        for log in logs:
            if log.get('removed') or not left <= quantity(log['blockNumber']) <= right:
                raise EvidenceError('out-of-range or removed registrar log')
            event = decode_registrar(log)
            if event is None:
                continue
            header = reader.block(event['block_number'])
            if header['hash'].lower() != event['block_hash']:
                raise EvidenceError('registrar log/header mismatch')
            event['timestamp'] = iso(quantity(header['timestamp']))
            records[event['key']] = event
    collisions = Counter((e['transaction_hash'], e['labelhash'], e['kind']) for e in records.values())
    for event in records.values():
        event['ambiguous_duplicate'] = collisions[(event['transaction_hash'], event['labelhash'], event['kind'])] > 1
    header = reader.block(end)
    return {'schema_version': 1, 'observed_at': as_of, 'start': START, 'start_block': start,
            'through_block': end, 'through_block_hash': header['hash'],
            'through_timestamp': iso(quantity(header['timestamp'])), 'target_finalized_block': target,
            'coverage': 'current_known_contracts' if end == target else 'backfill_in_progress',
            'referrer': REFERRER, 'mapping_source': 'https://dune.com/queries/8064446',
            'contracts': [CONTROLLER, HELPER], 'scan_manifests': manifests,
            'events': sorted(records.values(), key=lambda e: (e['block_number'], e['log_index'])),
            'limitations': ['Covers the two named ENSv1 referral paths only; future registrars require configuration.',
                           'Execution date is not the unknown signing baseline.',
                           'Referrer tags are public caller-supplied data, not independent proof of website use.']}


def settlement_summary(results):
    statuses = Counter(r['settlement']['status'] for r in results)
    good = [r for r in results if r['settlement']['status'] == 'matched' and int(r['amount_raw']) > 0
            and r['buyer'] != r['seller'] and ZERO not in (r['buyer'], r['seller'])]
    unique = {(r['transaction_hash'], r['settlement']['log_index'], r['settlement']['asset_contract'], r['settlement']['asset_id']): r for r in good}
    duplicate_references = len(good) - len(unique)
    good = list(unique.values())
    totals = {t: sum(int(r['amount_raw']) for r in good if r['currency'] == t) for t in (ZERO, WETH)}
    return {'records': len(results), 'statuses': dict(statuses), 'duplicate_fill_references': duplicate_references,
            'positive_nonself_matches': len(good), 'matched_secondary_buyers': len({r['buyer'] for r in good}),
            'native_eth_wei': str(totals[ZERO]), 'weth_wei': str(totals[WETH]),
            'grails_attribution_verified': False, 'gate_eligible': False}


def registrar_summary(doc):
    eligible = [e for e in doc['events'] if int(e['cost_wei']) > 0 and not e['ambiguous_duplicate']]
    reg = [e for e in eligible if e['kind'] == 'registration']
    renew = [e for e in eligible if e['kind'] == 'renewal']
    return {'coverage': doc['coverage'], 'start': START, 'through_timestamp': doc['through_timestamp'],
            'through_block': doc['through_block'], 'target_finalized_block': doc['target_finalized_block'],
            'registrations': len(reg), 'renewals': len(renew),
            'distinct_registered_owners': len({e['registered_owner'] for e in reg if e['registered_owner'] != ZERO}),
            'protocol_revenue_wei': str(sum(int(e['cost_wei']) for e in eligible)),
            'ambiguous_records_excluded': sum(e['ambiguous_duplicate'] for e in doc['events']),
            'verified_paying_wallets': None, 'usd_revenue': None, 'gate_eligible': False}


def load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def save(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n')
    os.replace(temp, path)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path, default=ROOT / 'data/grails')
    p.add_argument('--raw-dir', type=Path)
    p.add_argument('--max-blocks', type=int, default=24000)
    p.add_argument('--max-receipts', type=int, default=80)
    args = p.parse_args(argv)
    if args.max_blocks < 1 or args.max_receipts < 1:
        p.error('budgets must be positive')
    try:
        source = load(args.data_dir / 'sales.json')
        if not source.get('snapshot_id') or not isinstance(source.get('events'), list):
            raise EvidenceError('API evidence snapshot unavailable')
        reader = Reader(args.raw_dir)
        if quantity(reader.rpc('eth_chainId', [])) != 1:
            raise EvidenceError('wrong chain')
        finalized = reader.rpc('eth_getBlockByNumber', ['finalized', False])
        target = quantity(finalized['number'])
        as_of = iso(int(datetime.now(timezone.utc).timestamp()))
        results = []
        previous = load(args.data_dir / 'settlements.json')
        prior = {r['key']: r for r in previous.get('results', [])}
        events = [e for e in source['events'] if e.get('present_in_latest_scan')]
        events.sort(key=lambda e: (e['key'] in prior, -int(datetime.fromisoformat(e['sale_at'].replace('Z', '+00:00')).timestamp())))
        seen = set()
        for sale in events:
            tx = sale['transaction_hash']
            old = prior.get(sale['key'])
            cache = old.get('settlement', {}) if old else {}
            if cache.get('status') == 'matched' and old.get('source_record_sha256') == sale['source_record_sha256']:
                h = reader.block(cache['block_number'])
                if cache['block_number'] <= target and h['hash'].lower() == cache['block_hash'].lower():
                    results.append(dict(sale, settlement=cache))
                    continue
            if tx not in seen and len(seen) >= args.max_receipts:
                results.append(dict(sale, settlement={'status': 'budget_pending', 'attribution_verified': False}))
                continue
            seen.add(tx)
            try:
                receipt = reader.receipt(tx)
                h = reader.block(quantity(receipt['blockNumber'])) if receipt else finalized
                result = verify_sale(sale, receipt, h, target)
                result['receipt_sha256'] = digest(receipt)
            except Exception:
                result = {'status': 'unavailable', 'sale_amount_verified': False, 'attribution_verified': False}
            result['checked_at'] = as_of
            results.append(dict(sale, settlement=result))
        registrations = index_registrar(reader, load(args.data_dir / 'registrar.json'), finalized, args.max_blocks, as_of)
        summary = {'schema_version': 1, 'observed_at': as_of, 'source_snapshot_id': source['snapshot_id'],
                   'finalized_block': target, 'finalized_block_hash': finalized['hash'],
                   'settlements': settlement_summary(results), 'registrar': registrar_summary(registrations),
                   'gate_status': 'observation_only', 'committee_approved': False,
                   'q1_2027': {'passed': None, 'verified_paying_wallets': None},
                   'warnings': ['Seaport settlement does not independently establish Grails source attribution.',
                                'Registration owners and transaction initiators are not proven paying users.',
                                'Unknown signing baseline, wallet-role policy and wash filters block gate scoring.'],
                   'recent_results': sorted(results, key=lambda e: e['sale_at'], reverse=True)[:10]}
        summary['snapshot_id'] = digest(summary)
        evidence = {'schema_version': 1, 'observed_at': as_of, 'snapshot_id': summary['snapshot_id'],
                    'source_snapshot_id': source['snapshot_id'], 'results': results}
        save(args.data_dir / 'settlements.json', evidence)
        save(args.data_dir / 'registrar.json', registrations)
        save(args.data_dir / 'chain.json', summary)
        print(json.dumps({k: v for k, v in summary.items() if k != 'recent_results'}, indent=2))
        return 0
    except Exception as exc:
        print('Chain collection failed; previous published snapshot unchanged (' + type(exc).__name__ + ')', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
