# Grails observation collector

Read-only, hourly at :17 UTC via `.github/workflows/grails.yml`. The site serves committed snapshots, with no requests to Grails or Ethereum during page rendering.

## Sources and authority

Public feed: https://api.grails.app/api/v1/analytics/sales
Schema: https://docs.grails.app/docs/api/analytics/sales
Award: https://discuss.ens.domains/t/executable-spp3-marketplace-rfp-award-nomentum-labs-grails/22374

Every request filters `source=grails`, period `all`, and descending date. Every returned row must itself have `source=grails`. The execution date, September 10, 2026, is the observation boundary. It is not a substitute for the Award Notice signing date or 90-day revenue baseline.

API-reported sales, receipt checks, candidate calculations, committee determinations and payments are distinct. No gate in commitments.json is changed by this collector. Earlier draft methods in commitments.json are not committee approval. Paying-wallet roles and filtering parameters remain proposed.

## Collection and retention

A full window scan uses pages of 100 records, stops below the observation start or at the feed end, and re-reads the first page to detect moving offset pagination. A page cap, repeated page, schema error, invalid ordering or contradictory duplicate fails the run before data is replaced. Moving-feed errors get one fresh scan retry. No silent partial-window publication.

Sale identity includes Ethereum chain, transaction, asset, order hash and participants, so multiple assets in one transaction survive. Raw integer amounts are strings. Native ETH and WETH remain separate; other currencies are not converted. Self trades, zero value/zero addresses, unsuccessful receipts and ambiguous bundle price allocations are excluded from candidate counts. This is not a complete anti-wash methodology.

`data/grails/sales.json` retains canonical evidence fields and prior versions of changed rows. Rows that disappear remain marked absent and excluded from current candidate totals. Each source page and record is SHA-256 identified. Full source pages are GitHub Actions artifacts retained for 90 days; canonical evidence and snapshot history persist in Git.

Receipt checks are bounded to five recent transaction hashes per run. A successful receipt proves that transaction succeeded only. Sale amount, NFT fill, source attribution and finality are not independently verified by this v1. Missing or unavailable checks are visible and never elevated to verification.

## Metrics and limits

The observed window shows API sales, distinct secondary-sale buyers, buyer-plus-seller participants, native ETH consideration, and WETH consideration. These are candidate observations. Registrations, renewals and ENS protocol revenue are not collected, so the 150-wallet gate is not fully measured. The Q1 2027 window is [2027-01-01, 2027-04-01) UTC and remains null before it starts. The 250 ETH term-end metric remains null until the period and anti-wash/minimum-value policy are approved. No pass/fail result or payment authorization is generated.

## Operations

Run: `python scripts/grails_measurements.py --output-dir /tmp/grails-check --raw-dir /tmp/grails-raw`
Offline tests: `python -m unittest discover -s tests -v`

The successful workflow commits both JSON files atomically in one Git commit and triggers Railway through the existing source integration. A failed collection keeps last-good data and produces a failed GitHub run. The measurements UI marks snapshots stale after three hours. `/grails.json` and `/grails-sales.json` return HTTP 503 when no valid snapshot exists. Verify snapshot_id equality when joining the feeds.

Next: independently match Seaport fills and amounts, add Grails-attributed registration/renewal events, and reconcile those observations against a committee-approved measurement specification.
