# Grails chain evidence

This read-only collector adds an evidence layer to the existing API observations. It cannot approve a payment or determine contractual compliance.

## Sale checks

Only canonical Seaport deployment addresses are accepted. The collector matches the order hash, a single ENS BaseRegistrar ERC-721 or NameWrapper ERC-1155 asset, buyer/seller roles, consideration currency, fee-inclusive amount, and NFT transfer in a successful finalized transaction. It checks canonical block hashes. Bundles, unmatched transfers, ambiguous fills and amount discrepancies remain separate outcomes. Duplicate API references cannot double-count one on-chain fill.

A matched fill does not establish which website originated the sale. Grails attribution remains API-reported, even when the amount and settlement match. Source values are retained unchanged. Zero API amounts that conflict with positive chain amounts are discrepancies, not silently corrected observations.

## Registrar checks

The two known referral paths are the ENS controller at `0x59e16fccd424cc24e280be16e11bcd56fb0ce547` and the NameHash renewal helper at `0xf55575bde5953ee4272d5ce7cdd924c74d8fa81a`. The exact bytes32 Grails referrer comes from public Dune query 8064446. The collector verifies event emitters, signatures, label hashes, the referrer tag, integer costs, and canonical finalized block identity. Registrations include base cost plus premium. Renewals include their emitted cost.

The starting boundary is September 10, 2026 UTC, the recorded execution date. It is not the unknown Award Notice signing date. Scans are contiguous and resumable, with an explicit block budget. `backfill_in_progress` totals cover only the displayed interval; `current_known_contracts` means the configured paths have been scanned through the recorded finalized block. Other contracts or future ENSv2 paths are not implicitly covered. A failed scan does not advance the published cursor. Checkpoint hash changes fail closed.

Distinct registered owners are reported separately. Neither ownership nor the transaction initiator proves the paying end-user. No combined official paying-wallet figure is fabricated. USD revenue and the signing-date baseline remain null.

## Publication

The existing scheduled workflow (hourly on paper, every 4 to 8 hours in practice) collects API data, verifies chain evidence, runs tests and commits the files together. Raw receipts, blocks and log pages are retained as workflow artifacts for 90 days. Canonical event records and their hashes persist in Git. No chain calls occur while serving pages. Missing evidence is unavailable; mismatched or old snapshots receive an explicit warning.

Endpoints: `/grails-chain.json`, `/grails-settlements.json`, `/grails-registrar.json`. The existing Measurements page displays the independent checks alongside API observations.

## Primary references

- https://docs.opensea.io/docs/seaport-events-and-errors
- https://github.com/ProjectOpenSea/seaport#deployments
- https://github.com/ensdomains/ens-contracts/blob/staging/contracts/ethregistrar/ETHRegistrarController.sol
- https://github.com/namehash/ens-referrals#universalregistrarrenewalwithreferrer
- https://dune.com/queries/8064446
- https://docs.grails.app/docs/api/analytics/sales
