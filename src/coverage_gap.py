"""
Answers two questions the status reports could not answer before:

  1. For services ALREADY live on the tenant, which real business entity sets are we not
     extracting at all? (i.e. data sitting there that we never asked SAP for)
  2. For entity sets we DO extract, which of their fields never reach any Odoo CSV?

Both are computed from files on disk - the parsed service definitions
(schema_snapshots/custom_service_index.json), the live probe (schema_snapshots/service_probe.json),
output_raw/*.json and output_odoo/*.csv - so nothing here is asserted by hand.

Run: python -m src.coverage_gap            # both reports
     python -m src.coverage_gap --sets     # only the missing-entity-set report
     python -m src.coverage_gap --fields   # only the unused-field report
"""

import csv
import json
import logging
import os
import sys

from src.extract_raw import SOURCES, raw_filename
from src.parse_service_defs import load_index

logger = logging.getLogger("sap2odoo.coverage_gap")

PROBE_PATH = "schema_snapshots/service_probe.json"
RAW_DIR = "output_raw"
ODOO_DIR = "output_odoo"

# Entity sets that exist on every service and carry no business data of their own.
NOISE_SUFFIXES = ("CodeListCollection",)
NOISE_NAMES = {"CodeListCollection", "ContextualCodeListCollection", "AttachmentFolderCollection"}


def load_probe():
    if not os.path.isfile(PROBE_PATH):
        return {}
    with open(PROBE_PATH, encoding="utf-8") as f:
        return {r["service"]: r for r in json.load(f)}


def extracted_pairs():
    """{(service_basename, entity_set)} currently in SOURCES."""
    return {(s[0].rsplit("/", 1)[-1], s[1]) for s in SOURCES}


def missing_entity_sets():
    """
    Entity sets that a LIVE service exposes and that carry real business fields, but which
    SOURCES never pulls. Only counts sets the parsed .xml confirms are business entities
    (the live $metadata also lists dozens of codelist sets per service).
    """
    probe = load_probe()
    have = extracted_pairs()
    out = []

    for svc in load_index():
        name = svc["service"]
        live = probe.get(name)
        if not live or live["status"] != "LIVE":
            continue
        live_sets = set(live.get("entity_sets", []))

        missing = []
        for es in svc["entity_sets"]:
            if es["name"] in NOISE_NAMES or es["name"].endswith(NOISE_SUFFIXES):
                continue
            if es["name"] not in live_sets:
                continue  # defined in the .xml but not published by this tenant's version
            if (name, es["name"]) in have:
                continue
            missing.append(es)
        if missing:
            out.append((name, sorted(missing, key=lambda e: -e["field_count"])))
    return out


def unused_fields():
    """
    Per extracted raw file: which of its real columns never appear as a value source in any
    Odoo CSV. Approximated by checking whether a field's actual values show up in the Odoo
    output - conservative, but it reliably catches wholesale-ignored columns.
    """
    odoo_values = set()
    for filename in sorted(os.listdir(ODOO_DIR)) if os.path.isdir(ODOO_DIR) else []:
        if not filename.endswith(".csv"):
            continue
        with open(os.path.join(ODOO_DIR, filename), newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for value in row.values():
                    if value:
                        odoo_values.add(value.strip())

    out = []
    for source in SOURCES:
        service, entity_set = source[0], source[1]
        path = os.path.join(RAW_DIR, raw_filename(service, entity_set))
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload["rows"]
        if not rows:
            continue

        populated, unused = [], []
        for field in payload["fields_present"]:
            values = [r.get(field) for r in rows]
            non_empty = [str(v).strip() for v in values if v not in (None, "", False)]
            if not non_empty:
                continue  # empty in SAP - not a mapping gap
            populated.append(field)
            sample = set(non_empty[:400])
            if not (sample & odoo_values):
                unused.append(field)
        if unused:
            out.append({
                "service": service.rsplit("/", 1)[-1],
                "entity_set": entity_set,
                "rows": len(rows),
                "populated": len(populated),
                "unused": unused,
            })
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    want_sets = "--fields" not in sys.argv
    want_fields = "--sets" not in sys.argv

    if want_sets:
        print("=" * 110)
        print("GAP 1: business entity sets that are LIVE on the tenant but never extracted")
        print("=" * 110)
        total = 0
        for service, missing in missing_entity_sets():
            print(f"\n{service}  -  {len(missing)} unextracted entity sets")
            for es in missing:
                total += 1
                print(f"   {es['name']:<52} {es['field_count']:>3} fields  "
                      f"{', '.join(es['fields'][:6])}")
        print(f"\n>>> {total} live business entity sets are not being pulled at all.")

    if want_fields:
        print()
        print("=" * 110)
        print("GAP 2: SAP fields that have real data but never reach any Odoo CSV")
        print("=" * 110)
        total = 0
        for item in sorted(unused_fields(), key=lambda i: -len(i["unused"])):
            total += len(item["unused"])
            print(f"\n{item['service']}/{item['entity_set']}  "
                  f"({item['rows']} rows, {item['populated']} populated fields, "
                  f"{len(item['unused'])} unused)")
            print("   " + ", ".join(item["unused"]))
        print(f"\n>>> {total} populated SAP fields are extracted but never mapped to Odoo.")


if __name__ == "__main__":
    sys.exit(main())
