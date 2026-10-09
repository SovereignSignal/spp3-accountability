"""Presentation for API observations, separate from contractual gate verdicts."""
from datetime import datetime, timezone
from html import escape
import re

TX = re.compile(r"0x[0-9a-f]{64}\Z")
# grails.yml is scheduled hourly, but GitHub delivers scheduled runs late: the
# observed gap between runs is 4 to 8 hours. A 3-hour threshold showed a
# healthy pipeline as stale for a large share of every day.
STALE_SECONDS = 12 * 3600


def esc(value):
    return escape(str(value))


def freshness(doc, now):
    try:
        observed = datetime.fromisoformat(doc["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return "unknown"
        age = now - observed.timestamp()
        return "unknown" if age < -60 else "stale" if age > STALE_SECONDS else "current"
    except (KeyError, ValueError, TypeError):
        return "unknown"


def row(label, value, detail="", state="wait"):
    return ('<li class="check check--%s"><span class="check__label">%s'
            '<span class="check__why">%s</span></span>'
            '<span class="check__val">%s</span></li>' % (state, esc(label), esc(detail), esc(value)))


def marketplace_summary(ctx):
    doc = ctx.get("grails") or {}
    body = "No successful measurement snapshot is available yet."
    if doc:
        body = "%s API sales observed. Snapshot: %s. %s." % (
            doc.get("observed", {}).get("api_sales", "Unknown"),
            doc.get("observed_at", "Unknown"),
            freshness(doc, ctx.get("now", datetime.now(timezone.utc).timestamp())))
    return ('<section><h2>Collected observations</h2><p class="prose">%s</p>'
            '<p class="prose"><a href="/provider/nomentum/measurements">Open Grails measurements and transaction evidence</a>'
            '</p><p class="colnote">Read-only observations. No gate is approved or paid by this collector.</p></section>' % esc(body))


def page_measurements(ctx):
    doc = ctx.get("grails") or {}
    intro = ('<h2>API-reported observations</h2><p class="lede">These Grails API buyer and volume '
             'counts are provisional. The API collector receipt checks below establish transaction success only. '
             'Independent settlement and registrar checks are reported separately.</p>'
             '<p class="prose"><a href="/provider/nomentum">Back to Nomentum Labs</a> · '
             '<a href="/grails.json">Snapshot JSON</a> · <a href="/grails-sales.json">Sale evidence JSON</a></p>')
    if not doc:
        return intro + '<p class="drift">Collector data unavailable. This is not a zero-sales observation.</p>'
    obs, cov = doc.get("observed", {}), doc.get("coverage", {})
    state = freshness(doc, ctx.get("now", datetime.now(timezone.utc).timestamp()))
    banner = ('<p class="drift drift--info"><b>Observation only. Committee approval pending.</b> '
              'Collector snapshot: %s · %s. Stream-health timestamps elsewhere refer to a separate feed.</p>' % (
                  esc(doc.get("observed_at", "Unknown")), esc(state)))
    if state != "current":
        banner += '<p class="drift"><b>Measurement data is stale or its age is unknown.</b> Values below are historical observations.</p>'
    cards = ''.join(row(label, obs.get(key, "Unknown"), detail) for label, key, detail in [
        ("Grails-source API sales", "api_sales", "Completed-sale records as reported by the API"),
        ("Candidate secondary-sale buyers", "candidate_secondary_buyers", "Distinct buyers after sanity exclusions; registrations and renewals excluded"),
        ("Candidate sale participants", "candidate_sale_participants", "Buyers plus sellers; not equivalent to paying users"),
        ("Candidate native ETH volume", "native_eth_volume", "No USD conversion; anti-wash methodology unapproved"),
        ("Candidate WETH volume", "weth_volume", "Separate from native ETH; no other tokens converted"),
    ])
    gate = doc.get("q1_2027", {})
    q1_copy = "Measurement window has not started; no score." if gate.get("status") == "not_started" else "Partial source coverage; no verified gate score."
    gates = ('<section><h2>Payment gates</h2><ul>' +
             row("Q1 2027: 150-wallet target", "Unscored", q1_copy) +
             row("Term-end: 250 ETH filtered volume", "Unscored", "Window and filtering parameters require committee approval") +
             row("Revenue gates", "Unscored", "No signing-date baseline or agreed USD comparison. See independent chain coverage for collected referral events.") +
             '</ul></section>')
    exclusions = ''.join(row(k.replace('_', ' '), v) for k, v in obs.get("exclusions", {}).items())
    checks = ''.join(row(k.replace('_', ' '), v) for k, v in obs.get("receipt_statuses", {}).items())
    recent = []
    for e in doc.get("recent_sales", []):
        tx = e.get("transaction_hash", "")
        link = ('<a href="https://etherscan.io/tx/%s" target="_blank" rel="noopener">%s…%s</a>' %
                (tx, tx[:10], tx[-6:])) if TX.fullmatch(tx) else "Unavailable"
        recent.append('<li class="check check--wait"><span class="check__label">%s'
                      '<span class="check__why">%s · receipt: %s · %s</span></span>'
                      '<span class="check__val">%s</span></li>' % (
                          esc(e.get("name") or e.get("asset_id", "ENS sale")), esc(e.get("sale_at", "")),
                          esc(e.get("receipt", {}).get("status", "not_checked")),
                          "present in latest scan" if e.get("present_in_latest_scan") else "removed upstream",
                          link))
    warnings = ''.join('<li>%s</li>' % esc(w) for w in doc.get("warnings", []))
    return (intro + banner + '<section><h2>Observed award-window activity</h2><p class="colnote">%s through %s (UTC, end exclusive). '
            'Start is the award execution date, not the signing-date baseline.</p><ul>%s</ul></section>' % (
                esc(cov.get("start", "Unknown")), esc(cov.get("end", "Unknown")), cards) + gates +
            '<section><h2>Data quality</h2><ul>' +
            row("API traversal", cov.get("status", "Unknown"), "%s pages; %s" % (cov.get("pages", "Unknown"), cov.get("boundary", "Unknown"))) +
            row("Records disappeared upstream", doc.get("removed_upstream_records", 0), "Retained in evidence; excluded from current candidate totals") +
            exclusions + '</ul><h3>API collector receipt checks</h3><ul>' + checks + '</ul>'
            '<p class="colnote">A successful receipt alone does not establish an ENS sale, price, marketplace attribution or finality.</p></section>' +
            '<section><h2>Recent transaction evidence</h2><ul>' + ''.join(recent) + '</ul></section>' +
            '<section><h2>API-only methodology limits</h2><p class="colnote">These limits apply to the API snapshot. '
            'The independent chain section reports its own verification and historical coverage.</p><ul>' + warnings + '</ul>'
            '<p class="colnote">Canonical sale evidence persists in Git. Full API response pages are SHA-256 identified '
            'workflow artifacts retained for 90 days. No wallet signatures or payments are performed.</p></section>')
