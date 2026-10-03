"""Render chain checks as separate evidence, never an official funding verdict."""
import html
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation


def esc(v):
    return html.escape(str(v))


def amount(raw):
    try:
        value = Decimal(str(raw))
        if not value.is_finite() or value < 0:
            return 'unavailable'
        return format(value / Decimal(10**18), 'f')
    except (InvalidOperation, ValueError):
        return 'unavailable'


def venue_section(doc):
    venue = doc.get('venue_attribution') or {}
    if not venue:
        return ''
    counts = [("API-reported Grails order origin", doc.get('settlements', {}).get('records')),
              ("App-reported Grails fill venue", venue.get('fill_venue_reports')),
              ("Fill venue unknown", venue.get('unknown_fill_venues'))]
    rows = ''.join('<li class="check check--wait"><span class="check__label">'+esc(label)+
                   '</span><span class="check__val">'+esc(value if value is not None else 'unavailable')+
                   '</span></li>' for label, value in counts)
    url = venue.get('methodology_source', '')
    source = ('<a href="'+esc(url)+'" target="_blank" rel="noopener">Grails source definitions</a>'
              if isinstance(url, str) and url.startswith('https://github.com/grailsmarket/backend/blob/') else 'Source definitions unavailable')
    return ('<h3>Order origin and fill venue</h3><p class="prose">'
            '<code>source</code> identifies the reported order origin. <code>filled_via</code> '
            'records an authenticated app report about execution through Grails. Neither field '
            'is independently established by a Seaport settlement.</p><ul>'+rows+'</ul>'
            '<p class="colnote">'+esc(venue.get('scope', 'Scope unavailable'))+' '
            'Unknown venue is not evidence that Grails was unused. These are observations, not paying-user or gate counts. '+source+'.</p>')


def chain_section(ctx):
    doc = ctx.get('grails_chain') or {}
    links = ('<p class="colnote"><a href="/grails-chain.json">Chain summary JSON</a> · '
             '<a href="/grails-settlements.json">Settlement evidence</a> · '
             '<a href="/grails-registrar.json">Registrar evidence</a></p>')
    if not doc:
        return '<section><h2>Independent on-chain checks</h2><p class="empty">Chain evidence unavailable. No verified totals reported.</p>'+links+'</section>'
    try:
        parsed = datetime.fromisoformat(doc['observed_at'].replace('Z', '+00:00'))
        ts = parsed.timestamp()
        age = ctx.get('now', ts) - ts
        stale = parsed.tzinfo is None or age > 3 * 3600 or age < -60
    except (ValueError, KeyError, TypeError, AttributeError):
        stale = True
    synced = bool(doc.get('source_snapshot_id')) and doc.get('source_snapshot_id') == (ctx.get('grails') or {}).get('snapshot_id')
    note = 'Snapshot is stale.' if stale else 'Snapshot is current.'
    if not synced:
        note += ' API and chain snapshots differ; totals refer to the recorded source snapshot.'
    s, r = doc.get('settlements', {}), doc.get('registrar', {})
    rows = []
    for label, value in [('API sale records in evidence set', s.get('records')),
                         ('Matched positive, non-self fills', s.get('positive_nonself_matches')),
                         ('Distinct matched secondary buyers', s.get('matched_secondary_buyers')),
                         ('Matched native ETH consideration', amount(s.get('native_eth_wei'))+' ETH'),
                         ('Matched WETH consideration', amount(s.get('weth_wei'))+' WETH')]:
        rows.append('<li class="check check--wait"><span class="check__label">'+esc(label)+'</span><span class="check__val">'+esc(value if value is not None else 'unavailable')+'</span></li>')
    statuses = ', '.join(esc(k)+': '+esc(v) for k,v in s.get('statuses', {}).items())
    recent = []
    for row in doc.get('recent_results', []):
        tx = row.get('transaction_hash', '')
        link = '<a href="https://etherscan.io/tx/'+tx+'" target="_blank" rel="noopener">transaction</a>' if re.fullmatch(r'0x[0-9a-f]{64}', tx) else 'no valid transaction link'
        st = row.get('settlement', {}).get('status', 'unavailable')
        venue = row.get('venue') or {}
        venue_note = (' · fill venue: '+esc(venue.get('filled_via') or 'unknown')+' (app report)') if venue else ''
        recent.append('<li class="check check--'+('ok' if st == 'matched' else 'wait')+'"><span class="check__label">'+esc(row.get('name', ''))+'<span class="check__why">'+link+venue_note+'</span></span><span class="check__val">'+esc(st)+'</span></li>')
    method = ''
    if r.get('retrieval'):
        method = ('<p class="drift drift--info"><b>Coverage basis:</b> '+esc(r.get('completeness_basis', 'unavailable'))+
                  '. Tagged events are individually matched to successful, finalized canonical receipts. '
                  'The index supplies completeness; receipts supply event integrity.</p>')
    return ('<section><h2>Independent on-chain checks</h2><p class="drift drift--info">'+esc(note)+' Checked '+esc(doc.get('observed_at'))+'; finalized block '+esc(doc.get('finalized_block'))+'.</p>'
            '<p class="prose">A matched sale has an allowlisted Seaport fill, matching ENS asset, participants, fee-inclusive amount and NFT transfer. Grails venue attribution remains API-reported. No gate is approved.</p>'
            '<ul>'+''.join(rows)+'</ul><p class="colnote">Settlement outcomes: '+statuses+'</p>'+venue_section(doc)+
            '<h3>ENS registrations and renewals</h3><p class="prose">Coverage: '+esc(r.get('coverage', 'unavailable'))+'. '+esc(r.get('start', ''))+' through '+esc(r.get('through_timestamp', ''))+'.</p>'+method+
            '<p class="prose">'+esc(r.get('registrations', 'unavailable'))+' registrations; '+esc(r.get('renewals', 'unavailable'))+' renewals; '+esc(r.get('distinct_registered_owners', 'unavailable'))+' distinct registered owners. On-chain protocol revenue in this scanned range: '+esc(amount(r.get('protocol_revenue_wei')))+' ETH.</p>'
            '<p class="colnote">Covers the two configured ENSv1 referral contracts. These events carry the Grails referrer tag. An owner or transaction initiator is not a verified paying user. Backfill totals are partial until coverage is current. USD revenue, signing baseline and official gate results remain unavailable.</p>'
            '<h3>Recent settlement evidence</h3><ul>'+''.join(recent)+'</ul>'+links+'</section>')
