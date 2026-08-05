"""validate.py — explicit validators for the accountability data files.

Hand-written rather than JSON Schema because `jsonschema` is not installed and
this VM stays dependency-free. Every validator returns a list of human-readable
error strings; an empty list means valid.
"""
import json
import re

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
VALID_COHORTS = {"spp3", "spp2-continuing", "committee"}

_TOP_LEVEL = ("program", "schema_version", "pod", "master_stream_wei_s",
              "spp3_stream_start", "providers", "retired")
_PROVIDER_KEYS = ("slug", "name", "award_usd", "categories",
                  "approved_wallet", "cohort", "recusals")


def validate_providers(doc):
    errors = []
    if not isinstance(doc, dict):
        return ["providers document is not an object"]

    for key in _TOP_LEVEL:
        if key not in doc:
            errors.append("missing top-level key: %s" % key)
    if errors:
        return errors

    if not ADDRESS_RE.match(str(doc["pod"])):
        errors.append("pod is not a valid address: %r" % doc["pod"])
    if not isinstance(doc["master_stream_wei_s"], int) or doc["master_stream_wei_s"] <= 0:
        errors.append("master_stream_wei_s must be a positive integer")
    if not isinstance(doc["spp3_stream_start"], int) or doc["spp3_stream_start"] <= 0:
        errors.append("spp3_stream_start must be a positive unix timestamp")
    if not isinstance(doc["providers"], list) or not doc["providers"]:
        return errors + ["providers must be a non-empty list"]

    seen_slugs = {}
    seen_wallets = {}
    for i, p in enumerate(doc["providers"]):
        where = "providers[%d]" % i
        if not isinstance(p, dict):
            errors.append("%s is not an object" % where)
            continue
        for key in _PROVIDER_KEYS:
            if key not in p:
                errors.append("%s missing key: %s" % (where, key))
        if "slug" not in p or "approved_wallet" not in p:
            continue

        slug = p["slug"]
        where = "providers[%s]" % slug
        if slug in seen_slugs:
            errors.append("duplicate slug: %s" % slug)
        seen_slugs[slug] = i

        wallet = str(p["approved_wallet"])
        if not ADDRESS_RE.match(wallet):
            errors.append("%s approved_wallet is not a valid address: %r"
                          % (where, wallet))
        elif wallet.lower() in seen_wallets:
            errors.append("duplicate approved_wallet %s (%s and %s)"
                          % (wallet, seen_wallets[wallet.lower()], slug))
        else:
            seen_wallets[wallet.lower()] = slug

        if not isinstance(p.get("award_usd"), int) or p.get("award_usd", 0) <= 0:
            errors.append("%s award_usd must be a positive integer" % where)
        if p.get("cohort") not in VALID_COHORTS:
            errors.append("%s cohort must be one of %s, got %r"
                          % (where, sorted(VALID_COHORTS), p.get("cohort")))
        if not isinstance(p.get("categories"), list):
            errors.append("%s categories must be a list" % where)
        if not isinstance(p.get("recusals"), list):
            errors.append("%s recusals must be a list" % where)

    for i, r in enumerate(doc.get("retired") or []):
        if not isinstance(r, dict) or "slug" not in r:
            errors.append("retired[%d] must be an object with a slug" % i)
            continue
        if r["slug"] in seen_slugs:
            errors.append("%s appears in both active and retired lists"
                          % r["slug"])
        if not ADDRESS_RE.match(str(r.get("approved_wallet", ""))):
            errors.append("retired[%s] approved_wallet is not a valid address"
                          % r["slug"])

    return errors


def load_providers(path):
    """Load and validate. Raises ValueError listing every problem found."""
    doc = json.loads(open(path).read())
    errs = validate_providers(doc)
    if errs:
        raise ValueError("providers.json invalid:\n  - " + "\n  - ".join(errs))
    return doc
