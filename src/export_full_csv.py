"""
Stage 2b: write EVERY column of EVERY SAP entity set to CSV, not just the columns that map onto
a standard Odoo field.

Why this exists alongside output_odoo/: `transform_odoo.py` deliberately maps only the fields
Odoo has somewhere to put. That is right for a clean import, but it means most of what SAP
returned never reaches a spreadsheet - of the ~2,500 columns pulled, only a few hundred appear
in output_odoo/. This module writes the rest out too, so the migration can create custom fields
in Odoo and load the remainder rather than silently dropping it.

Nothing here re-reads SAP. It works entirely from output_raw/*.json, so it is cheap to re-run.

Output layout (output_full_csv/):

  entities/<service>__<EntitySet>.csv
      One file per SAP entity set, one row per SAP record, EVERY column. A direct, lossless
      transcription of the raw JSON - this is the guarantee that nothing was dropped.

  objects/<service>.csv
      The convenience view: each service's root entity with its one-to-one child entities
      merged in, child columns prefixed "<Child>.<Field>". Children that can have SEVERAL rows
      per parent are NOT merged - flattening them would either duplicate the parent row or throw
      rows away - so they stay in entities/ and are named in the index.

  _INDEX.csv
      One row per entity set: where it came from, how many rows and columns, whether any Odoo
      transform reads it, which Odoo file it feeds, and whether it was merged into an object
      file or left standalone (with the reason).

Where the Odoo external ID for a record can be derived, both layouts carry an
`odoo_external_id` column, so a file can be matched row-for-row against the corresponding
output_odoo/*.csv and used to populate custom fields on already-imported records.

Run: python -m src.export_full_csv
"""

import csv
import json
import logging
import os
import sys

from src.csv_writer import external_id
from src.extract_raw import SOURCES

logger = logging.getLogger("sap2odoo.export_full_csv")

RAW_DIR = "output_raw"
OUT_DIR = "output_full_csv"
ENTITY_DIR = os.path.join(OUT_DIR, "entities")
OBJECT_DIR = os.path.join(OUT_DIR, "objects")
INDEX_PATH = os.path.join(OUT_DIR, "_INDEX.csv")

# (service, entity_set) -> (external-ID prefix, field holding the key)
# Mirrors the external IDs transform_odoo.py writes, so a full-column file can be joined to the
# matching output_odoo/*.csv on `odoo_external_id`. Only entity sets whose rows correspond
# one-to-one with an Odoo record appear here; a child collection has no Odoo record of its own.
ODOO_KEY = {
    ("khcustomer", "CustomerCollection"): ("sap_bp", "InternalID"),
    ("khsupplier", "SupplierCollection"): ("sap_bp", "InternalID"),
    ("khbusinesspartner", "BusinessPartnerCollection"): ("sap_bp", "InternalID"),
    ("vmumaterial", "MaterialCollection"): ("sap_prod", "InternalID"),
    ("khserviceproduct", "ServiceProductCollection"): ("sap_prod", "InternalID"),
    ("khpurchaseorder", "PurchaseOrderCollection"): ("sap_po", "ID"),
    ("khsalesorder", "SalesOrderCollection"): ("sap_so", "ID"),
    ("khcustomerinvoice", "CustomerInvoiceCollection"): ("sap_cinv", "ID"),
    ("khsupplierinvoice", "SupplierInvoiceCollection"): ("sap_vinv", "ObjectID"),
    ("khopportunity", "OpportunityCollection"): ("sap_opp", "ID"),
    ("khemployee", "EmployeeCollection"): ("sap_emp", "ObjectID"),
    ("khpayment", "PaymentCollection"): ("sap_pay", "ObjectID"),
    ("khhousebankstatement", "HouseBankStatementCollection"): ("sap_stmt", "ObjectID"),
    ("khproductionorder", "ProductionOrderCollection"): ("sap_mo", "ID"),
    ("khoutbounddelivery", "OutboundDeliveryCollection"): ("sap_del", "ID"),
    ("khinbounddelivery", "InboundDeliveryCollection"): ("sap_inbdel", "ID"),
    ("khlocation", "LocationCollection"): ("sap_loc", "ObjectID"),
    ("khsalesarrangement", "SalesArrangementCollection"): ("sap_pricelist", "ObjectID"),
    ("khprofitcentre", "ProfitCentreCollection"): ("sap_pc", "ID"),
    ("costcentre", "CostCentreCollection"): ("sap_cost_center", "UUID"),
}


def load_raw_files():
    """-> [{service, entity_set, fields, rows, filename}], sorted, one per output_raw file."""
    out = []
    for filename in sorted(os.listdir(RAW_DIR)):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(RAW_DIR, filename), encoding="utf-8") as f:
            payload = json.load(f)
        out.append({
            "filename": filename,
            "service": payload["service"].rsplit("/", 1)[-1],
            "entity_set": payload["entity_set"],
            "fields": payload["fields_present"],
            "rows": payload["rows"],
        })
    return out


def _cell(value):
    """A CSV-safe scalar. Nested values are kept as JSON rather than silently flattened away."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _odoo_id(service, entity_set, row):
    key = ODOO_KEY.get((service, entity_set))
    if not key:
        return None
    prefix, field = key
    value = row.get(field)
    return external_id(prefix, value) if value else ""


def write_entity_csvs(entities):
    """One CSV per entity set, every column. The lossless part."""
    os.makedirs(ENTITY_DIR, exist_ok=True)
    written = []
    for entity in entities:
        has_odoo_id = (entity["service"], entity["entity_set"]) in ODOO_KEY
        fieldnames = (["odoo_external_id"] if has_odoo_id else []) + entity["fields"]
        path = os.path.join(ENTITY_DIR, entity["filename"].replace(".json", ".csv"))
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in entity["rows"]:
                out = {name: _cell(row.get(name)) for name in entity["fields"]}
                if has_odoo_id:
                    out["odoo_external_id"] = _odoo_id(entity["service"], entity["entity_set"], row)
                writer.writerow(out)
        written.append((path, len(entity["rows"]), len(fieldnames)))
    return written


def _parent_key(row):
    """Children reference their parent's 32-character ObjectID, sometimes inside a 64-character
    concatenation of two ObjectIDs - see _join_by_parent in transform_odoo.py."""
    return (row.get("ParentObjectID") or "")[:32]


def pick_root(entities):
    """
    The entity set the others hang off: the one whose ObjectIDs the most child rows point at.
    Returns None when nothing references anything (a service of independent reports).
    """
    best, best_score = None, 0
    for candidate in entities:
        own_ids = {r.get("ObjectID") for r in candidate["rows"] if r.get("ObjectID")}
        if not own_ids:
            continue
        score = sum(
            1
            for other in entities
            if other is not candidate
            for r in other["rows"]
            if _parent_key(r) in own_ids
        )
        if score > best_score:
            best, best_score = candidate, score
    return best


def write_object_csvs(entities_by_service):
    """
    Per service: the root entity widened with every child that has at most one row per parent.

    A child with several rows per parent is left alone on purpose. Merging it would mean either
    repeating the parent on every child row or keeping one child and discarding the rest, and
    both misrepresent the data - the entities/ file already holds it in full.
    """
    os.makedirs(OBJECT_DIR, exist_ok=True)
    written, decisions = [], []

    for service, entities in sorted(entities_by_service.items()):
        root = pick_root(entities)
        if root is None:
            for entity in entities:
                decisions.append((service, entity["entity_set"], "standalone",
                                  "service has no parent/child structure"))
            continue

        root_ids = {r.get("ObjectID") for r in root["rows"] if r.get("ObjectID")}
        merged_children = []
        for child in entities:
            if child is root:
                continue
            by_parent = {}
            matched = 0
            too_many = False
            for row in child["rows"]:
                parent = _parent_key(row)
                if parent not in root_ids:
                    continue
                matched += 1
                if parent in by_parent:
                    too_many = True
                    break
                by_parent[parent] = row
            if not matched:
                decisions.append((service, child["entity_set"], "standalone",
                                  "does not join to the root entity"))
            elif too_many:
                decisions.append((service, child["entity_set"], "standalone",
                                  "several rows per parent - merging would duplicate or drop rows"))
            else:
                merged_children.append((child, by_parent))
                decisions.append((service, child["entity_set"], "merged",
                                  f"one row per parent ({matched} matched)"))

        has_odoo_id = (service, root["entity_set"]) in ODOO_KEY
        fieldnames = (["odoo_external_id"] if has_odoo_id else []) + list(root["fields"])
        for child, _ in merged_children:
            fieldnames += [f"{child['entity_set'].replace('Collection', '')}.{name}"
                           for name in child["fields"]]

        path = os.path.join(OBJECT_DIR, f"{service}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in root["rows"]:
                out = {name: _cell(row.get(name)) for name in root["fields"]}
                if has_odoo_id:
                    out["odoo_external_id"] = _odoo_id(service, root["entity_set"], row)
                object_id = row.get("ObjectID")
                for child, by_parent in merged_children:
                    child_row = by_parent.get(object_id, {})
                    prefix = child["entity_set"].replace("Collection", "")
                    for name in child["fields"]:
                        out[f"{prefix}.{name}"] = _cell(child_row.get(name))
                writer.writerow(out)
        written.append((path, len(root["rows"]), len(fieldnames), root["entity_set"],
                        len(merged_children)))
    return written, decisions


def write_index(entities, decisions):
    """One row per entity set, so the folder is navigable without opening 376 files."""
    from src.consolidated_report import _read_raw  # noqa: F401  (kept for symmetry of sources)
    from src.registry import REGISTRY
    from src.validate import FILENAME_OVERRIDES

    objects_by_raw = {}
    for obj in REGISTRY:
        for raw_file in obj.raw_sources:
            objects_by_raw.setdefault(raw_file, []).append(obj)

    transform_source = open("src/transform_odoo.py", encoding="utf-8").read()
    decision_by_entity = {(s, e): (d, why) for s, e, d, why in decisions}

    rows = []
    for entity in entities:
        consumers = objects_by_raw.get(entity["filename"], [])
        odoo_files = sorted({
            FILENAME_OVERRIDES.get(o.filename_base, f"{o.filename_base}.csv") for o in consumers
        })
        decision, reason = decision_by_entity.get(
            (entity["service"], entity["entity_set"]), ("standalone", "root entity"))
        rows.append({
            "Entity CSV": f"entities/{entity['filename'].replace('.json', '.csv')}",
            "SAP Service": entity["service"],
            "SAP Entity Set": entity["entity_set"],
            "Rows": len(entity["rows"]),
            "Columns": len(entity["fields"]),
            "In object file": f"objects/{entity['service']}.csv" if decision == "merged" else "",
            "Merge decision": decision,
            "Why": reason,
            # "Read by a transform" answers: is any of this already in output_odoo/?
            "Read by an Odoo transform": "yes" if f'"{entity["entity_set"]}"' in transform_source else "no",
            "Odoo object": " / ".join(o.name for o in consumers),
            "Odoo CSV": " + ".join(odoo_files),
            "Has odoo_external_id": "yes" if (entity["service"], entity["entity_set"]) in ODOO_KEY else "no",
        })

    rows.sort(key=lambda r: (r["SAP Service"], r["SAP Entity Set"]))
    with open(INDEX_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not os.path.isdir(RAW_DIR):
        logger.error("%s/ does not exist - run `python -m src.extract_raw` first.", RAW_DIR)
        return 1

    entities = load_raw_files()
    if not entities:
        logger.error("No raw files in %s/ - run `python -m src.extract_raw` first.", RAW_DIR)
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    entity_files = write_entity_csvs(entities)

    by_service = {}
    for entity in entities:
        by_service.setdefault(entity["service"], []).append(entity)
    object_files, decisions = write_object_csvs(by_service)

    index_rows = write_index(entities, decisions)

    total_rows = sum(count for _, count, _ in entity_files)
    total_cols = sum(cols for _, _, cols in entity_files)
    unmapped = sum(1 for r in index_rows if r["Read by an Odoo transform"] == "no")

    logger.info("%s/", OUT_DIR)
    logger.info("  entities/  %d CSVs - every entity set, every column (%d rows, %d columns total)",
                len(entity_files), total_rows, total_cols)
    logger.info("  objects/   %d CSVs - root entity widened with its one-to-one children",
                len(object_files))
    logger.info("  _INDEX.csv %d rows - what is in each file and why", len(index_rows))
    logger.info("")
    logger.info("  %d of %d entity sets are NOT read by any Odoo transform - that data exists "
                "only here.", unmapped, len(index_rows))
    logger.info("  %d entity sets carry an odoo_external_id column for matching against "
                "output_odoo/.", sum(1 for r in index_rows if r["Has odoo_external_id"] == "yes"))

    widest = sorted(object_files, key=lambda o: -o[2])[:5]
    if widest:
        logger.info("")
        logger.info("  Widest object files:")
        for path, rows, cols, root, children in widest:
            logger.info("    %-46s %5d rows %4d columns (%s + %d child entities)",
                        path, rows, cols, root, children)
    return 0


if __name__ == "__main__":
    sys.exit(main())
