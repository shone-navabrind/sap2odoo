"""
Turns the raw service catalog into something a human can search: every entity set the tenant
publishes, joined to its business description.

Entity sets on the analytics services are named RP<DataSourceID>_<Query>QueryResults, and
<DataSourceID> is exactly the "DataSourceID" column of the Design Data Sources export in
schema_snapshots/ByDesign_Design_Data_Sources_catalog.csv - so the tenant's own descriptions can
be attached to each one. (The catalog CSV's "Exposed" column refers to a *different* API, the
/sap/byd/odata/analytics/ds/ data-source endpoint, which returns 404 on this tenant. It says
nothing about whether the report-backed entity sets below work - they do.)

Run: python -m src.catalog_report              # writes SERVICE_CATALOG.csv
     python -m src.catalog_report <term> ...   # search descriptions and IDs for terms
"""

import csv
import json
import logging
import os
import re
import sys

logger = logging.getLogger("sap2odoo.catalog_report")

CATALOG_PATH = "schema_snapshots/service_catalog.json"
DATASOURCES_PATH = "schema_snapshots/ByDesign_Design_Data_Sources_catalog.csv"
OUTPUT_PATH = "SERVICE_CATALOG.csv"


def load_descriptions():
    """{normalised DataSourceID -> (Name, Description)}"""
    out = {}
    if not os.path.isfile(DATASOURCES_PATH):
        return out
    with open(DATASOURCES_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ds_id = (row.get("DataSourceID") or "").strip()
            if not ds_id:
                continue
            # Catalog IDs may carry a namespace prefix (/CCAT/SICAT); entity-set names drop it.
            out[ds_id.strip("/").replace("/", "").upper()] = (
                (row.get("Name") or "").strip(),
                (row.get("Description") or "").strip(),
            )
    return out


def build_rows():
    with open(CATALOG_PATH, encoding="utf-8") as f:
        catalog = json.load(f)
    descriptions = load_descriptions()

    rows = []
    for svc in catalog:
        for entity_set in svc.get("entity_sets", []):
            # RP<DataSourceID>_<Query>QueryResults -> DataSourceID
            match = re.match(r"^RP(.+?)_[A-Z]?Q\w*QueryResults$", entity_set)
            ds_id = match.group(1) if match else ""
            name, description = descriptions.get(ds_id.replace("_", "").upper(), ("", ""))
            if not name and ds_id:
                name, description = descriptions.get(ds_id.upper(), ("", ""))
            rows.append({
                "Service": svc["service"],
                "Entity Set": entity_set,
                "Data Source ID": ds_id,
                "Report Name": name,
                "Description": description,
                "URL Path": f"sap/byd/odata/{svc['service']}/{entity_set}",
            })
    return rows


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rows = build_rows()

    terms = [a.lower() for a in sys.argv[1:]]
    if terms:
        seen = set()
        for row in rows:
            haystack = f"{row['Entity Set']} {row['Report Name']} {row['Description']}".lower()
            if not any(t in haystack for t in terms):
                continue
            key = (row["Entity Set"], row["Report Name"])
            if key in seen:
                continue
            seen.add(key)
            print(f"{row['Entity Set']:<44} {row['Report Name'][:44]:<46} {row['Service']}")
            if row["Description"]:
                print(f"    {row['Description'][:150]}")
        print(f"\n{len(seen)} distinct matches")
        return

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    named = sum(1 for r in rows if r["Report Name"])
    logger.info("Wrote %s", OUTPUT_PATH)
    logger.info("  %d entity sets across %d services",
                len(rows), len({r["Service"] for r in rows}))
    logger.info("  %d matched to a business description in the Design Data Sources export", named)


if __name__ == "__main__":
    sys.exit(main())
