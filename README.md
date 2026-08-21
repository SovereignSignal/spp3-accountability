# spp3-accountability

Accountability infrastructure for the ENS SPP3 cohort, operated by
sovereignsignal.eth on the committee VM. Runs **alongside** `~/SPP3-Workspace`
and `~/RFP-Workspace` and reads or writes neither.

The public tracker is the Railway service `spp3-streams`, which renders the
committed JSON in this repo. Sub-project B (stream health monitor), C
(quarterly report watcher), the Notion whitelist export, and the tracker
site are implemented.

## What the monitor checks, daily at 15:00 UTC

1. **Every provider stream against its ratified rate.** Four SPP3 cohort
   streams, two continuing SPP2 streams, four committee salary streams.
2. **Every retired SPP2 stream is stopped.** A retired stream still running
   means the DAO is paying someone it stopped funding.
3. **No unaccounted stream exists.** The pod's net flowrate must equal master
   inflow minus known outflows. Checking only known receivers is blind to a
   receiver nobody recorded; this catches one without an event indexer.
4. **Funding runway.** Timelock USDCx plus USDC (autowrap's input) against the
   master stream's daily draw.

## Two rules that matter

**Compare `wei/s` integers, never dollars.** Rates are
`annual_usd * 1e18 // 31_536_000`, so the nominal $3.21M/yr master stream is
actually $3,207,871/yr. Comparing dollars flags healthy streams every day, and
daily false alarms are how monitoring gets ignored. SPP2's streams ran dead for
33.7 days and cost $416,076.52 in hand-calculated backpay.

**Runway thresholds are 60 days (warning) and 21 days (critical)**, set above
SPP2's 33.7-day outage so an alert is still actionable when it fires.

## Running it

```bash
python3 scripts/stream_monitor.py --dry-run    # check and print, change nothing
python3 scripts/stream_monitor.py              # write, commit, alert on change
python3 scripts/stream_monitor.py --heartbeat  # weekly all-clear
python3 -m unittest discover -s tests -v       # stdlib only, no network needed
```

Alerts use the flag-file pattern: one message per distinct fault, one recovery
notice when it clears, silence otherwise. The Monday heartbeat exists because
silence from a healthy system and silence from a dead monitor are otherwise
indistinguishable.

## Data files

| File | Written by | Hand-edit? |
|---|---|---|
| `data/providers.json` | humans (`_generated: false`) | yes, then run the tests |
| `data/commitments.json` | humans (`_generated: false`) | yes; milestones stay provisional until Award Notice Item 5 |
| `data/calendar.json` | humans (`_generated: false`) | yes |
| `data/streams/status.json` | the monitor (`_generated: true`) | **never** |
| `data/notion/board.json` | `notion_export.py` (`_generated: true`) | **never**; whitelist only |

## Operational warnings

- **The crontab is shared.** `crontab <file>` replaces the *entire* user
  crontab. `scripts/cron/crontab-accountability.txt` is a merged file
  containing the RFP intake lines too, and is the only file that may be
  installed. Diff it against `crontab -l` before installing.
- Secrets live in `~/.claude/secrets/`, never in this repo.
- Standard library only. Do not add pip dependencies; this VM runs live
  RFP intake infrastructure.
- Addresses were verified on-chain 2026-08-04 at block 25,685,582. Source:
  blockful `ep-6-49/podStreamSetup.t.sol` @ `04d349a`.
