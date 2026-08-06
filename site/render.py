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

def page_home(ctx):
    epoch = ctx["providers"]["spp3_stream_start"]
    funded = _funded(ctx)
    rate = sum(s["actual_wei_s"] for s in ctx["status"]["streams"]
               if s["cohort"] == "spp3")
    q, qdays = _next_quarter(ctx)

    cards = []
    for p in funded:
        s = _stream_for(ctx, p["slug"]) or {}
        c = _commit(ctx, p["slug"])
        ms = c.get("milestones", [])
        state = "ok" if s.get("ok") else "fault"
        cards.append(
            '<a class="card card--%s" href="/provider/%s">'
            '<span class="card__label">%s</span>'
            '<span class="card__headline">%s</span>'
            '<span class="card__detail">$%s/yr &middot; %s</span>'
            '<span class="card__detail card__detail--dim">%s</span></a>' % (
                state, _esc(p["slug"]), _esc(p["name"]),
                _ticker(s.get("actual_wei_s", 0), epoch, "tick tick--card"),
                _money(p["award_usd"]),
                "stream live" if s.get("ok") else "STREAM FAULT",
                ("%d commitments recorded" % len(ms)) if ms
                else "commitments not yet recorded"))

    drift = ""
    if _ghosts(ctx):
        drift = ('<p class="drift"><b>Board drift:</b> the committee pipeline still '
                 'lists %s as cohort-selected, but there is no funded stream. EthID '
                 'declined publicly on 3 July 2026. The chain is authoritative here.</p>'
                 % _esc(", ".join(_ghosts(ctx))))

    obligation = ""
    if q:
        obligation = (
            '<section><h2>Next obligation</h2>'
            '<p class="big">Quarterly Reports for <b>%s</b> are due <b>%s</b>, %s.</p>'
            '<p class="colnote">Due within 30 days of quarter end. A public version on '
            'the ENS Forum is contractual, not a courtesy (Program Terms clause 6.3). '
            'Nothing has been filed yet, which is expected: the window has not opened.'
            '</p></section>' % (_esc(q["quarter"]), _esc(q["report_due"]), _when(qdays)))

    return (
        '<div class="hero"><p class="eyebrow">Delivered to the cohort so far</p>'
        '<p class="ticker">%s</p>'
        '<p class="hero__sub">Streaming continuously since <b>%s</b> at <b>$%s/yr</b> '
        'across %d providers. Amounts are read from Ethereum mainnet; commitments and '
        'reports are the committee\'s own record.</p></div>'
        '%s<section><h2>The cohort</h2><div class="cards">%s</div></section>%s' % (
            _ticker(rate, epoch, "tick tick--hero"), _fmt_utc(epoch),
            _money(_usd(rate)), len(funded), drift, "\n".join(cards), obligation))


def page_providers(ctx):
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
                ("%d recorded" % len(c.get("milestones", [])))
                if c.get("milestones") else "not recorded",
                "30 Oct 2026"))
    return ('<p class="lede">Four providers ratified by EP&nbsp;6.49 and funded '
            'on-chain since 1 August 2026.</p>'
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
        mrows = "".join('<li class="app"><span class="app__name">%s</span>'
                        '<span class="app__gate">%s</span>'
                        '<span class="app__state">%s</span></li>'
                        % (_esc(m.get("title", "")), _esc(m.get("target_quarter", "")),
                           _esc(m.get("status", "not started")))
                        for m in ms)
        milestones = '<ul class="apps">%s</ul>' % mrows
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


def _verdict(ctx):
    st = ctx["status"]
    age = max(0, int((ctx["now"] - _parse_iso(st["checked_at"])) / 60))
    return ('<header class="verdict v-%s"><div class="wrap">'
            '<span class="dot" aria-hidden="true"></span>'
            '<span class="verdict__copy">%s</span>'
            '<span class="verdict__meta">block %s &middot; checked %d min ago</span>'
            '</div></header>' % (
                st["overall"], _esc(VERDICT_COPY.get(st["overall"], st["overall"])),
                "{:,}".format(st["block_number"]), age))


def render(ctx, path="/"):
    """Dispatch. Returns None for an unknown path so the server can 404."""
    if path.startswith("/provider/"):
        slug = path[len("/provider/"):].strip("/")
        body = page_provider(ctx, slug)
        if body is None:
            return None
        p = next(x for x in _funded(ctx) if x["slug"] == slug)
        title, active = p["name"], "/providers"
    elif path in ROUTES:
        title, builder = ROUTES[path]
        body, active = builder(ctx), path
    else:
        return None

    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
            "<title>" + _esc(title) + " · ENS SPP3 accountability</title>\n"
            "<meta name=\"description\" content=\"Accountability tracker for the ENS "
            "SPP3 service provider cohort: funding verified on-chain, commitments, and "
            "quarterly reporting.\">\n<style>" + CSS + "</style>\n</head>\n<body>\n"
            + _nav(active) + _verdict(ctx)
            + '<main class="wrap"><h1 class="title">' + _esc(title) + "</h1>\n"
            + body + FOOTER + "</main>\n<script>"
            + JS.replace("%SERVER_NOW%", repr(ctx["now"])) + "</script>\n</body>\n</html>\n")


CSS = """
:root{--ground:#EDF0F3;--ink:#0E141A;--flow:#1B5CF0;--ok:#0E8A5F;--warn:#B87503;
--fault:#C2331B;--rule:rgba(14,20,26,.14);--mute:rgba(14,20,26,.58);--panel:#fff;
--mono:ui-monospace,"SF Mono",SFMono-Regular,"JetBrains Mono",Menlo,Consolas,monospace;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
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
@keyframes pulse{0%{box-shadow:0 0 0 0 currentColor;opacity:1}
70%{box-shadow:0 0 0 9px transparent;opacity:.75}100%{box-shadow:0 0 0 0 transparent;opacity:1}}
.verdict__copy{font-weight:620;font-size:14.5px}
.verdict__meta{margin-left:auto;font-family:var(--mono);font-size:11.5px;color:var(--mute)}

.title{font-family:var(--mono);font-size:13px;font-weight:500;letter-spacing:.12em;
text-transform:uppercase;color:var(--mute);margin:34px 0 0}
.lede{margin:14px 0 28px;font-size:15px;color:var(--mute);max-width:70ch}
.lede b{color:var(--ink)}
.prose{margin:0 0 12px;font-size:14.5px;max-width:72ch}
.prose--dim{color:var(--mute);font-size:13.5px}
.empty{margin:0;font-size:14.5px;color:var(--mute);font-style:italic}
.big{margin:0;font-size:19px;letter-spacing:-.01em}

.hero{padding:32px 0 38px;border-bottom:1px solid var(--rule)}
.hero--sm{padding:20px 0 24px}
.eyebrow{margin:0 0 12px;font-family:var(--mono);font-size:11px;letter-spacing:.14em;
text-transform:uppercase;color:var(--mute)}
.ticker{margin:0;font-family:var(--mono);font-weight:600;letter-spacing:-.03em;
font-size:clamp(38px,8.5vw,78px);line-height:1;font-variant-numeric:tabular-nums}
.ticker--sm{font-size:clamp(30px,6vw,50px)}
.tick--hero::before{content:"$";color:var(--flow);margin-right:.06em}
.tick--hero .acc__frac{font-size:.44em;color:var(--mute)}
.hero__sub{margin:18px 0 0;font-size:14.5px;color:var(--mute);max-width:64ch}
.hero__sub b{color:var(--ink);font-weight:600}

section{padding:28px 0;border-bottom:1px solid var(--rule)}
h2{margin:0 0 18px;font-family:var(--mono);font-size:11px;letter-spacing:.14em;
text-transform:uppercase;color:var(--mute);font-weight:500}
ul{list-style:none;margin:0;padding:0}

.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:14px}
.card{display:flex;flex-direction:column;gap:4px;padding:17px 17px 15px;
background:var(--panel);border:1px solid var(--rule);border-radius:9px;
text-decoration:none;border-left-width:3px;transition:transform .12s ease}
.card:hover{transform:translateY(-2px)}
.card--ok{border-left-color:var(--ok)}
.card--fault{border-left-color:var(--fault)}
.card__label{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;
text-transform:uppercase;color:var(--mute)}
.card__headline{font-size:17px;font-weight:600}
.tick--card{font-family:var(--mono);font-size:23px;font-weight:600;letter-spacing:-.02em;
font-variant-numeric:tabular-nums;display:block;margin:2px 0}
.tick--card::before{content:"$";color:var(--flow)}
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
.tick--row::before{content:"$";color:var(--flow)}
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
@media (prefers-reduced-motion:reduce){.dot{animation:none}.card{transition:none}}
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
