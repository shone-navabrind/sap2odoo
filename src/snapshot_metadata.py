"""
Captures $metadata for the tenant's analytics services into schema_snapshots/, which is where
SAPODataClient.get_entity_fields() looks first.

Why this is needed: on the catch-all services (ana_businessanalytics_analytics.svc, 582 entity
sets) $metadata collapses to just ID + TotaledProperties, exactly as CLAUDE.md documents. On the
*module* services (fin_generalledger_analytics.svc and friends) the same call returns the real
field lists. So every report is reachable twice, and the module service is the one worth using.

Skips the two catch-all services by default (they are huge and return nothing useful); pass
--all to try them anyway.

Run: python -m src.snapshot_metadata [--all] [service.svc ...]
"""

import concurrent.futures
import logging
import os
import re
import sys

import requests

from src.config import load_config
from src.discover_catalog import list_services

logger = logging.getLogger("sap2odoo.snapshot_metadata")

SNAPSHOT_DIR = "schema_snapshots"
# Catch-all services: every report is also reachable through its own module service, where
# $metadata actually resolves. Fetching these is a slow way to get ID + TotaledProperties.
CATCH_ALL = {"ana_businessanalytics_analytics.svc", "cc_home_analytics.svc",
             "mma_managingmyarea_analytics.svc"}


def snapshot_one(config, service):
    path = os.path.join(SNAPSHOT_DIR, f"{service}.metadata.xml")
    try:
        resp = requests.get(
            f"{config.sap_base_url}/sap/byd/odata/{service}/$metadata",
            auth=(config.sap_username, config.sap_password),
            headers={"Accept": "application/xml"},
            timeout=600,
            verify=config.verify_ssl,
        )
    except requests.RequestException as exc:
        return service, 0, 0, f"ERROR {str(exc)[:90]}"

    if resp.status_code != 200 or "<EntityType" not in resp.text:
        return service, 0, 0, f"HTTP {resp.status_code}, no entity types"

    types = re.findall(r'<EntityType Name="([^"]+)"[^>]*>(.*?)</EntityType>', resp.text, re.DOTALL)
    real = sum(1 for _, body in types
               if len(re.findall(r'<Property Name="([^"]+)"', body)) > 2)
    if real == 0:
        return service, len(types), 0, "collapsed to ID + TotaledProperties - not saved"

    with open(path, "w", encoding="utf-8") as f:
        f.write(resp.text)
    return service, len(types), real, f"saved {len(resp.text) // 1024} KB"


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    services = args or [s for s in list_services(config)
                        if s.endswith("_analytics.svc")
                        and (s not in CATCH_ALL or "--all" in sys.argv)]
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    logger.info("Fetching $metadata for %d services ...", len(services))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda s: snapshot_one(config, s), services))

    saved = 0
    for service, types, real, note in sorted(results):
        logger.info("  %-46s %3d types, %3d with real fields - %s", service, types, real, note)
        saved += 1 if real else 0
    logger.info("")
    logger.info("%d/%d services now have a usable metadata snapshot in %s/",
                saved, len(services), SNAPSHOT_DIR)


if __name__ == "__main__":
    sys.exit(main())
