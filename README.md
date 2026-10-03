# spp3-accountability

Public accountability infrastructure for ENS Service Provider Program Season 3.

Production: https://spp3-streams-production.up.railway.app  
Repository: https://github.com/SovereignSignal/spp3-accountability  
Runtime: Railway service `spp3-streams`; monitoring/collection jobs publish committed evidence snapshots to `master`.

## What the system does

The site is a read-only renderer over committed evidence. HTTP requests do not make live chain calls.

Public surfaces:

- **Overview**: SPP3 program state and funded entities.
- **Providers**: award scope, provisional commitments, reports and evidence review.
- **Marketplace**: Nomentum Labs / Grails award structure and release gates.
- **Measurements**: Grails observations, settlement checks, registrar coverage and measurement limitations.
- **Ledger**: unified program financial position from discrete USDC transfers plus continuous USDCx delivery.
- **Streams**: live Superfluid rate health and event-derived delivered amounts.
- **Reports**: quarterly filing status.
- **Calendar**: reporting and program milestones.
- JSON evidence endpoints include `/status.json`, `/ledger.json`, `/grails.json`, `/grails-sales.json`, `/grails-chain.json`, `/grails-settlements.json`, and `/grails-registrar.json`.

## Accountability model

The tracker deliberately separates different levels of evidence:

1. **Authorized / binding**: DAO executions and executed award terms where public.
2. **On-chain observed**: token transfers, Superfluid state/history, settlement and registrar events.
3. **Provider reported**: quarterly-report claims and metrics.
4. **Independently verified artifact**: public code, deployments, transactions or other reproducible evidence.
5. **Committee verified**: only when the committee has actually made that determination.

Missing data is not treated as a pass or a fail.

The four original cohort providers' application milestones remain **provisional** until the binding Award Notice Item 5 records are available. Grails payment gates remain unapproved until the required scoring inputs and committee determinations exist.

## Current production model

### Stream monitoring

Daily monitoring checks:

1. Every known provider/committee stream against its exact ratified `wei/s` rate.
2. Retired SPP2 streams remain stopped.
3. Pod net flow equals known inflow minus known outflows, catching unknown receivers.
4. Funding runway against the master stream draw.

Rates are compared as integers, never rounded dollar values.

Alert state is keyed by distinct fault, so a new fault is not hidden by an older active alert. Runway warning thresholds are 60 days and 21 days.

### Event-sourced financial ledger

The ledger combines two independent event classes:

- **USDC custody**: every USDC transfer into/out of `stream.mg.wg.ens.eth`.
- **USDCx delivery**: Superfluid stream history checkpointed against CFA state and incrementally updated from `FlowUpdated` events.

The historical bootstrap is valid because each current SPP3 stream's CFA `lastUpdated` was at or before the 1 Aug 2026 SPP3 epoch. Future rate changes, stops and restarts are ingested incrementally and reconciled against live CFA reads.

### Grails evidence

Grails measurement data is observation-only. No payment gate is automatically approved.

Current collection distinguishes:

- API-reported **order origin** from reported **fill venue**.
- Seaport settlement proof from Grails venue attribution.
- registered owners from verified paying users.
- indexed-source completeness from independent proof of all website activity.

Registrar coverage uses ordered Blockscout traversal for the two configured ENSv1 referral contracts, then validates matching events against successful canonical finalized Ethereum receipts. Legacy checkpoints are fully rescanned; v2 checkpoints retain overlap and canonical-hash checks.

The current API-origin sale feed can omit foreign-origin orders executed through Grails. Unknown venue remains unknown.

## Automation and CI

GitHub Actions runs the stdlib unit suite on PRs and `master`.

The Grails workflow:

- refreshes public observations;
- verifies settlement evidence;
- completes registrar coverage to a fixed finalized block;
- validates canonical receipts;
- fails closed on incomplete traversal, conflicting source data, invalid bootstrap evidence or unavailable required verification;
- publishes a new committed snapshot only after verification.

The on-chain ledger workflow refreshes its checkpoint and publishes only verified changes.

Railway auto-deploys `master`.

## Data files

| File | Source | Hand-edit? |
|---|---|---|
| `data/providers.json` | human-maintained program config | yes, then test |
| `data/commitments.json` | human-maintained award/evidence model | yes, then test |
| `data/calendar.json` | human-maintained program calendar | yes |
| `data/streams/status.json` | stream monitor | never |
| `data/onchain/ledger.json` | Ethereum ledger workflow | never |
| `data/grails/*.json` | Grails observation/verification workflow | never |
| `data/notion/board.json` | whitelisted Notion export | never |

## Running locally

```bash
python3 -m unittest discover -s tests -v

python3 scripts/stream_monitor.py --dry-run
python3 scripts/stream_monitor.py
python3 scripts/stream_monitor.py --heartbeat

python3 scripts/ledger.py --dry-run
python3 scripts/grails_measurements.py --output-dir /tmp/grails-check --raw-dir /tmp/grails-raw
```

The project intentionally remains Python standard-library only.

## Operational rules

- Never hand-edit generated evidence files.
- Never turn missing evidence into a positive or negative gate result.
- Never equate a successful Seaport fill with proof that Grails originated or executed the user interaction.
- Never count registered owners or transaction initiators as paying users without the approved methodology.
- Never publish a partial registrar traversal as complete coverage.
- Secrets stay outside the repository.
- The committee VM crontab is shared. Do not replace it without reconciling existing jobs.

## Known unresolved inputs

These are documented gaps, not bugs:

- binding Award Notice Item 5 records for the four original cohort providers;
- Grails signing-date revenue baseline;
- approved USD conversion methodology;
- final paying-wallet policy for gate scoring;
- exact anti-wash and minimum-value parameters for the secondary-volume gate;
- authoritative Grails fill-venue evidence for the current API-origin records.

Until those inputs exist, corresponding gate/milestone results remain pending.

## Production verification

As of **2026-10-03**, CI, the production Grails refresh, Railway deployment, health endpoint, on-chain ledger, registrar evidence and public measurement surfaces were verified after the final implementation pass.
