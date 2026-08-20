"""render.py — ENS SPP3 cohort accountability tracker.

Tracks the four providers the DAO is actually funding: what they were funded to
do, what money has reached them, what they have committed to, and whether their
reports have landed.

  /                     overview: one card per provider, next obligation
  /provider/<slug>      scope, funding, commitments, reports for one provider
  /streams              every stream on the pod, integrity checks, runway
  /reports              the reporting calendar and who owes what by when
  /calendar             cohort obligations through the end of term

Deliberately out of scope: the Marketplace RFP. That is a selection process
still in flight, not an accountability record of funded work.

Two rules hold everywhere and are enforced by tests:
  - Live figures come from (rate x elapsed) against the explicit program epoch,
    never a stream's own lastUpdated. See spp3_stream_start in providers.json.
  - Cohort membership is decided by the chain, not the committee board, which
    still lists a provider who declined.
"""
import calendar as _cal
import html
import time as _time

SECONDS_PER_YEAR = 31_536_000

NAV = [("/", "Overview"), ("/providers", "Providers"), ("/streams", "Streams"),
       ("/reports", "Reports"), ("/calendar", "Calendar")]

# Each provider gets an identity hue, used on its card, its flow edge and its
# page. Deliberately jewel-toned and cool so none of them collides with the
# semantic colours (green ok / amber warning / vermilion fault).
ACCENT = {
    "namespace": "#6B4FE8",
    "goldsky": "#0E8A7E",
    "unruggable": "#1B5CF0",
    "fluidkey": "#A8399B",
}
ACCENT_FALLBACK = "#1B5CF0"


def accent(slug):
    return ACCENT.get(slug, ACCENT_FALLBACK)


VERDICT_COPY = {
    "healthy": "All streams flowing",
    "warning": "Streams flowing, funding needs attention",
    "critical": "Stream fault",
}


# ---------------------------------------------------------------- helpers

def _esc(s):
    return html.escape(str(s))


def _usd(wei_s):
    return wei_s * SECONDS_PER_YEAR / 10**18


def _money(n, dp=0):
    return "{:,.{dp}f}".format(n, dp=dp)


def _short(a):
    return a[:6] + "…" + a[-4:]


def _fmt_short(ts):
    return _time.strftime("%d %b %Y", _time.gmtime(ts))


def _fmt_utc(ts):
    return _time.strftime("%d %b %Y %H:%M UTC", _time.gmtime(ts))


def _parse_iso(s):
    try:
        return _cal.timegm(_time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0


def _days_to(datestr, now):
    try:
        return (_cal.timegm(_time.strptime(datestr, "%Y-%m-%d")) - now) / 86400.0
    except (ValueError, TypeError):
        return None


def _when(days):
    if days is None:
        return ""
    if days < 0:
        return "overdue by %d days" % abs(round(days))
    return "in %d days" % round(days) if days >= 1 else "today"


def _ticker(rate, since, cls="tick"):
    return ('<span class="%s" data-rate="%d" data-since="%d">'
            '<span class="acc__whole">0</span><span class="acc__frac">.00</span>'
            '</span>' % (cls, rate, since))


def _flow_diagram(ctx):
    """The pod topology, drawn.

    Timelock funds the pod; the pod funds each provider. Edge thickness is the
    flowrate and the dash animation runs faster on a bigger stream, so the
    picture carries the same information as the table instead of decorating it.
    A stopped stream loses its motion, which is the failure mode made visible.
    """
    funded = _funded(ctx)
    if not funded:
        return ""
    by_slug = {s["slug"]: s for s in ctx["status"]["streams"]}
    master = ctx["providers"]["master_stream_wei_s"]
    rates = [by_slug.get(p["slug"], {}).get("actual_wei_s", 0) for p in funded]
    top = max(rates + [1])

    W, H = 920, 300
    x_tl, x_pod, x_pr = 74, 340, 726
    y_mid = H / 2
    step = (H - 96) / max(len(funded) - 1, 1)

    edges, nodes, defs = [], [], []
    # timelock -> pod
    edges.append(
        '<path class="fl fl--master" d="M %d %d C %d %d, %d %d, %d %d" '
        'stroke-width="14"/>' % (x_tl + 46, y_mid, x_tl + 150, y_mid,
                                x_pod - 150, y_mid, x_pod - 52, y_mid))
    for i, p in enumerate(funded):
        y = 48 + i * step
        s_ = by_slug.get(p["slug"], {})
        r = s_.get("actual_wei_s", 0)
        w = 2.5 + (r / top) * 8.0
        dur = 5.5 - (r / top) * 3.0          # bigger stream, faster dashes
        stalled = "" if s_.get("ok") else " fl--stalled"
        edges.append(
            '<path class="fl%s" d="M %d %d C %d %d, %d %d, %d %d" '
            'stroke="%s" stroke-width="%.1f" style="--dur:%.2fs"/>' % (
                stalled, x_pod + 52, y_mid, x_pod + 190, y_mid,
                x_pr - 190, y, x_pr - 34, y, accent(p["slug"]), w, dur))
        nodes.append(
            '<g class="node node--pr"><circle cx="%d" cy="%.1f" r="7" fill="%s"/>'
            '<text x="%d" y="%.1f" class="lbl">%s</text>'
            '<text x="%d" y="%.1f" class="sub">$%s/yr</text></g>' % (
                x_pr, y, accent(p["slug"]),
                x_pr + 18, y - 1, _esc(p["name"]),
                x_pr + 18, y + 14, _money(_usd(r))))

    nodes.insert(0,
                 '<g class="node"><rect x="%d" y="%d" width="92" height="46" rx="9" '
                 'class="box"/><text x="%d" y="%.1f" class="lbl lbl--c">Timelock</text>'
                 '<text x="%d" y="%.1f" class="sub sub--c">DAO treasury</text></g>'
                 % (x_tl - 46, y_mid - 23, x_tl, y_mid - 2, x_tl, y_mid + 14))
    nodes.insert(1,
                 '<g class="node"><rect x="%d" y="%d" width="104" height="52" rx="10" '
                 'class="box box--pod"/><text x="%d" y="%.1f" class="lbl lbl--c">Stream Pod</text>'
                 '<text x="%d" y="%.1f" class="sub sub--c">$%s/yr in</text></g>'
                 % (x_pod - 52, y_mid - 26, x_pod, y_mid - 2, x_pod, y_mid + 15,
                    _money(_usd(master))))

    return ('<div class="flowwrap"><svg class="flow" viewBox="0 0 %d %d" '
            'preserveAspectRatio="xMidYMid meet" role="img" '
            'aria-label="Funding flows from the DAO timelock through the Stream '
            'Management Pod to each funded provider.">%s%s%s</svg></div>' % (
                W, H, "".join(defs), "".join(edges), "".join(nodes)))


def _funded(ctx):
    """The cohort, per the chain. The committee board still lists EthID as
    cohort-selected although they declined on 2026-07-03; rendering that list
    published a provider as funded who is not."""
    return [p for p in ctx["providers"].get("providers", [])
            if p.get("cohort") == "spp3"]


def _stream_for(ctx, slug):
    for s in ctx["status"]["streams"]:
        if s["slug"] == slug:
            return s
    return None


def _commit(ctx, slug):
    return ctx["commitments"].get("providers", {}).get(slug, {})


def _next_quarter(ctx):
    for q in ctx["commitments"].get("quarters", []):
        d = _days_to(q["report_due"], ctx["now"])
        if d is not None and d >= 0:
            return q, d
    return None, None


def _ghosts(ctx):
    names = {p["name"] for p in _funded(ctx)}
    return [r["name"] for r in ctx["board"].get("pipeline", [])
            if r.get("status") == "Cohort selected" and r["name"] not in names]


# ---------------------------------------------------------------- pages

def _term_timeline(ctx):
    """The twelve-month term as a line: quarter ends below, report due dates
    above, today marked. Static and money-free by design; the class is `term`,
    never `flow`, so the overview-is-static tests can tell them apart.
    """
    def ts(d):
        return _cal.timegm(_time.strptime(d, "%Y-%m-%d"))

    t0, t1 = ts("2026-08-01"), ts("2027-07-31")
    W, H, x0, x1, y = 920, 138, 34, 886, 78

    def X(t):
        f = (t - t0) / (t1 - t0)
        return x0 + max(0.0, min(1.0, f)) * (x1 - x0)

    xn = X(ctx["now"])
    parts = [
        '<line class="tl-base" x1="%d" y1="%d" x2="%d" y2="%d"/>' % (x0, y, x1, y),
        '<line class="tl-done" x1="%d" y1="%d" x2="%.1f" y2="%d"/>' % (x0, y, xn, y),
    ]
    for d, lbl in [("2026-09-30", "Q3"), ("2026-12-31", "Q4"),
                   ("2027-03-31", "Q1"), ("2027-06-30", "Q2")]:
        x = X(ts(d))
        parts.append('<line class="tl-tick" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>'
                     % (x, y - 5, x, y + 9))
        parts.append('<text class="tl-lbl" x="%.1f" y="%d">%s ends</text>'
                     % (x, y + 27, lbl))
    for d in ("2026-10-30", "2027-01-30", "2027-04-30", "2027-07-30"):
        t = ts(d)
        x = X(t)
        parts.append('<path class="tl-due" d="M %.1f %d l 5 6 l -5 6 l -5 -6 z"/>'
                     % (x, y - 28))
        parts.append('<text class="tl-lbl tl-lbl--due" x="%.1f" y="%d">%s</text>'
                     % (x, y - 35, _time.strftime("%d %b", _time.gmtime(t))))
    parts.append('<text class="tl-cap" x="%d" y="%d">Aug 2026</text>' % (x0, y + 27))
    parts.append('<text class="tl-cap tl-cap--end" x="%d" y="%d">Jul 2027</text>'
                 % (x1, y + 27))
    parts.append('<line class="tl-now" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>'
                 % (xn, y - 14, xn, y + 14))
    parts.append('<circle class="tl-nowdot" cx="%.1f" cy="%d" r="4"/>' % (xn, y))
    parts.append('<text class="tl-nowlbl" x="%.1f" y="%d">today</text>'
                 % (xn, y + 44))
    return ('<div class="termwrap"><svg class="term" viewBox="0 0 %d %d" '
            'preserveAspectRatio="xMidYMid meet" role="img" aria-label="The '
            'SPP3 term from August 2026 to July 2027, with quarterly report '
            'due dates and today marked.">%s</svg>'
            '<p class="colnote">Amber diamonds are quarterly report due dates, '
            '30 days after each quarter ends.</p></div>' % (W, H, "".join(parts)))


def page_home(ctx):
    """A plain program introduction, but not a gray one: the term timeline is
    the hero graphic (program detail, no money), and the cohort wears its
    provider hues. No tickers, no stream state; each page owns its own data.
    """
    funded = _funded(ctx)
    total = sum(p["award_usd"] for p in funded)
    q, _qd = _next_quarter(ctx)

    cohort_cards = "\n".join(
        '<a class="card card--ok" href="/provider/%s" style="--accent:%s">'
        '<span class="card__label">%s</span>'
        '<span class="card__amt">$%s<i>/yr</i></span>'
        '<span class="card__detail">%s</span></a>' % (
            _esc(p["slug"]), accent(p["slug"]), _esc(p["name"]),
            _money(p["award_usd"]),
            _esc((_commit(ctx, p["slug"]).get("scope") or "")[:150]))
        for p in funded)

    sections = [
        ("/streams", "Streams", "Every payment stream, checked daily against "
         "Ethereum mainnet at the rates ratified in EP 6.49."),
        ("/providers", "Providers", "Each provider's scope, funding, and "
         "proposed commitments."),
        ("/reports", "Reports", "The quarterly reporting calendar and what has "
         "actually been filed on the ENS Forum."),
        ("/calendar", "Calendar", "Cohort obligations through the end of the "
         "term, 31 July 2027."),
    ]
    site_cards = "\n".join(
        '<a class="card card--ok" href="%s">'
        '<span class="card__label">%s</span>'
        '<span class="card__detail">%s</span></a>' % (href, _esc(t), _esc(d))
        for href, t, d in sections)

    return (
        '<div class="hero hero--home">'
        '<p class="eyebrow">ENS Service Provider Program &middot; Season 3</p>'
        '<h2 class="lead">The public record of the SPP3 cohort.</h2>'
        '<p class="hero__sub">SPP3 was authorized by <a href="https://discuss.ens.'
        'domains/t/22086" target="_blank" rel="noopener">EP&nbsp;6.42</a> and its '
        'cohort ratified on-chain by <a href="https://discuss.ens.domains/t/22237" '
        'target="_blank" rel="noopener">EP&nbsp;6.49</a>: <b>$%s a year</b> across '
        'four providers on a twelve-month term. Providers owe public quarterly '
        'reports on the ENS Forum; funding flows as continuous streams the DAO '
        'can verify on-chain. Nothing on this site is self-reported by the '
        'providers.</p>'
        '%s'
        '<div class="factrow">'
        '<div><i>Term</i><b>1 Aug 2026 &ndash; 31 Jul 2027</b></div>'
        '<div><i>Next obligation</i><b>%s</b></div>'
        '<div><i>Committee</i><b>coltron.eth (Chair), sovereignsignal.eth, '
        'austingriffith.eth, abdullahumar.eth, gregskril.eth</b></div>'
        '</div>'
        '</div>'
        '<section><h2>The cohort</h2><div class="cards cards--cohort">%s</div>'
        '</section>'
        '<section><h2>On this site</h2><div class="cards">%s</div></section>' % (
            _money(total), _term_timeline(ctx),
            _esc("Quarterly Reports for %s, due 30 Oct 2026" % q["quarter"])
            if q else "term reconciliation",
            cohort_cards, site_cards))


def page_providers(ctx):
    ghosts = _ghosts(ctx)
    drift = ""
    if ghosts:
        drift = ('<p class="drift"><b>Board drift:</b> the committee pipeline still '
                 'lists %s as cohort-selected, but there is no funded stream. EthID '
                 'declined publicly on 3 July 2026. The chain is authoritative here.</p>'
                 % _esc(", ".join(ghosts)))
    rows = []
    for p in _funded(ctx):
        s = _stream_for(ctx, p["slug"]) or {}
        c = _commit(ctx, p["slug"])
        rows.append(
            '<li class="app app--%s"><span class="app__name">'
            '<a href="/provider/%s">%s</a></span>'
            '<span class="app__req">$%s</span>'
            '<span class="app__gate">%s</span>'
            '<span class="app__state">%s</span>'
            '<span class="app__score">%s</span></li>' % (
                "ok" if s.get("ok") else "out", _esc(p["slug"]), _esc(p["name"]),
                _money(p["award_usd"]),
                "live" if s.get("ok") else "FAULT",
                ("%d proposed" % len(c.get("milestones", [])))
                if c.get("milestones") else "not recorded",
                "30 Oct 2026"))
    return ('<p class="lede">Four providers ratified by EP&nbsp;6.49 and funded '
            'on-chain since 1 August 2026.</p>' + drift +
            '<section><ul class="apps"><li class="app app--head">'
            '<span>Provider</span><span>Award</span><span>Stream</span>'
            '<span>Commitments</span><span>First report</span></li>%s</ul></section>'
            % "\n".join(rows))


def page_provider(ctx, slug):
    p = next((x for x in _funded(ctx) if x["slug"] == slug), None)
    if not p:
        return None
    s = _stream_for(ctx, slug) or {}
    c = _commit(ctx, slug)
    epoch = ctx["providers"]["spp3_stream_start"]
    rec = {r["name"]: r for r in ctx["board"].get("pipeline", [])}.get(p["name"], {})

    facts = [
        ("Award", "$%s / year" % _money(p["award_usd"])),
        ("Categories", ", ".join(str(x) for x in p.get("categories", [])) or "&mdash;"),
        ("Team status", _esc(rec.get("team_status") or "&mdash;")),
        ("Approved wallet",
         '<a href="https://etherscan.io/address/%s" target="_blank" rel="noopener">%s</a>'
         % (_esc(p["approved_wallet"]), _esc(_short(p["approved_wallet"])))),
        ("Stream", ("running at the ratified rate" if s.get("ok")
                    else "<b>FAULT: %s</b>" % _esc(s.get("state", "unknown")))),
        ("Flow opened", _fmt_short(s["since"]) if s.get("since") else "&mdash;"),
    ]
    if p.get("recusals"):
        facts.append(("Recusals", _esc(", ".join(p["recusals"]))
                      + " &mdash; another member signs off"))

    ext = ""
    if c.get("external_note"):
        ext = ('<p class="drift drift--info"><b>External dependency:</b> %s</p>'
               % _esc(c["external_note"]))

    ms = c.get("milestones", [])
    if ms:
        by_q = {}
        for m in ms:
            by_q.setdefault(m.get("target_quarter") or "No date stated", []).append(m)
        blocks = []
        for q in sorted(by_q, key=lambda k: (k == "No date stated", k)):
            rows = "".join(
                '<li class="ms"><span class="ms__title">%s</span>'
                '<span class="ms__kpi">%s</span>'
                '<span class="ms__state">%s</span></li>' % (
                    _esc(m.get("title", "")),
                    _esc(" &middot; ".join(m.get("kpis") or [])) or "&mdash;",
                    _esc(m.get("status", "not started")))
                for m in by_q[q])
            blocks.append('<div class="qgroup"><h3 class="qgroup__h">%s '
                          '<span>%d</span></h3><ul>%s</ul></div>'
                          % (_esc(q), len(by_q[q]), rows))
        src = c.get("milestones_source_url")
        milestones = (
            '<p class="drift"><b>Provisional, not confirmed.</b> These %d milestones '
            'were extracted from the provider\'s own SPP3 <a href="%s" target="_blank" '
            'rel="noopener">application</a> and are what they <em>proposed</em>. The '
            'binding set is Award Notice Item 5, which is not yet in the committee '
            'workspace. Every entry was checked against a verbatim quote from the '
            'source document; none is committee-confirmed, and none counts toward the '
            '80%% completion metric until reconciled.</p>%s' % (
                len(ms), _esc(src or "#"), "".join(blocks)))
    else:
        milestones = (
            '<p class="empty">No commitments recorded yet.</p>'
            '<p class="colnote">Milestones and KPIs come from Award Notice Item 5, '
            'which is not yet in the committee workspace. Until they land, the 80% '
            'milestone-completion rate the committee is bound to report has no '
            'denominator. An empty list here is an accurate statement of what is '
            'known, not a placeholder.</p>')

    reports = c.get("reports", [])
    if reports:
        rrows = "".join('<li class="app"><span class="app__name">%s</span>'
                        '<span class="app__gate">%s</span></li>'
                        % (_esc(r.get("quarter", "")), _esc(r.get("url", "")))
                        for r in reports)
        reports_html = '<ul class="apps">%s</ul>' % rrows
    else:
        q, qdays = _next_quarter(ctx)
        reports_html = ('<p class="empty">No reports filed.</p>'
                        '<p class="colnote">First Quarterly Report covers %s and is due '
                        '%s, %s. Not overdue.</p>' % (
                            _esc(q["quarter"]), _esc(q["report_due"]), _when(qdays))
                        if q else '<p class="empty">No reports filed.</p>')

    return (
        '<p class="lede">%s</p>'
        '<div class="hero hero--sm"><p class="eyebrow">Delivered to %s</p>'
        '<p class="ticker ticker--sm">%s</p></div>'
        '<section><h2>Funding</h2><dl class="facts">%s</dl></section>'
        '%s'
        '<section><h2>Why the committee funded this</h2><p class="prose">%s</p>'
        '<p class="prose prose--dim">%s</p></section>'
        '<section><h2>Commitments</h2>%s</section>'
        '<section><h2>Reports</h2>%s</section>' % (
            _esc(c.get("scope", "")), _esc(p["name"]),
            _ticker(s.get("actual_wei_s", 0), epoch, "tick tick--hero"),
            "".join("<dt>%s</dt><dd>%s</dd>" % (k, v) for k, v in facts),
            ext,
            _esc(c.get("why_funded", "")), _esc(c.get("watch", "")),
            milestones, reports_html))


def page_streams(ctx):
    st = ctx["status"]
    epoch = ctx["providers"]["spp3_stream_start"]
    max_rate = max([s["actual_wei_s"] for s in st["streams"]] or [1])
    body = ['<p class="lede">Every stream the Stream Management Pod runs, compared '
            'against the rates ratified in EP&nbsp;6.49. Amounts are delivered since '
            'the 1 Aug 2026 switch so rows stay comparable; a provider whose rate was '
            'unchanged across cycles kept the same uninterrupted stream, noted beside '
            'its address.</p>']
    body.append('<section><h2>Where the money goes</h2>%s</section>'
                % _flow_diagram(ctx))

    for label, key in [("Cohort", "spp3"), ("Continuing SPP2", "spp2-continuing"),
                       ("Committee", "committee")]:
        rows = [s for s in st["streams"] if s["cohort"] == key]
        if not rows:
            continue
        out = []
        for s in rows:
            pct = s["actual_wei_s"] / max_rate * 100 if max_rate else 0
            fs = s.get("since", 0)
            out.append(
                '<li class="stream stream--%s"><div class="stream__id">'
                '<span class="stream__name">%s</span><span class="stream__meta">'
                '<a href="https://etherscan.io/address/%s" target="_blank" '
                'rel="noopener">%s</a>%s</span></div>'
                '<div class="stream__bar"><span style="width:%.2f%%"></span></div>'
                '<div class="stream__rate"><b>$%s</b><span>/yr</span></div>'
                '<div class="stream__acc">%s</div></li>' % (
                    "ok" if s["ok"] else "fault", _esc(s["name"]),
                    _esc(s["address"]), _esc(_short(s["address"])),
                    (" &middot; flowing since " + _fmt_short(fs)) if fs else "",
                    pct, _money(_usd(s["expected_wei_s"])),
                    _ticker(s["actual_wei_s"], max(fs, epoch), "tick tick--row")))
        body.append('<section><h2>%s</h2><ul>%s</ul></section>'
                    % (_esc(label), "\n".join(out)))

    retired = st.get("retired", [])
    if retired:
        body.append('<section><h2>Retired SPP2 streams</h2><ul>%s</ul>'
                    '<p class="colnote">A retired stream still running would mean the '
                    'DAO is paying someone it stopped funding.</p></section>' % "".join(
                        '<li class="check check--%s"><span class="check__label">%s</span>'
                        '<span class="check__val">%s</span></li>' % (
                            "ok" if r["ok"] else "fault", _esc(r["name"]),
                            "stopped" if r["ok"]
                            else "STILL STREAMING %d wei/s" % r["actual_wei_s"])
                        for r in retired))

    net, run = st["net_flow"], st["runway"]
    checks = [
        ("Unaccounted flow on the pod", "%d wei/s" % net["unaccounted_wei_s"], net["ok"],
         "The pod's net flowrate must equal master inflow minus known outflows. "
         "Checking only known receivers is blind to a receiver nobody recorded."),
        ("Funding runway", "%s days" % _money(run["combined_days"]), run["ok"],
         "Timelock USDCx plus the USDC autowrap converts, against the master stream's "
         "daily draw. SPP2's streams once ran dead for 33.7 days before anyone noticed."),
    ]
    body.append('<section><h2>Integrity checks</h2><ul>%s</ul></section>' % "".join(
        '<li class="check check--%s"><span class="check__label">%s'
        '<span class="check__why">%s</span></span><span class="check__val">%s</span>'
        '</li>' % ("ok" if ok else "fault", _esc(l), _esc(why), _esc(v))
        for l, v, ok, why in checks))
    return "\n".join(body)


def page_reports(ctx):
    funded = _funded(ctx)
    rows = []
    for q in ctx["commitments"].get("quarters", []):
        d = _days_to(q["report_due"], ctx["now"])
        filed = sum(1 for p in funded
                    if any(r.get("quarter") == q["quarter"]
                           for r in _commit(ctx, p["slug"]).get("reports", [])))
        if d is not None and d < 0 and filed < len(funded):
            state, val = "fault", "%d of %d filed &middot; OVERDUE" % (filed, len(funded))
        elif filed == len(funded):
            state, val = "ok", "%d of %d filed" % (filed, len(funded))
        else:
            state, val = "wait", "%d of %d filed &middot; %s" % (filed, len(funded), _when(d))
        rows.append(
            '<li class="check check--%s"><span class="check__label">%s'
            '<span class="check__why">quarter ends %s, report due %s</span></span>'
            '<span class="check__val">%s</span></li>' % (
                state, _esc(q["quarter"]), _esc(q["ends"]), _esc(q["report_due"]), val))
    return (
        '<p class="lede">Each provider owes a Quarterly Report within 30 days of each '
        'calendar quarter end, covering progress against the KPIs in their Award '
        'Notice, fees received, and how those fees were applied. A public version on '
        'the ENS Forum is required by <b>Program Terms clause 6.3</b>, not offered as '
        'a courtesy.</p>'
        '<section><h2>Reporting calendar</h2><ul>%s</ul>'
        '<p class="colnote">Calendar quarters, matching the ensdao/spp convention. '
        'SPP2 counted quarters from program start in places and from the calendar in '
        'others, and the resulting due-date confusion played out in public. Nothing is '
        'overdue: the first window opens 30 September 2026.</p></section>'
        % "\n".join(rows))


def page_calendar(ctx):
    nxt = None
    for m in ctx["calendar"].get("milestones", []):
        d = _days_to(m["date"], ctx["now"])
        if not (m.get("done") or (d is not None and d < 0)):
            nxt = m
            break
    items = []
    for m in ctx["calendar"].get("milestones", []):
        if m.get("track") == "rfp":
            continue
        d = _days_to(m["date"], ctx["now"])
        past = m.get("done") or (d is not None and d < 0)
        items.append(
            '<li class="mile mile--%s%s"><span class="mile__date">%s</span>'
            '<span class="mile__label">%s</span><span class="mile__track">%s</span>'
            '<span class="mile__when">%s</span></li>' % (
                "past" if past else m.get("track", ""),
                " mile--next" if (nxt is m) else "", _esc(m["date"]),
                _esc(m["label"]), _esc(m.get("track", "")),
                "" if past else _when(d)))
    return ('<p class="lede">Cohort obligations through the end of term, fixed by '
            'EP&nbsp;6.49 and Program Terms clauses 4.4 and 6.1&ndash;6.5. '
            'Marketplace RFP dates are tracked separately and are not shown here.</p>'
            '<section><ul class="miles">%s</ul></section>' % "\n".join(items))


ROUTES = {
    "/": ("Overview", page_home),
    "/providers": ("Providers", page_providers),
    "/streams": ("Streams", page_streams),
    "/reports": ("Reports", page_reports),
    "/calendar": ("Calendar", page_calendar),
}


# ---------------------------------------------------------------- shell

def _nav(active):
    return ('<nav class="nav"><div class="wrap">'
            '<a class="nav__mark" href="/">ENS SPP3 <span>accountability</span></a>'
            '<div class="nav__links">%s</div></div></nav>' % "".join(
                '<a class="nav__link%s" href="%s">%s</a>' % (
                    " is-active" if h == active else "", h, _esc(l))
                for h, l in NAV))


# The check runs daily at 15:00 UTC, so a healthy page is under ~24h old.
# Past STALE_HOURS either the run or the deploy that carries it has failed, and
# the verdict on screen has stopped being a statement about now.
STALE_HOURS = 30


def _age_hours(ctx):
    return max(0.0, (ctx["now"] - _parse_iso(ctx["status"]["checked_at"])) / 3600.0)


def _age_text(hours):
    if hours < 2:
        return "%d min ago" % round(hours * 60)
    if hours < 48:
        return "%d hours ago" % round(hours)
    return "%.1f days ago" % (hours / 24)


def _verdict(ctx):
    """The verdict bar, which must never assert a live state from dead data.

    A failed deploy or a dead cron leaves the last good page serving, and
    "All streams flowing" then reads as current when it is a day old. Past
    STALE_HOURS the bar says that instead: the age becomes the headline and the
    stream verdict is demoted to what it actually is, a past observation.
    """
    st = ctx["status"]
    hours = _age_hours(ctx)
    age_txt = _age_text(hours)

    if hours > STALE_HOURS:
        copy = "Data is %s and may not reflect the chain" % age_txt
        cls = "stale"
        meta = "last verdict: %s &middot; block %s" % (
            _esc(VERDICT_COPY.get(st["overall"], st["overall"])).lower(),
            "{:,}".format(st["block_number"]))
    else:
        copy = _esc(VERDICT_COPY.get(st["overall"], st["overall"]))
        cls = st["overall"]
        meta = "block %s &middot; checked %s" % (
            "{:,}".format(st["block_number"]), age_txt)

    return ('<header class="verdict v-%s"><div class="wrap">'
            '<span class="dot" aria-hidden="true"></span>'
            '<span class="verdict__copy">%s</span>'
            '<span class="verdict__meta">%s</span>'
            '</div></header>' % (cls, copy, meta))


def render(ctx, path="/"):
    """Dispatch. Returns None for an unknown path so the server can 404."""
    if path.startswith("/provider/"):
        slug = path[len("/provider/"):].strip("/")
        body = page_provider(ctx, slug)
        if body is None:
            return None
        p = next(x for x in _funded(ctx) if x["slug"] == slug)
        title, active = p["name"], "/providers"
        main_style = ' style="--accent:%s;--flow:%s"' % (accent(slug), accent(slug))
    elif path in ROUTES:
        title, builder = ROUTES[path]
        body, active = builder(ctx), path
        main_style = ""
    else:
        return None

    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
            "<title>" + _esc(title) + " · ENS SPP3 accountability</title>\n"
            "<meta name=\"description\" content=\"Accountability tracker for the ENS "
            "SPP3 service provider cohort: funding verified on-chain, commitments, and "
            "quarterly reporting.\">\n<style>" + CSS + "</style>\n</head>\n<body>\n"
            + _nav(active) + _verdict(ctx)
            + '<main class="wrap"' + main_style + '>'
            + ("" if path == "/" else '<h1 class="title">' + _esc(title) + "</h1>\n")
            + body + FOOTER + "</main>\n<script>"
            + JS.replace("%SERVER_NOW%", repr(ctx["now"])) + "</script>\n</body>\n</html>\n")


CSS = """
:root{--ground:#EDF0F3;--ink:#0E141A;--flow:#1B5CF0;--ok:#0E8A5F;--warn:#B87503;
--fault:#C2331B;--rule:rgba(14,20,26,.14);--mute:rgba(14,20,26,.58);--panel:#fff;
--mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
--sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
--display:"Instrument Serif",Georgia,"Times New Roman",serif;
--accent:#1B5CF0}
@media (prefers-color-scheme:dark){:root{--ground:#0B0F14;--ink:#E6ECF2;--flow:#5B8DFF;
--ok:#3FCF97;--warn:#E0A233;--fault:#FF6A4D;--rule:rgba(230,236,242,.16);
--mute:rgba(230,236,242,.6);--panel:#121820}}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
-webkit-font-smoothing:antialiased;line-height:1.5}
a{color:inherit}
.wrap{max-width:960px;margin:0 auto;padding:0 20px}

.nav{border-bottom:1px solid var(--rule)}
.nav .wrap{display:flex;align-items:center;gap:22px;flex-wrap:wrap;padding:15px 20px}
.nav__mark{font-family:var(--mono);font-size:13px;font-weight:600;text-decoration:none}
.nav__mark span{color:var(--mute);font-weight:400}
.nav__links{display:flex;gap:20px;margin-left:auto;flex-wrap:wrap}
.nav__link{font-size:13.5px;text-decoration:none;color:var(--mute);padding-bottom:2px;
border-bottom:2px solid transparent}
.nav__link:hover{color:var(--ink)}
.nav__link.is-active{color:var(--ink);border-bottom-color:var(--flow);font-weight:560}

.verdict{border-bottom:1px solid var(--rule);padding:12px 0;background:var(--panel)}
.verdict .wrap{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.dot{width:9px;height:9px;border-radius:50%;background:var(--ok);flex:none;color:var(--ok);
animation:pulse 2.6s ease-out infinite}
.v-warning .dot{background:var(--warn);color:var(--warn)}
.v-critical .dot{background:var(--fault);color:var(--fault);animation:none}
.v-stale{background:rgba(184,117,3,.12);border-bottom-color:var(--warn)}
.v-stale .dot{background:var(--warn);color:var(--warn);animation:none}
.v-stale .verdict__copy{color:var(--warn);font-weight:640}
@keyframes pulse{0%{box-shadow:0 0 0 0 currentColor;opacity:1}
70%{box-shadow:0 0 0 9px transparent;opacity:.75}100%{box-shadow:0 0 0 0 transparent;opacity:1}}
.verdict__copy{font-weight:620;font-size:14.5px}
.verdict__meta{margin-left:auto;font-family:var(--mono);font-size:11.5px;color:var(--mute)}

.title{font-family:var(--display);font-size:clamp(34px,5vw,52px);font-weight:400;
letter-spacing:-.015em;color:var(--ink);margin:36px 0 0;line-height:1.05}
.lede{margin:14px 0 28px;font-size:15px;color:var(--mute);max-width:70ch}
.lede b{color:var(--ink)}
.prose{margin:0 0 12px;font-size:14.5px;max-width:72ch}
.prose--dim{color:var(--mute);font-size:13.5px}
.empty{margin:0;font-size:14.5px;color:var(--mute);font-style:italic}
.big{margin:0;font-size:19px;letter-spacing:-.01em}

.hero{padding:32px 0 38px;border-bottom:1px solid var(--rule)}
.hero--sm{padding:20px 0 24px}
.eyebrow{margin:0 0 12px;font-family:var(--mono);font-size:10.5px;letter-spacing:.16em;
text-transform:uppercase;color:var(--mute)}
.lede em,.hero__sub em{font-family:var(--display);font-style:italic;font-size:1.14em}
.ticker{margin:0;font-family:var(--mono);font-weight:600;letter-spacing:-.03em;
font-size:clamp(38px,8.5vw,78px);line-height:1;font-variant-numeric:tabular-nums}
.ticker--sm{font-size:clamp(30px,6vw,50px)}
.tick--hero::before{content:"$";color:var(--accent);margin-right:.1em;font-size:.52em;
font-weight:400;vertical-align:.16em;opacity:.85}
.tick--hero .acc__frac{font-size:.44em;color:var(--mute)}
.lead{font-family:var(--display);font-weight:400;font-size:clamp(30px,4.6vw,46px);
line-height:1.1;letter-spacing:-.012em;margin:0 0 18px;max-width:24ch;color:var(--ink);
text-transform:none}
.lead::after{content:"";display:none}
.facts--hero{margin:26px 0 0;grid-template-columns:auto 1fr;gap:7px 24px}
.facts__tick{font-family:var(--mono);font-variant-numeric:tabular-nums}
.tick--inline::before{content:"$";color:var(--accent);opacity:.85}
.tick--inline .acc__frac{color:var(--mute);font-size:.85em}
.hero__sub{margin:18px 0 0;font-size:14.5px;color:var(--mute);max-width:64ch}
.hero__sub b{color:var(--ink);font-weight:600}

section{padding:28px 0;border-bottom:1px solid var(--rule)}
h2{margin:0 0 18px;font-family:var(--mono);font-size:10.5px;letter-spacing:.16em;
text-transform:uppercase;color:var(--mute);font-weight:500;display:flex;
align-items:center;gap:12px}
h2::after{content:"";flex:1;height:1px;background:var(--rule)}
ul{list-style:none;margin:0;padding:0}

.flowwrap{overflow-x:auto;margin:0 -4px;padding:0 4px}
.flow{width:100%;min-width:640px;height:auto;display:block}
.flow .fl{fill:none;stroke-linecap:round;opacity:.85;
stroke-dasharray:1 13;animation:drift var(--dur,4s) linear infinite}
.flow .fl--master{stroke:var(--mute);stroke-dasharray:1 15;--dur:5s}
.flow .fl--stalled{animation:none;stroke-dasharray:5 5;opacity:.35}
@keyframes drift{to{stroke-dashoffset:-140}}
.flow .box{fill:var(--panel);stroke:var(--rule);stroke-width:1}
.flow .box--pod{stroke:var(--flow);stroke-width:1.5}
.flow .lbl{font-family:var(--sans);font-size:12.5px;font-weight:600;fill:var(--ink)}
.flow .sub{font-family:var(--mono);font-size:10.5px;fill:var(--mute)}
.flow .lbl--c,.flow .sub--c{text-anchor:middle}

.termwrap{overflow-x:auto;margin:30px -4px 0;padding:0 4px}
.term{width:100%;min-width:640px;height:auto;display:block}
.term .tl-base{stroke:var(--rule);stroke-width:3;stroke-linecap:round}
.term .tl-done{stroke:var(--flow);stroke-width:3;stroke-linecap:round}
.term .tl-tick{stroke:var(--mute);stroke-width:1.5}
.term .tl-due{fill:var(--warn)}
.term .tl-lbl{font-family:var(--mono);font-size:11px;fill:var(--mute);text-anchor:middle}
.term .tl-lbl--due{fill:var(--warn)}
.term .tl-cap{font-family:var(--mono);font-size:11px;fill:var(--mute);text-anchor:start}
.term .tl-cap--end{text-anchor:end}
.term .tl-now{stroke:var(--flow);stroke-width:2}
.term .tl-nowdot{fill:var(--flow)}
.term .tl-nowlbl{font-family:var(--mono);font-size:11px;font-weight:600;fill:var(--flow);
text-anchor:middle}
.termwrap .colnote{margin-top:2px}
.factrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
gap:18px 26px;margin:28px 0 0;padding-top:22px;border-top:1px solid var(--rule)}
.factrow i{display:block;font-style:normal;font-family:var(--mono);font-size:10.5px;
letter-spacing:.12em;text-transform:uppercase;color:var(--mute);margin-bottom:4px}
.factrow b{font-weight:540;font-size:14px;line-height:1.45}
.card__amt{font-family:var(--mono);font-size:22px;font-weight:600;letter-spacing:-.02em;
margin:2px 0;color:var(--ink)}
.card__amt i{font-style:normal;font-size:12px;color:var(--mute);font-weight:400}
.cards--cohort{grid-template-columns:repeat(auto-fit,minmax(270px,1fr))}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:14px}
.card{display:flex;flex-direction:column;gap:4px;padding:17px 17px 15px;
background:var(--panel);border:1px solid var(--rule);border-radius:9px;
text-decoration:none;border-left-width:3px;transition:transform .12s ease}
.card:hover{transform:translateY(-2px)}
.card--ok{border-left-color:var(--accent)}
.card--fault{border-left-color:var(--fault)}
.card:hover{border-color:color-mix(in srgb,var(--accent) 45%,var(--rule));
box-shadow:0 6px 22px -14px var(--accent)}
.card .tick--card::before{color:var(--accent)}
.card__label{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;
text-transform:uppercase;color:var(--mute)}
.card__headline{font-size:17px;font-weight:600}
.tick--card{font-family:var(--mono);font-size:23px;font-weight:600;letter-spacing:-.02em;
font-variant-numeric:tabular-nums;display:block;margin:2px 0}
.tick--card::before{content:"$";color:var(--accent);font-size:.68em;font-weight:400;
vertical-align:.1em;opacity:.85}
.tick--card .acc__frac{font-size:.62em;color:var(--mute)}
.card__detail{font-size:12.5px;color:var(--mute)}
.card__detail--dim{font-size:11.5px;opacity:.8}

.facts{display:grid;grid-template-columns:auto 1fr;gap:8px 22px;margin:0;font-size:14px}
.facts dt{font-family:var(--mono);font-size:11px;letter-spacing:.08em;
text-transform:uppercase;color:var(--mute);padding-top:2px}
.facts dd{margin:0}

.stream{display:grid;grid-template-columns:minmax(140px,1.3fr) minmax(60px,1.4fr) auto auto;
gap:16px;align-items:center;padding:11px 0;border-top:1px solid var(--rule)}
.stream:first-child{border-top:0}
.stream__id{display:flex;flex-direction:column;gap:1px;min-width:0}
.stream__name{font-weight:580;font-size:15px}
.stream__meta{font-family:var(--mono);font-size:11px;color:var(--mute)}
.stream__meta a{color:inherit;text-decoration:none}
.stream__meta a:hover{color:var(--flow);text-decoration:underline}
.stream__bar{height:5px;background:var(--rule);border-radius:3px;overflow:hidden}
.stream__bar span{display:block;height:100%;background:var(--flow);border-radius:3px}
.stream--fault .stream__bar span{background:var(--fault)}
.stream__rate{font-family:var(--mono);font-size:12.5px;color:var(--mute);text-align:right;
white-space:nowrap}
.stream__rate b{color:var(--ink);font-weight:600}
.stream__acc{text-align:right;min-width:104px}
.tick--row{font-family:var(--mono);font-size:15px;font-variant-numeric:tabular-nums;
white-space:nowrap}
.tick--row::before{content:"$";color:var(--flow);font-size:.8em;opacity:.75}
.tick--row .acc__frac{font-size:.76em;color:var(--mute)}
.stream--fault .tick--row,.stream--fault .tick--row::before{color:var(--fault)}

.app{display:grid;grid-template-columns:minmax(120px,2fr) 96px 108px 132px 104px;gap:14px;
align-items:baseline;padding:9px 0;border-top:1px solid var(--rule);font-size:14px}
.app--head{border-top:0;font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;
text-transform:uppercase}
.app--head span{color:var(--mute)}
.app__name{font-weight:560}
.app__name a{text-decoration:none;border-bottom:1px solid var(--rule)}
.app__name a:hover{color:var(--flow);border-bottom-color:var(--flow)}
.app__req,.app__score{font-family:var(--mono);font-size:12.5px;
font-variant-numeric:tabular-nums}
.app__gate,.app__state{font-size:12.5px;color:var(--mute)}
.app--ok .app__gate{color:var(--ok)}
.app--out .app__gate{color:var(--fault);font-weight:600}

.check{display:flex;gap:16px;align-items:baseline;padding:11px 0;
border-top:1px solid var(--rule)}
.check:first-child{border-top:0}
.check__label{font-size:14.5px;display:flex;flex-direction:column;gap:2px}
.check__why{font-size:12.5px;color:var(--mute);max-width:64ch}
.check__val{margin-left:auto;font-family:var(--mono);font-size:12.5px;white-space:nowrap}
.check--ok .check__val{color:var(--ok)}
.check--wait .check__val{color:var(--mute)}
.check--fault .check__val{color:var(--fault);font-weight:600}

.qgroup{margin:0 0 22px}
.qgroup__h{margin:0 0 8px;font-family:var(--mono);font-size:11px;letter-spacing:.12em;
text-transform:uppercase;color:var(--mute);font-weight:500}
.qgroup__h span{color:var(--accent);margin-left:6px}
.ms{display:grid;grid-template-columns:minmax(160px,2fr) 1.4fr auto;gap:14px;
align-items:baseline;padding:8px 0;border-top:1px solid var(--rule);font-size:14px}
.ms__title{font-weight:500}
.ms__kpi{font-family:var(--mono);font-size:11.5px;color:var(--mute)}
.ms__state{font-family:var(--mono);font-size:11px;color:var(--mute);white-space:nowrap}
@media (max-width:780px){.ms{grid-template-columns:1fr;gap:2px}}
.colnote{margin:16px 0 0;font-size:13px;color:var(--mute);max-width:72ch}
.drift{margin:0 0 22px;padding:13px 16px;border-left:3px solid var(--warn);
background:rgba(184,117,3,.09);font-size:13.5px;max-width:76ch}
.drift--info{border-left-color:var(--flow);background:rgba(27,92,240,.07)}

.mile{display:grid;grid-template-columns:92px 1fr 92px auto;gap:14px;align-items:baseline;
padding:9px 0;border-top:1px solid var(--rule);font-size:14px}
.mile:first-child{border-top:0}
.mile__date,.mile__when{font-family:var(--mono);font-size:11.5px;color:var(--mute)}
.mile__track{font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;
text-transform:uppercase;color:var(--mute)}
.mile--past{opacity:.45}
.mile--past .mile__label{text-decoration:line-through;text-decoration-thickness:1px}
.mile--next{font-weight:600}
.mile--next .mile__date,.mile--next .mile__when{color:var(--flow)}

footer{padding:26px 0 60px;font-family:var(--mono);font-size:11.5px;color:var(--mute);
line-height:1.85;border-bottom:0}
footer p{margin:0 0 8px;max-width:80ch}

@media (max-width:780px){
.app{grid-template-columns:1fr auto;gap:2px 12px}
.app__gate,.app__state{grid-column:1;font-size:11.5px}
.app--head{display:none}
.mile{grid-template-columns:78px 1fr;gap:3px 12px}
.mile__track,.mile__when{grid-column:2}
.stream{grid-template-columns:1fr auto;gap:5px 12px}
.stream__bar{grid-column:1/-1;order:3}
.stream__rate{order:2}
.stream__acc{order:4;grid-column:1/-1;text-align:left}
.facts{grid-template-columns:1fr;gap:2px}
.facts dd{margin-bottom:10px}
.verdict__meta{margin-left:0;width:100%}
.nav__links{margin-left:0;width:100%;gap:16px}
}
@media (prefers-reduced-motion:reduce){
.dot{animation:none}.card{transition:none}.flow .fl{animation:none}}
"""

FOOTER = """
<footer>
<p>Funding figures are read from Ethereum mainnet and compared as Superfluid
<code>wei/s</code> integers against the rates ratified in EP&nbsp;6.49, never as
reconstructed dollar amounts. Commitments and reports are the committee's own record.</p>
<p>Streams checked daily against the Stream Management Pod. Source and method:
<a href="https://github.com/SovereignSignal/spp3-accountability">spp3-accountability</a>.
Built and operated by sovereignsignal.eth for the ENS SPP3 committee.</p>
</footer>
"""

JS = """
(function(){
var SERVER_NOW=%SERVER_NOW%;
var t0=performance.now();
var nodes=[].slice.call(document.querySelectorAll('[data-rate][data-since]'));
if(!nodes.length)return;
var reduce=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function paint(){
  var now=SERVER_NOW+(performance.now()-t0)/1000;
  for(var i=0;i<nodes.length;i++){
    var el=nodes[i];
    var rate=Number(el.getAttribute('data-rate'));
    var since=Number(el.getAttribute('data-since'));
    if(!rate||!since)continue;
    var v=rate*Math.max(0,now-since)/1e18;
    var whole=Math.floor(v);
    el.querySelector('.acc__whole').textContent=whole.toLocaleString('en-US');
    el.querySelector('.acc__frac').textContent='.'+String(Math.floor((v-whole)*100)).padStart(2,'0');
  }
}
paint();
if(reduce){setInterval(paint,1000);}else{(function loop(){paint();requestAnimationFrame(loop);})();}
})();
"""
