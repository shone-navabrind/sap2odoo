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

    odoo_models/<odoo_model_file>.csv
            The recommended business-facing layout.  It mirrors output_odoo/'s model-named files and
            starts with the normal Odoo import columns, followed by the complete linked SAP source
            fields.  One-to-many child values are JSON arrays, preserving every value without
            duplicating an Odoo record.

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

    _MODEL_INDEX.csv
            One row per odoo_models/ CSV, recording source lineage and direct link coverage.

Where the Odoo external ID for a record can be derived, both layouts carry an
`odoo_external_id` column, so a file can be matched row-for-row against the corresponding
output_odoo/*.csv and used to populate custom fields on already-imported records.

Run: python -m src.export_full_csv
"""

import csv
import gc
import json
import logging
import os
import sys
import time

from src.csv_writer import external_id
from src.extract_raw import SOURCES
from src.sysmem import low_memory

logger = logging.getLogger("sap2odoo.export_full_csv")

RAW_DIR = "output_raw"
OUT_DIR = "output_full_csv"
ENTITY_DIR = os.path.join(OUT_DIR, "entities")
OBJECT_DIR = os.path.join(OUT_DIR, "objects")
MODEL_DIR = os.path.join(OUT_DIR, "odoo_models")
INDEX_PATH = os.path.join(OUT_DIR, "_INDEX.csv")
MODEL_INDEX_PATH = os.path.join(OUT_DIR, "_MODEL_INDEX.csv")

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

# Odoo CSV filename -> root SAP records that create its rows.  This is deliberately separate
# from ODOO_KEY: ODOO_KEY annotates a source entity, whereas this table records the exact
# external-ID convention used by the transform.  Keeping the lineage here prevents the old
# service-named convenience files from being the only way to access complete source columns.
#
# A root's own fields are copied as scalar columns.  Every same-service child that points to
# that root through ParentObjectID is represented too; repeating children are JSON arrays in a
# single cell, so no parent is duplicated and no child value is discarded.
MODEL_ROOTS = {
    "account_account.csv": [
        ("fin_costandrevenue_analytics.svc", "RPFINCACU04_Q0002QueryResults", "sap_account", ("CGLACCT",)),
        ("fin_audit_analytics.svc", "RPFINGLAU02_Q0002QueryResults", "sap_account", ("CGLACCT",)),
        ("fin_generalledger_analytics.svc", "RPFINFXAU05_Q0001QueryResults", "sap_account", ("CGLACCT",)),
        ("fin_generalledger_analytics.svc", "RPFINFCDU02_Q0001QueryResults", "sap_account", ("CGLACCT",)),
        ("fin_audit_analytics.svc", "RPFININVU03_Q0001QueryResults", "sap_account", ("CGLACCT",)),
        ("fin_audit_analytics.svc", "RPFINGLAU02_Q0003QueryResults", "sap_account", ("CGLACCT",)),
    ],
    "res_partner.csv": [("khcustomer", "CustomerCollection", "sap_bp", ("InternalID",)),
                        ("khsupplier", "SupplierCollection", "sap_bp", ("InternalID",))],
    "purchase_order.csv": [("khpurchaseorder", "PurchaseOrderCollection", "sap_po", ("ObjectID",))],
    "purchase_order_rfq.csv": [("khpurchaseorder", "PurchaseOrderCollection", "sap_rfq", ("ID", "ObjectID"))],
    "purchase_order_line.csv": [("khpurchaseorder", "ItemCollection", "sap_po_item", ("ObjectID",))],
    "sale_order.csv": [("khsalesorder", "SalesOrderCollection", "sap_so", ("ObjectID",))],
    "sale_order_line.csv": [("khsalesorder", "ItemCollection", "sap_so_item", ("ObjectID",))],
    "account_move_customer_invoice.csv": [("khcustomerinvoice", "CustomerInvoiceCollection", "sap_cinv", ("ObjectID",))],
    "account_move_customer_invoice_line.csv": [("khcustomerinvoice", "ItemCollection", "sap_cinv_item", ("ObjectID",))],
    "account_move_vendor_bill.csv": [("khsupplierinvoice", "SupplierInvoiceCollection", "sap_vinv", ("ObjectID",))],
    "account_move_open_vendor.csv": [("khsupplierinvoice", "SupplierInvoiceCollection", "sap_vinv", ("ObjectID",))],
    "account_move_vendor_bill_line.csv": [("khsupplierinvoice", "ItemCollection", "sap_vinv_item", ("ObjectID",))],
    "account_move_vendor_credit.csv": [("khsupplierinvoice", "SupplierInvoiceCollection", "sap_vcredit", ("ID", "ObjectID"))],
    "account_move_credit_note.csv": [("khcustomerinvoicerequest", "CustomerInvoiceRequestCollection", "sap_ccredit", ("BaseBusinessTransactionDocumentID", "ObjectID"))],
    "account_move_open_customer.csv": [("fin_receivablesar_analytics.svc", "RPFINDUEU04_Q0007QueryResults", "sap_openinv", ("CIM_B_BTD_ID",))],
    "account_payment_term.csv": [("khcustomerinvoice", "CashDiscountTermsCollection", "sap_payterm", ("PaymentTermsCode",)),
                                 ("khsupplierinvoice", "CashDiscountTermsCollection", "sap_payterm", ("Code",))],
    "product_template.csv": [("vmumaterial", "MaterialCollection", "sap_prod", ("InternalID", "ObjectID")),
                             ("khserviceproduct", "ServiceProductCollection", "sap_prod", ("InternalID",))],
    "product_category.csv": [("vmumaterial", "ProductCategoryCollection", "sap_prodcat", ("ProductCategoryInternalID",)),
                             ("khserviceproduct", "ProductCategoryCollection", "sap_prodcat", ("ProductCategoryInternalID",))],
    "crm_lead.csv": [("khopportunity", "OpportunityCollection", "sap_opp", ("ObjectID",))],
    "crm_lead_open.csv": [("khopportunity", "OpportunityCollection", "sap_opp", ("ObjectID",))],
    "crm_lead_closed.csv": [("khopportunity", "OpportunityCollection", "sap_opp", ("ObjectID",))],
    "res_users.csv": [("khemployee", "EmployeeCollection", "sap_emp", ("ObjectID",))],
    "res_partner_contact.csv": [("khcustomer", "RelationshipCollection", "sap_contact", ("InternalID2",))],
    "res_bank.csv": [("khhousebankaccount", "BankDirectoryEntryCollection", "sap_bank", ("OrganisationFormattedName",))],
    "account_bank_statement.csv": [("khhousebankstatement", "HouseBankStatementCollection", "sap_bstmt", ("ObjectID",))],
    "account_payment.csv": [("khpayment", "PaymentCollection", "sap_pay", ("ObjectID",))],
    "product_pricelist.csv": [("khsalesarrangement", "SalesArrangementCollection", "sap_pricelist", ("ObjectID",))],
    "stock_picking_delivery.csv": [("khoutbounddelivery", "OutboundDeliveryCollection", "sap_delivery", ("ObjectID",))],
    "stock_picking_delivery_line.csv": [("khoutbounddelivery", "ItemCollection", "sap_delivery_item", ("ObjectID",))],
    "mrp_production.csv": [("khproductionorder", "ProductionOrderCollection", "sap_mo", ("ObjectID",))],
    "mrp_workcenter.csv": [("khproductionorder", "OperationCollection", "sap_wc", ("ResourceID",))],
    "mrp_production_history.csv": [("khproductionorder", "ProductionOrderCollection", "sap_mo_hist", ("ID", "ObjectID"))],
    "account_analytic_account.csv": [("khprofitcentre", "ProfitCentreCollection", "sap_pc", ("ID",))],
    "account_analytic_account_cc.csv": [("costcentre", "CostCentreCollection", "sap_cost_center", ("UUID", "ObjectID", "ID"))],
    "stock_location.csv": [("khlocation", "LocationCollection", "sap_loc", ("ObjectID",))],
    "stock_warehouse.csv": [("khlocation", "LocationCollection", "sap_wh", ("ObjectID",))],
    "stock_picking_transfer.csv": [("khinbounddelivery", "InboundDeliveryCollection", "sap_inbdel", ("ID",))],
    "stock_picking_transfer_line.csv": [("khinbounddelivery", "ItemCollection", "sap_inbdel_item", ("ObjectID",))],
    "stock_move_history.csv": [("khgoodsandserviceacknowledgement", "ItemCollection", "sap_move", ("ObjectID",))],
    "uom_uom.csv": [("vmumaterial", "MaterialBaseMeasureUnitCodeCollection", "sap_uom", ("Code",))],
}


def list_service_names():
    """
    Every service with at least one output_raw/*.json file, sorted - just filenames, nothing
    loaded into memory. Used to process one service's data at a time instead of holding the
    entire tenant's raw JSON in memory simultaneously (see main()'s docstring note).
    """
    return sorted({f.split("__", 1)[0] for f in os.listdir(RAW_DIR) if f.endswith(".json")})


def load_raw_files_for_service(service):
    """
    -> [{service, entity_set, fields, rows, filename}] for just ONE service's raw files.

    Loading service-by-service (rather than the whole tenant via one load_raw_files() call
    holding everything at once) is what keeps this module's peak memory bounded: on the live
    tenant, several single entity sets are hundreds of MB of JSON on disk (khsupplierinvoice
    alone has multiple 680k-row entities) - parsed into Python objects and held alongside
    dozens of siblings, the whole-tenant version of this function grew past 7GB and got
    OOM-killed by the OS. One service at a time keeps the peak to whatever that single
    service's data costs, which every service on this tenant fits well within available memory.
    """
    out = []
    prefix = f"{service}__"
    for filename in sorted(os.listdir(RAW_DIR)):
        if not filename.startswith(prefix) or not filename.endswith(".json"):
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


def _sap_column(entity, field):
    """A collision-proof, import-friendly source column name."""
    return f"sap__{entity['service']}__{entity['entity_set']}__{field}"


def _root_external_id(prefix, key_fields, row):
    for field in key_fields:
        value = row.get(field)
        if value:
            return external_id(prefix, value)
    return ""


def _apply_root_to_model(output_rows, matched_ids, fieldnames, seen_fields, service, root_name, prefix, key_fields):
    """
    Load ONE service's entities (on demand, discarded when this returns), join its root+children
    onto `output_rows` in place, and append any newly-seen SAP columns to `fieldnames`.

    This is the per-root building block write_odoo_model_csvs() calls once per (service,
    root_name) pair in a model's MODEL_ROOTS entry - never loading more than one service's raw
    data into memory at a time, which is what keeps this module's peak memory bounded (see
    load_raw_files_for_service's docstring).
    """
    if not key_fields:
        return
    if low_memory():
        logger.warning("Low memory before loading %s for %s - pausing 10s", service, root_name)
        gc.collect()
        time.sleep(10)
    service_entities = load_raw_files_for_service(service)
    root = next((e for e in service_entities if e["entity_set"] == root_name), None)
    if not root:
        return

    for field in root["fields"]:
        column = _sap_column(root, field)
        if column not in seen_fields:
            fieldnames.append(column)
            seen_fields.add(column)

    roots_by_id = {
        _root_external_id(prefix, key_fields, row): row
        for row in root["rows"]
        if _root_external_id(prefix, key_fields, row)
    }
    # The root itself and ParentObjectID children are linkable. Other entity sets in this
    # service stay in entities/ only, to avoid attaching unrelated records to every Odoo row.
    children = [e for e in service_entities if e is not root and any(r.get("ParentObjectID") for r in e["rows"])]

    # Index each child once by parent key instead of rescanning its full row list for every
    # output row - a linear scan here made this O(rows * child_rows), which took hours (and had
    # to be killed) on services like khsupplierinvoice where a 55k-row parent joins against
    # 680k-row children.
    children_indexed = []
    for child in children:
        for field in child["fields"]:
            column = _sap_column(child, field)
            if column not in seen_fields:
                fieldnames.append(column)
                seen_fields.add(column)
        by_parent = {}
        for row in child["rows"]:
            parent = _parent_key(row)
            if parent:
                by_parent.setdefault(parent, []).append(row)
        children_indexed.append((child, by_parent))

    for out in output_rows:
        source_row = roots_by_id.get(out.get("id", ""))
        if not source_row:
            continue
        matched_ids.add(out["id"])
        for field in root["fields"]:
            out[_sap_column(root, field)] = _cell(source_row.get(field))

        object_id = source_row.get("ObjectID")
        if not object_id:
            continue
        for child, by_parent in children_indexed:
            child_rows = by_parent.get(object_id)
            if not child_rows:
                continue
            for field in child["fields"]:
                values = [_cell(row.get(field)) for row in child_rows]
                # Scalar for a genuine 1:1 child, JSON array for all multi-row children.
                out[_sap_column(child, field)] = values[0] if len(values) == 1 else json.dumps(values)


def write_odoo_model_csvs():
    """
    Write the business-facing full export: the same model-named files as output_odoo/, with
    Odoo import columns first and every safely linkable SAP column after them.

    Values from a one-to-many SAP child are a JSON array in one cell.  This preserves all child
    rows while retaining the Odoo model's one-row-per-record grain.  Raw entities that cannot
    be linked unambiguously remain available in entities/, the lossless audit layout.

    Loads one service at a time (see _apply_root_to_model), never the whole tenant's raw data
    at once - a model whose roots span several services (e.g. account_account.csv across 6
    analytics services) costs one service's memory at a time, not all of them simultaneously.
    """
    os.makedirs(MODEL_DIR, exist_ok=True)
    for filename in os.listdir(MODEL_DIR):
        if filename.endswith(".csv"):
            os.remove(os.path.join(MODEL_DIR, filename))

    results = []
    for filename in sorted(os.listdir("output_odoo")):
        if not filename.endswith(".csv"):
            continue
        with open(os.path.join("output_odoo", filename), newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            odoo_fields = reader.fieldnames or []
            odoo_rows = list(reader)

        roots = MODEL_ROOTS.get(filename, [])
        fieldnames = list(odoo_fields)
        seen_fields = set(fieldnames)
        output_rows = [dict(row) for row in odoo_rows]
        matched_ids = set()

        for service, root_name, prefix, key_fields in roots:
            _apply_root_to_model(output_rows, matched_ids, fieldnames, seen_fields,
                                  service, root_name, prefix, key_fields)

        path = os.path.join(MODEL_DIR, filename)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(output_rows)
        results.append({
            "Odoo model CSV": f"odoo_models/{filename}",
            "Rows": len(output_rows),
            "Odoo columns": len(odoo_fields),
            "All-column export columns": len(fieldnames),
            "Root SAP source(s)": " + ".join(f"{service}/{entity_set}" for service, entity_set, _, _ in roots),
            "Rows linked to SAP": len(matched_ids),
            "Rows without direct SAP lineage": len(output_rows) - len(matched_ids),
            "Notes": ("SAP child fields use JSON arrays where a record has several child rows; "
                      "unlinked entities remain losslessly available in entities/"),
        })

    with open(MODEL_INDEX_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    return results


def write_index(entity_meta, decisions):
    """
    One row per entity set, so the folder is navigable without opening 569 files.

    `entity_meta` is lightweight - {filename, service, entity_set, row_count, field_count} per
    entity, not the full row data - collected in main()'s per-service pass instead of requiring
    a second full load of everything just to count rows and columns.
    """
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
    for entity in entity_meta:
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
            "Rows": entity["row_count"],
            "Columns": entity["field_count"],
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
    """
    Runs in three passes, each processing ONE SAP service's raw data at a time rather than
    loading the whole tenant into memory at once (see load_raw_files_for_service's docstring -
    the whole-tenant version of this got OOM-killed at 7+GB RSS on the live tenant's full
    extraction). Pass 1 writes entities/ + objects/ and collects lightweight per-entity metadata
    (row/column counts only) for the index. Pass 2 writes odoo_models/, loading each root's
    service on demand as it's needed rather than requiring everything pre-loaded. Pass 3 writes
    the index files from the metadata collected in pass 1.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not os.path.isdir(RAW_DIR):
        logger.error("%s/ does not exist - run `python -m src.extract_raw` first.", RAW_DIR)
        return 1

    services = list_service_names()
    if not services:
        logger.error("No raw files in %s/ - run `python -m src.extract_raw` first.", RAW_DIR)
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)

    entity_files = []
    object_files = []
    decisions = []
    entity_meta = []
    for service in services:
        # Real backpressure, not just a log line: if the machine is genuinely low on memory
        # right now (competing with whatever else is running on it), wait and let things settle
        # rather than loading another potentially-large service on top of an already-tight
        # situation - this is what export_full_csv.py didn't have the first time and got
        # OOM-killed. Capped retries so a permanently memory-starved machine doesn't hang
        # forever; it proceeds anyway after that, logging that it's doing so under pressure.
        for attempt in range(5):
            if not low_memory():
                break
            logger.warning("Low memory before loading %s (attempt %d/5) - pausing 10s for it "
                           "to free up before continuing", service, attempt + 1)
            gc.collect()
            time.sleep(10)

        service_entities = load_raw_files_for_service(service)
        entity_files.extend(write_entity_csvs(service_entities))
        for entity in service_entities:
            entity_meta.append({
                "filename": entity["filename"], "service": entity["service"],
                "entity_set": entity["entity_set"], "row_count": len(entity["rows"]),
                "field_count": len(entity["fields"]),
            })
        service_object_files, service_decisions = write_object_csvs({service: service_entities})
        object_files.extend(service_object_files)
        decisions.extend(service_decisions)
        # Free this service's row data before loading the next one - the largest single
        # service on the live tenant is over 1GB of raw JSON text (several GB parsed), so an
        # explicit collect here (rather than waiting for the next assignment's refcount drop)
        # matters when running alongside other memory pressure on the machine.
        del service_entities
        gc.collect()

    model_files = write_odoo_model_csvs()

    index_rows = write_index(entity_meta, decisions)

    total_rows = sum(count for _, count, _ in entity_files)
    total_cols = sum(cols for _, _, cols in entity_files)
    unmapped = sum(1 for r in index_rows if r["Read by an Odoo transform"] == "no")

    logger.info("%s/", OUT_DIR)
    logger.info("  entities/  %d CSVs - every entity set, every column (%d rows, %d columns total)",
                len(entity_files), total_rows, total_cols)
    logger.info("  objects/   %d CSVs - root entity widened with its one-to-one children",
                len(object_files))
    logger.info("  odoo_models/ %d CSVs - output_odoo-style model files with linked SAP columns",
                len(model_files))
    logger.info("  _MODEL_INDEX.csv - per-model lineage, columns, and link coverage")
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
