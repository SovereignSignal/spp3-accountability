"""render.py — build the public stream-health page from status.json.

Pure: takes the two data documents plus a clock reading and returns HTML.
No I/O, so it is testable without a server or a chain connection.

Design note: every figure the page asserts is derived from on-chain reads.
The live counters are computed in the browser from (rate x elapsed) using the
program epoch, never from a stream's own lastUpdated -- see spp3_stream_start
in providers.json for why that distinction matters.
"""
import html
import json

SECONDS_PER_YEAR = 31_536_000

VERDICT_COPY = {
    "healthy": "All streams flowing",
    "warning": "Streams flowing, funding needs attention",
    "critical": "Stream fault",
}


def _usd(wei_s):
    return wei_s * SECONDS_PER_YEAR / 10**18


def _money(n, dp=0):
    return "{:,.{dp}f}".format(n, dp=dp)


def _esc(s):
    return html.escape(str(s))


def _short(addr):
    return addr[:6] + "…" + addr[-4:]


def _rows(streams, max_rate):
    out = []
    for s in streams:
        pct = (s["actual_wei_s"] / max_rate * 100) if max_rate else 0
        state = "ok" if s["ok"] else "fault"
        out.append(
            '<li class="stream stream--{state}">'
            '<div class="stream__id">'
            '<span class="stream__name">{name}</span>'
            '<a class="stream__addr" href="https://etherscan.io/address/{addr}"'
            ' target="_blank" rel="noopener">{short}</a>'
            '</div>'
            '<div class="stream__bar"><span style="width:{pct:.2f}%"></span></div>'
            '<div class="stream__rate"><b>${rate}</b><span>/yr</span></div>'
            '<div class="stream__acc" data-rate="{wei}" data-since="{since}">'
            '<span class="acc__whole">0</span><span class="acc__frac">.00</span>'
            '</div>'
            '</li>'.format(
                state=state, name=_esc(s["name"]), addr=_esc(s["address"]),
                short=_esc(_short(s["address"])), pct=pct,
                rate=_money(_usd(s["expected_wei_s"])),
                wei=s["actual_wei_s"], since=s.get("since", 0)))
    return "\n".join(out)


def render(status, providers, now):
    epoch = providers["spp3_stream_start"]
    cohort = [s for s in status["streams"] if s["cohort"] == "spp3"]
    continuing = [s for s in status["streams"] if s["cohort"] == "spp2-continuing"]
    committee = [s for s in status["streams"] if s["cohort"] == "committee"]

    cohort_rate = sum(s["actual_wei_s"] for s in cohort)
    delivered = cohort_rate * max(0, now - epoch) / 10**18
    days = max(0.0, (now - epoch) / 86400)

    verdict = status["overall"]
    runway = status["runway"]
    net = status["net_flow"]
    retired_ok = sum(1 for r in status["retired"] if r["ok"])
    age_min = max(0, int((now - _parse_iso(status["checked_at"])) / 60))

    max_rate = max([s["actual_wei_s"] for s in status["streams"]] or [1])

    checks = [
        ("Retired SPP2 streams stopped",
         "%d of %d" % (retired_ok, len(status["retired"])),
         retired_ok == len(status["retired"])),
        ("Unaccounted flow on the pod",
         "%d wei/s" % net["unaccounted_wei_s"], net["ok"]),
        ("Funding runway",
         "%s days" % _money(runway["combined_days"]), runway["ok"]),
    ]
    check_html = "\n".join(
        '<li class="check check--{k}"><span class="check__label">{l}</span>'
        '<span class="check__val">{v}</span></li>'.format(
            k="ok" if ok else "fault", l=_esc(label), v=_esc(val))
        for label, val, ok in checks)

    faults = ""
    if verdict != "healthy":
        items = "".join(
            "<li><b>%s</b> %s</li>" % (_esc(f["subject"]), _esc(f["detail"]))
            for f in status.get("findings", []))
        if items:
            faults = '<ul class="faults">%s</ul>' % items

    return PAGE.format(
        verdict=verdict,
        verdict_copy=_esc(VERDICT_COPY.get(verdict, verdict)),
        block="{:,}".format(status["block_number"]),
        age=age_min,
        delivered_whole=_money(int(delivered)),
        delivered_frac="%02d" % int(round((delivered % 1) * 100)),
        cohort_rate=cohort_rate,
        epoch=epoch,
        epoch_h=_fmt_utc(epoch),
        days="%.1f" % days,
        cohort_yr=_money(_usd(cohort_rate)),
        n_cohort=len(cohort),
        cohort_rows=_rows(cohort, max_rate),
        continuing_rows=_rows(continuing, max_rate),
        committee_rows=_rows(committee, max_rate),
        checks=check_html,
        faults=faults,
        pod=_esc(status.get("_pod", "")),
        now=now,
    )


def _parse_iso(s):
    import calendar
    import time as _t
    try:
        return calendar.timegm(_t.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0


def _fmt_utc(ts):
    import time as _t
    return _t.strftime("%d %b %Y %H:%M UTC", _t.gmtime(ts))


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SPP3 stream health</title>
<meta name="description" content="Live on-chain verification of ENS SPP3 service provider payment streams.">
<style>
:root {{
  --ground:#EDF0F3; --ink:#0E141A; --flow:#1B5CF0;
  --ok:#0E8A5F; --warn:#B87503; --fault:#C2331B;
  --rule:rgba(14,20,26,.14); --mute:rgba(14,20,26,.58);
  --mono:ui-monospace,"SF Mono",SFMono-Regular,"JetBrains Mono",Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}}
@media (prefers-color-scheme:dark) {{
  :root {{ --ground:#0B0F14; --ink:#E6ECF2; --flow:#5B8DFF;
    --ok:#3FCF97; --warn:#E0A233; --fault:#FF6A4D;
    --rule:rgba(230,236,242,.16); --mute:rgba(230,236,242,.6); }}
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
  -webkit-font-smoothing:antialiased;line-height:1.5}}
.wrap{{max-width:940px;margin:0 auto;padding:0 20px}}

.verdict{{border-bottom:1px solid var(--rule);padding:14px 0}}
.verdict .wrap{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
.dot{{width:9px;height:9px;border-radius:50%;background:var(--ok);flex:none;
  box-shadow:0 0 0 0 currentColor;color:var(--ok);animation:pulse 2.6s ease-out infinite}}
.v-warning .dot{{background:var(--warn);color:var(--warn)}}
.v-critical .dot{{background:var(--fault);color:var(--fault);animation:none}}
@keyframes pulse{{0%{{box-shadow:0 0 0 0 currentColor;opacity:1}}
  70%{{box-shadow:0 0 0 9px transparent;opacity:.75}}100%{{box-shadow:0 0 0 0 transparent;opacity:1}}}}
.verdict__copy{{font-weight:640;letter-spacing:-.01em}}
.verdict__meta{{margin-left:auto;font-family:var(--mono);font-size:12px;color:var(--mute)}}

.hero{{padding:56px 0 44px;border-bottom:1px solid var(--rule)}}
.eyebrow{{margin:0 0 14px;font-family:var(--mono);font-size:11px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--mute)}}
.ticker{{margin:0;font-family:var(--mono);font-weight:600;letter-spacing:-.03em;
  font-size:clamp(40px,9vw,86px);line-height:1;font-variant-numeric:tabular-nums}}
.ticker .cur{{color:var(--flow);margin-right:.08em}}
.ticker .acc__frac{{font-size:.44em;color:var(--mute);letter-spacing:-.01em}}
.hero__sub{{margin:18px 0 0;font-size:14.5px;color:var(--mute);max-width:60ch}}
.hero__sub b{{color:var(--ink);font-weight:600}}

section{{padding:36px 0;border-bottom:1px solid var(--rule)}}
h2{{margin:0 0 20px;font-family:var(--mono);font-size:11px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--mute);font-weight:500}}
h2 + h2{{margin-top:34px}}

ul{{list-style:none;margin:0;padding:0}}
.stream{{display:grid;grid-template-columns:minmax(140px,1.3fr) minmax(60px,1.5fr) auto auto;
  gap:16px;align-items:center;padding:11px 0;border-top:1px solid var(--rule)}}
.stream:first-child{{border-top:0}}
.stream__id{{display:flex;flex-direction:column;gap:1px;min-width:0}}
.stream__name{{font-weight:580;font-size:15px}}
.stream__addr{{font-family:var(--mono);font-size:11px;color:var(--mute);text-decoration:none}}
.stream__addr:hover{{color:var(--flow);text-decoration:underline}}
.stream__bar{{height:5px;background:var(--rule);border-radius:3px;overflow:hidden}}
.stream__bar span{{display:block;height:100%;background:var(--flow);border-radius:3px}}
.stream--fault .stream__bar span{{background:var(--fault)}}
.stream__rate{{font-family:var(--mono);font-size:12.5px;color:var(--mute);text-align:right;white-space:nowrap}}
.stream__rate b{{color:var(--ink);font-weight:600}}
.stream__acc{{font-family:var(--mono);font-size:15px;font-variant-numeric:tabular-nums;
  text-align:right;min-width:104px;white-space:nowrap}}
.stream__acc::before{{content:"$";color:var(--flow)}}
.stream__acc .acc__frac{{font-size:.76em;color:var(--mute)}}
.stream--fault .stream__acc{{color:var(--fault)}}
.stream--fault .stream__acc::before{{color:var(--fault)}}

.check{{display:flex;gap:16px;align-items:baseline;padding:10px 0;border-top:1px solid var(--rule)}}
.check:first-child{{border-top:0}}
.check__label{{font-size:14.5px}}
.check__val{{margin-left:auto;font-family:var(--mono);font-size:13px}}
.check--ok .check__val{{color:var(--ok)}}
.check--fault .check__val{{color:var(--fault);font-weight:600}}
.faults{{margin:0 0 22px;padding:14px 16px;border-left:3px solid var(--fault);
  background:rgba(194,51,27,.07);font-size:14px}}
.faults li{{margin:3px 0}}

footer{{padding:30px 0 60px;font-family:var(--mono);font-size:11.5px;
  color:var(--mute);line-height:1.85;border-bottom:0}}
footer a{{color:var(--mute)}}
footer p{{margin:0 0 8px;max-width:78ch}}

@media (max-width:660px){{
  .stream{{grid-template-columns:1fr auto;gap:6px 12px}}
  .stream__bar{{grid-column:1/-1;order:3}}
  .stream__rate{{order:2}}
  .stream__acc{{order:4;grid-column:1/-1;text-align:left;font-size:14px}}
  .verdict__meta{{margin-left:0;width:100%}}
}}
@media (prefers-reduced-motion:reduce){{
  .dot{{animation:none}}
}}
</style>
</head>
<body>

<header class="verdict v-{verdict}">
  <div class="wrap">
    <span class="dot" aria-hidden="true"></span>
    <span class="verdict__copy">{verdict_copy}</span>
    <span class="verdict__meta">block {block} &middot; checked {age} min ago</span>
  </div>
</header>

<main class="wrap">

  <div class="hero">
    <p class="eyebrow">Delivered to the SPP3 cohort</p>
    <p class="ticker" id="total" data-rate="{cohort_rate}" data-since="{epoch}">
      <span class="cur">$</span><span class="acc__whole">{delivered_whole}</span><span
        class="acc__frac">.{delivered_frac}</span>
    </p>
    <p class="hero__sub">Streaming continuously since <b>{epoch_h}</b>, {days} days ago, at
      <b>${cohort_yr}/yr</b> across {n_cohort} providers. Every figure below is read
      from Ethereum mainnet, not reported by the providers.</p>
  </div>

  {faults}

  <section>
    <h2>SPP3 cohort</h2>
    <ul>{cohort_rows}</ul>
    <h2>Continuing SPP2 streams</h2>
    <ul>{continuing_rows}</ul>
    <h2>Committee</h2>
    <ul>{committee_rows}</ul>
  </section>

  <section>
    <h2>Integrity checks</h2>
    <ul>{checks}</ul>
  </section>

  <footer>
    <p>Rates are compared as Superfluid <code>wei/s</code> integers against the rates
      ratified in EP&nbsp;6.49, never as reconstructed dollar figures. Amounts shown in
      dollars are a gloss on the integer rate.</p>
    <p>Checked daily against the Stream Management Pod. Source and method:
      <a href="https://github.com/SovereignSignal/spp3-accountability">spp3-accountability</a>.
      Built and operated by sovereignsignal.eth for the ENS SPP3 committee.</p>
  </footer>

</main>

<script>
(function () {{
  var SERVER_NOW = {now};
  var t0 = performance.now();
  var nodes = [].slice.call(document.querySelectorAll('[data-rate][data-since]'));
  if (!nodes.length) return;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function paint() {{
    var now = SERVER_NOW + (performance.now() - t0) / 1000;
    for (var i = 0; i < nodes.length; i++) {{
      var el = nodes[i];
      var rate = Number(el.getAttribute('data-rate'));
      var since = Number(el.getAttribute('data-since'));
      if (!rate || !since) continue;
      var v = rate * Math.max(0, now - since) / 1e18;
      var whole = Math.floor(v);
      el.querySelector('.acc__whole').textContent = whole.toLocaleString('en-US');
      el.querySelector('.acc__frac').textContent =
        '.' + String(Math.floor((v - whole) * 100)).padStart(2, '0');
    }}
  }}

  paint();
  if (reduce) {{ setInterval(paint, 1000); }}
  else {{ (function loop() {{ paint(); requestAnimationFrame(loop); }})(); }}
}})();
</script>
</body>
</html>
"""
