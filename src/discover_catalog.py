"""
Enumerates every OData service this tenant publishes to the configured user, and every entity
set inside each one.

This replaces guessing service names entirely. The tenant publishes a live catalog at
    GET {SAP_BASE_URL}/sap/byd/odata/
returning an Atom feed of `<service>.svc` links, and each service answers
    GET {SAP_BASE_URL}/sap/byd/odata/<service>.svc/     (Accept: application/json)
with its own entity-set list. Neither call needs $metadata, which is unreliable on this tenant.

(An earlier round of this project burned two passes of ~100 guessed service names for 0 hits,
on the documented assumption that no catalog existed. It does; this module is that catalog.)

Run: python -m src.discover_catalog
Writes schema_snapshots/service_catalog.json
"""

import concurrent.futures
import json
import logging
import os
import re
import sys

import requests

from src.config import load_config

logger = logging.getLogger("sap2odoo.discover_catalog")

RESULT_PATH = "schema_snapshots/service_catalog.json"


def list_services(config):
    resp = requests.get(
        f"{config.sap_base_url}/sap/byd/odata/",
        auth=(config.sap_username, config.sap_password),
        headers={"Accept": "application/xml"},
        timeout=60,
        verify=config.verify_ssl,
    )
    resp.raise_for_status()
    return sorted(set(re.findall(r'href="([^"]+\.svc)"', resp.text)))


def entity_sets(config, service):
    url = f"{config.sap_base_url}/sap/byd/odata/{service}/"
    try:
        resp = requests.get(
            url,
            auth=(config.sap_username, config.sap_password),
            headers={"Accept": "application/json"},
            timeout=120,
            verify=config.verify_ssl,
        )
    except requests.RequestException as exc:
        return {"service": service, "error": str(exc)[:150], "entity_sets": []}

    if resp.status_code != 200:
        return {"service": service, "error": f"HTTP {resp.status_code}", "entity_sets": []}

    # Service documents come back either as OData JSON or as an Atom service document,
    # depending on the service - accept both rather than forcing a format.
    try:
        return {"service": service, "entity_sets": sorted(resp.json()["d"]["EntitySets"])}
    except (ValueError, KeyError, TypeError):
        pass
    # Atom service document: <app:collection sap:label="..." ... href="EntitySetName"/>
    # The tag is namespace-prefixed and href is not necessarily the first attribute.
    sets = re.findall(r'<(?:\w+:)?collection\b[^>]*\bhref="([^"]+)"', resp.text)
    if sets:
        return {"service": service, "entity_sets": sorted(set(sets))}
    return {"service": service, "error": "no entity sets in response", "entity_sets": []}


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config()

    services = list_services(config)
    logger.info("Tenant publishes %d OData services to this user.", len(services))

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda s: entity_sets(config, s), services))

    total = 0
    for r in sorted(results, key=lambda r: r["service"]):
        if r.get("error"):
            logger.info("  %-46s ERROR %s", r["service"], r["error"])
            continue
        total += len(r["entity_sets"])
        logger.info("  %-46s %3d entity sets", r["service"], len(r["entity_sets"]))
        for name in r["entity_sets"]:
            logger.info("        %s", name)

    os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    logger.info("")
    logger.info("%d entity sets across %d services. Wrote %s", total, len(services), RESULT_PATH)


if __name__ == "__main__":
    sys.exit(main())
