"""
Finds which analytics reports on this tenant actually return G/L account numbers.

Background: the dedicated "G/L Account Master Data" report (RPFINGLAU17) exists and has exactly
the right fields (CGLACCT = account number, TGLACCT = account name), but it declares a mandatory
"Chart of Accounts" variable with no default, so it returns 0 rows and rejects a filter unless a
valid chart-of-accounts key is supplied - which the tenant does not expose anywhere readable.

Many *other* reports carry the same two fields and DO return rows without any parameters. Each
such report yields the set of G/L accounts actually used in its own area (fixed assets,
inventory, payables, ...). Unioning them gives the accounts really in use in this tenant, which
is what an Odoo chart of accounts needs to contain.

This module finds those reports empirically rather than assuming which ones work.

Run: python -m src.probe_gl_accounts
Writes schema_snapshots/gl_account_sources.json
"""

import concurrent.futures
import glob
import json
import logging
import os
import re
import sys

import requests

from src.config import load_config

logger = logging.getLogger("sap2odoo.probe_gl_accounts")

RESULT_PATH = "schema_snapshots/gl_account_sources.json"
SNAPSHOT_DIR = "schema_snapshots"


def candidates():
    """Every (service, entity_set) whose metadata declares both CGLACCT and TGLACCT."""
    found = []
    for path in sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "*_analytics.svc.metadata.xml"))):
        service = os.path.basename(path).replace(".metadata.xml", "")
        text = open(path, encoding="utf-8", errors="replace").read()
        sets_by_type = {}
        for set_name, type_name in re.findall(
                r'<EntitySet Name="([^"]+)"[^>]*EntityType="[^"]*\.([^"]+)"', text):
            sets_by_type.setdefault(type_name, set_name)
        for match in re.finditer(r'<EntityType Name="([^"]+)".*?</EntityType>', text, re.DOTALL):
            props = set(re.findall(r'<Property Name="([^"]+)"', match.group(0)))
            if {"CGLACCT", "TGLACCT"} <= props and match.group(1) in sets_by_type:
                found.append((service, sets_by_type[match.group(1)]))
    return found


def fetch_accounts(config, service, entity_set):
    """All (account number, account name) pairs this report will hand over, or an error."""
    url = f"{config.sap_base_url}/sap/byd/odata/{service}/{entity_set}"
    try:
        resp = requests.get(
            url,
            params={"$select": "CGLACCT,TGLACCT", "$format": "json"},
            auth=(config.sap_username, config.sap_password),
            headers={"Accept": "application/json"},
            timeout=300,
            verify=config.verify_ssl,
        )
    except requests.RequestException as exc:
        return {"service": service, "entity_set": entity_set, "error": str(exc)[:120], "accounts": []}

    if resp.status_code != 200:
        detail = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", resp.text or ""))[:120]
        return {"service": service, "entity_set": entity_set,
                "error": f"HTTP {resp.status_code}: {detail}", "accounts": []}
    try:
        results = resp.json()["d"]["results"]
    except (ValueError, KeyError, TypeError) as exc:
        return {"service": service, "entity_set": entity_set, "error": f"parse: {exc}", "accounts": []}

    accounts = sorted({(r.get("CGLACCT", ""), r.get("TGLACCT", ""))
                       for r in results if r.get("CGLACCT")})
    return {"service": service, "entity_set": entity_set,
            "accounts": [{"code": c, "name": n} for c, n in accounts]}


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config()

    targets = candidates()
    logger.info("%d reports declare both CGLACCT and TGLACCT. Testing which return rows ...",
                len(targets))

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda t: fetch_accounts(config, *t), targets))

    productive = [r for r in results if r["accounts"]]
    union = {}
    for r in productive:
        for account in r["accounts"]:
            # Keep the first non-empty name seen for each account number.
            if account["code"] not in union or not union[account["code"]]["name"]:
                union[account["code"]] = account
            union[account["code"]].setdefault("sources", []).append(
                f"{r['service']}/{r['entity_set']}")

    logger.info("")
    logger.info("Reports that returned G/L accounts (%d of %d):", len(productive), len(targets))
    for r in sorted(productive, key=lambda r: -len(r["accounts"])):
        logger.info("  %-40s %-38s %4d accounts",
                    r["service"], r["entity_set"], len(r["accounts"]))

    logger.info("")
    logger.info("%d distinct G/L accounts in use across this tenant:", len(union))
    for code in sorted(union)[:25]:
        logger.info("  %-12s %s", code, union[code]["name"])
    if len(union) > 25:
        logger.info("  ... and %d more", len(union) - 25)

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump({"reports": results, "accounts": sorted(union.values(), key=lambda a: a["code"])},
                  f, indent=2)
    logger.info("")
    logger.info("Wrote %s", RESULT_PATH)


if __name__ == "__main__":
    sys.exit(main())
