#!/usr/bin/env python3
"""notion_export.py — export SPP3 cohort state from committee Notion.

Exports the cohort-round pipeline only. The Marketplace RFP board is a
selection process still in flight, not an accountability record of funded
work, and is deliberately not exported: leaving it in board.json kept
applicant data reachable over HTTP after the page was removed.

The export is a WHITELIST, never a blacklist. Only the fields named in
PUBLIC_* below ever leave Notion. A new column added to either database is
invisible here until someone adds it deliberately, which is the behaviour you
want when the destination is a public page.

Excluded on purpose:
  - Individual member scores. EP 6.49: "Individual scoring records remain
    internal working documents, available to the accountability body or ENS
    Foundation on request." They live in each row's page body, which this
    export never reads. Only the aggregate Final Score is published.
  - Applicant contact details (email, Telegram, primary contact, payment
    address). Submitted to the committee, not to the DAO.
  - Committee notes and rationale prose.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/home/ubuntu/RFP-Workspace/scripts")

import acct_config as C  # noqa: E402

PIPELINE_DB_ID = "3571724b-64c7-8108-ada5-f8ef55c094a3"

# (notion property, output key, kind)
PUBLIC_PIPELINE = [
    ("Applicant", "name", "title"),
    ("Requested (USD)", "requested_usd", "number"),
    ("Awarded (USD)", "awarded_usd", "number"),
    ("Status", "status", "select"),
    ("Category", "category", "multi"),
    ("Team Status", "team_status", "select"),
    ("Final Score", "final_score", "number"),
    ("Program Terms Agreement", "terms_agreed", "checkbox"),
    ("Award Notice Agreement", "notice_agreed", "checkbox"),
    ("Recusals", "recusals", "multi"),
]


def _field(props, name, kind):
    v = props.get(name)
    if not v:
        return None
    if kind == "title":
        return "".join(t.get("plain_text", "") for t in v.get("title", [])) or None
    if kind == "number":
        return v.get("number")
    if kind == "checkbox":
        return bool(v.get("checkbox"))
    if kind == "url":
        return v.get("url")
    if kind == "created":
        return (v.get("created_time") or "")[:10] or None
    if kind == "select":
        d = v.get("select") or v.get("status")
        return d.get("name") if isinstance(d, dict) else None
    if kind == "multi":
        return [x.get("name") for x in (v.get("multi_select") or [])]
    return None


def export_rows(notion, db_id, fields):
    """Query a database and project each row through the whitelist."""
    out = []
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = notion("POST", "/databases/%s/query" % db_id, body)
        for row in r.get("results", []):
            props = row["properties"]
            rec = {}
            for prop, key, kind in fields:
                rec[key] = _field(props, prop, kind)
            if rec.get("name"):
                out.append(rec)
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    return out


def main():
    import rfp_lib as L

    pipeline = export_rows(L.notion, PIPELINE_DB_ID, PUBLIC_PIPELINE)

    doc = {
        "_generated": True,
        "_source": "notion_export.py (field whitelist; individual scores and "
                   "contact details never leave the committee workspace)",
        "pipeline": sorted(pipeline, key=lambda r: -(r["awarded_usd"] or 0)),
    }

    out = C.DATA_DIR / "notion" / "board.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    prev = out.read_text() if out.exists() else ""
    new = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    changed = prev != new
    if changed:
        out.write_text(new)
    print("pipeline=%d changed=%s" % (len(pipeline), changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
