"""
Asks the tenant how many rows each live-but-unextracted entity set actually holds, before any
of them get added to extract_raw.SOURCES.

Costs one cheap request per entity set ($top=1&$inlinecount=allpages returns the total count
without downloading the rows), and means the decision about what is worth extracting is made
from real row counts rather than from a name that looks promising.

Run: python -m src.probe_entity_sets
Writes schema_snapshots/entity_set_counts.json
"""

import concurrent.futures
import json
import logging
import os
import sys

import requests

from src.config import load_config
from src.coverage_gap import missing_entity_sets

logger = logging.getLogger("sap2odoo.probe_entity_sets")

RESULT_PATH = "schema_snapshots/entity_set_counts.json"


def count_one(config, service, entity_set):
    url = f"{config.sap_base_url}/sap/byd/odata/cust/v1/{service}/{entity_set}"
    try:
        resp = requests.get(
            url,
            params={"$top": "1", "$inlinecount": "allpages", "$format": "json"},
            auth=(config.sap_username, config.sap_password),
            headers={"Accept": "application/json"},
            timeout=120,
            verify=config.verify_ssl,
        )
    except requests.RequestException as exc:
        return {"service": service, "entity_set": entity_set, "count": None, "error": str(exc)[:150]}

    if resp.status_code != 200:
        body = (resp.text or "")[:150].replace("\n", " ")
        return {"service": service, "entity_set": entity_set, "count": None,
                "error": f"HTTP {resp.status_code}: {body}"}
    try:
        count = int(resp.json()["d"]["__count"])
    except (ValueError, KeyError, TypeError) as exc:
        return {"service": service, "entity_set": entity_set, "count": None, "error": f"parse: {exc}"}
    return {"service": service, "entity_set": entity_set, "count": count}


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config()

    targets = [(svc, es["name"], es["field_count"])
               for svc, missing in missing_entity_sets() for es in missing]
    logger.info("Counting rows in %d live-but-unextracted entity sets ...", len(targets))

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda t: count_one(config, t[0], t[1]), targets))
    fields = {(t[0], t[1]): t[2] for t in targets}
    for r in results:
        r["field_count"] = fields.get((r["service"], r["entity_set"]), 0)

    with_data = sorted(
        (r for r in results if r.get("count")),
        key=lambda r: (r["service"], -r["count"]),
    )
    empty = [r for r in results if r.get("count") == 0]
    errors = [r for r in results if r.get("count") is None]

    logger.info("")
    logger.info("HAS DATA (%d):", len(with_data))
    current = None
    for r in with_data:
        if r["service"] != current:
            current = r["service"]
            logger.info("")
            logger.info("  %s", current)
        logger.info("    %-52s %8d rows  %2d fields", r["entity_set"], r["count"], r["field_count"])

    logger.info("")
    logger.info("EMPTY on this tenant (%d) - nothing to extract:", len(empty))
    logger.info("  %s", ", ".join(f"{r['service']}/{r['entity_set']}" for r in empty))
    if errors:
        logger.info("")
        logger.info("ERRORS (%d):", len(errors))
        for r in errors:
            logger.info("  %s/%s - %s", r["service"], r["entity_set"], r["error"])

    os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    logger.info("")
    logger.info("Wrote %s", RESULT_PATH)


if __name__ == "__main__":
    sys.exit(main())
