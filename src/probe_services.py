"""
Probes the live ByDesign tenant for every custom OData service defined in
`byd-api-samples-main/Custom OData Services/`, so we know - from the tenant itself, not from
assumption - which ones the user has already imported and which are still missing.

For each service it requests `$metadata`, and classifies the result:
  LIVE          - service exists and returns a schema
  NOT_IMPORTED  - 404: the .xml has not been imported into the tenant yet
  NO_AUTH       - service exists but the technical user is not authorised for it
  ERROR         - anything else (reported verbatim, never silently swallowed)

Run: python -m src.probe_services            # probe all 47
     python -m src.probe_services kh...      # probe a subset by name
"""

import concurrent.futures
import json
import logging
import os
import re
import sys

import requests

from src.config import load_config
from src.parse_service_defs import build_index

logger = logging.getLogger("sap2odoo.probe")

RESULT_PATH = "schema_snapshots/service_probe.json"


def probe_one(config, service):
    url = f"{config.sap_base_url}/sap/byd/odata/cust/v1/{service}/$metadata"
    try:
        resp = requests.get(
            url,
            auth=(config.sap_username, config.sap_password),
            headers={"Accept": "application/xml"},
            timeout=60,
            verify=config.verify_ssl,
        )
    except requests.RequestException as exc:
        return {"service": service, "status": "ERROR", "detail": str(exc)[:200]}

    body = resp.text or ""
    if resp.status_code == 200 and "<edmx:Edmx" in body:
        sets = sorted(set(re.findall(r'<EntitySet Name="([^"]+)"', body)))
        return {"service": service, "status": "LIVE", "http": 200, "entity_sets": sets}
    if resp.status_code == 404:
        return {"service": service, "status": "NOT_IMPORTED", "http": 404}
    if "RBAM_ERROR" in body or resp.status_code in (401, 403):
        return {"service": service, "status": "NO_AUTH", "http": resp.status_code}
    detail = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))[:200]
    return {"service": service, "status": "ERROR", "http": resp.status_code, "detail": detail}


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config()

    services = [s["service"] for s in build_index()]
    if len(sys.argv) > 1:
        wanted = {a.lower() for a in sys.argv[1:]}
        services = [s for s in services if s.lower() in wanted]

    logger.info("Probing %d custom OData services on %s ...", len(services), config.sap_base_url)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda s: probe_one(config, s), services))

    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    for status in ("LIVE", "NO_AUTH", "NOT_IMPORTED", "ERROR"):
        group = by_status.get(status, [])
        if not group:
            continue
        logger.info("")
        logger.info("%s (%d):", status, len(group))
        for r in sorted(group, key=lambda r: r["service"]):
            extra = ""
            if status == "LIVE":
                extra = f" - {len(r['entity_sets'])} entity sets"
            elif r.get("detail"):
                extra = f" - {r['detail'][:120]}"
            logger.info("  %-36s%s", r["service"], extra)

    os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    logger.info("")
    logger.info("Wrote %s", RESULT_PATH)


if __name__ == "__main__":
    sys.exit(main())
