"""
Ad-hoc CSV export: pick an object and a list of field names exactly as they appear on the SAP
ByDesign screen, get back a CSV with just those columns.

Run: python -m src.export_by_fields --object products --fields "Product ID,Product Description,Base UoM"
     python -m src.export_by_fields --object products --list-fields
     python -m src.export_by_fields --list-objects

Works entirely offline against output_full_csv/odoo_models/*.csv (already-extracted, already
merged data) - no SAP calls, so it's instant no matter how large the object is.

Each object's FIELD_MAP below is a curated {SAP screen label: source column} lookup, built by
opening the real SAP screen and matching what's visible to the real column already sitting in
output_full_csv/odoo_models/. A label mapped to None means: checked directly against this
object's live $metadata, the field is visible on the SAP screen but genuinely does not exist in
the OData service - not a missed extraction, a real gap in what SAP exposes. Passing such a
label with --fields still produces a column, filled with the documented reason instead of data,
so a "not available" is loud in the output rather than a silently blank column.

Adding a new object: open its real SAP maintenance screen, and for each field visible, find (or
confirm the absence of) the matching column in output_full_csv/odoo_models/<file>.csv - then add
one FIELD_MAP entry here. Do not guess a mapping without checking the real screen and the real
column list; a wrong mapping is worse than an honest "not yet mapped" error.
"""

import argparse
import csv
import json
import logging
import os
import sys

logger = logging.getLogger("sap2odoo.export_by_fields")

FULL_CSV_DIR = os.path.join("output_full_csv", "odoo_models")
OUT_DIR = "output_custom_exports"

# {SAP screen label: source column in output_full_csv/odoo_models/<file>, or (None, reason)}
FIELD_MAPS = {
    "products": {
        "_file": "product_template.csv",
        "_source_screen": "Product and Service Portfolio -> Products -> <product> -> General/"
                           "Purchasing/Logistics/Sales/Valuation/Taxes (validated live 2026-09-28 "
                           "against product 730511, Cover Assy with PCB Bajaj)",
        "fields": {
            "Product ID": "sap__vmumaterial__MaterialCollection__InternalID",
            "Product Description": "sap__vmumaterial__MaterialCollection__Description",
            "Product Category": "sap__vmumaterial__ProductCategoryCollection__Description",
            "Base UoM": "sap__vmumaterial__MaterialCollection__BaseMeasureUnitCodeText",
            "Identified Stock Type": "sap__vmumaterial__MaterialCollection__IdentifiedStockTypeCodeText",
            "Purchasing UoM": "sap__vmumaterial__PurchasingCollection__PurchasingMeasureUnitCodeText",
            "Purchasing Status": "sap__vmumaterial__PurchasingCollection__LifeCycleStatusCodeText",
            "Sales UoM": "sap__vmumaterial__SalesCollection__SalesMeasureUnitCodeText",
            "Item Group": "sap__vmumaterial__SalesCollection__ItemGroupCodeText",
            "Minimum Order Quantity": "sap__vmumaterial__SalesCollection__MinimumOrderQuantity",
            "Cash Discount Allowed": "sap__vmumaterial__SalesCollection__CashDiscountDeductibleIndicator",
            "Reference Price Material": "sap__vmumaterial__SalesCollection__ReferencePriceMaterialID",
            "Sales Organization": "sap__vmumaterial__SalesCollection__SalesOrganisationID",
            "Distribution Channel": "sap__vmumaterial__SalesCollection__DistributionChannelCode",
            "Inventory Valuation UoM": "sap__vmumaterial__MaterialCollection__BaseMeasureUnitCodeText",
            "Valuation Level Type": "sap__vmumaterial__MaterialCollection__ValuationLevelTypeCodeText",
            "Company": "sap__vmumaterial__ValuationCollection__CompanyID",
            "Business Residence": "sap__vmumaterial__ValuationCollection__BusinessResidenceID",
            "Valuation Status": "sap__vmumaterial__ValuationCollection__LifeCycleStatusCodeText",
            "Standard Price": "standard_price",
            "Supplier": "sap__vmumaterial__SupplierInformationCollection__BusinessPartnerFormattedName",
            "Supplier Part Number": "sap__vmumaterial__SupplierInformationCollection__SupplierPartNumber",
            "Supplier Lead Time": "sap__vmumaterial__SupplierInformationCollection__SupplierLeadTimeDuration",
            "Cycle Count": "sap__vmumaterial__LogisticsCollection__CycleCountPlannedDuration",
            "Site": "sap__vmumaterial__LogisticsCollection__SiteName",
            "Serial Number Profile": "sap__vmumaterial__MaterialCollection__SerialNumberProfileCodeText",
            # Checked live against vmumaterial's own $metadata on 2026-09-28 - genuinely absent,
            # not unextracted. If these are needed, the custom vmumaterial OData service needs to
            # be edited (via the OData Editor) to add them, the same way khbomvariant/
            # khequipmentresource/khbatch were built - they are NOT in output_raw/ at all today.
            "HSN Code for India": (None, "Not exposed by the vmumaterial custom OData service - "
                                          "confirmed absent from its live $metadata, 2026-09-28"),
            "MRP for India": (None, "Not exposed by the vmumaterial custom OData service - "
                                     "confirmed absent from its live $metadata, 2026-09-28"),
            "Batch Managed": (None, "Not exposed by the vmumaterial custom OData service - "
                                     "confirmed absent from its live $metadata, 2026-09-28"),
            "Storage Location": (None, "Not exposed by the vmumaterial custom OData service - "
                                        "confirmed absent from its live $metadata, 2026-09-28"),
            "Manufacturer Name": (None, "Not exposed by the vmumaterial custom OData service - "
                                         "confirmed absent from its live $metadata, 2026-09-28"),
        },
    },
}


def _flatten_first(value):
    """
    A one-to-many source column arrives as a JSON array (one entry per child row, per
    output_full_csv's own convention). This tool exports one row per product, so it takes the
    first child's value - documented, not hidden - rather than silently picking one.
    """
    if not value:
        return ""
    if value.startswith("[") :
        try:
            parsed = json.loads(value)
            return parsed[0] if parsed else ""
        except (json.JSONDecodeError, IndexError):
            return value
    return value


def export(object_name, field_labels, output_path):
    spec = FIELD_MAPS.get(object_name)
    if not spec:
        logger.error("Unknown --object %r. Known objects: %s", object_name, ", ".join(FIELD_MAPS))
        return 1

    field_map = spec["fields"]
    unknown = [f for f in field_labels if f not in field_map]
    if unknown:
        logger.error(
            "These field labels aren't mapped yet for %r: %s\n"
            "Run --list-fields to see what's available, or add them to FIELD_MAPS in "
            "src/export_by_fields.py after checking the real SAP screen.",
            object_name, ", ".join(unknown),
        )
        return 1

    src_path = os.path.join(FULL_CSV_DIR, spec["_file"])
    if not os.path.exists(src_path):
        logger.error("%s not found - run `python -m src.export_full_csv` first.", src_path)
        return 1

    with open(src_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    out_rows = []
    not_available = {f: field_map[f][1] for f in field_labels if isinstance(field_map[f], tuple)}
    for row in rows:
        out_row = {}
        for label in field_labels:
            mapped = field_map[label]
            if isinstance(mapped, tuple):
                out_row[label] = f"NOT AVAILABLE - {mapped[1]}"
            else:
                out_row[label] = _flatten_first(row.get(mapped, ""))
        out_rows.append(out_row)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=field_labels)
        writer.writeheader()
        writer.writerows(out_rows)

    logger.info("Wrote %s (%d rows, %d columns)", output_path, len(out_rows), len(field_labels))
    if not_available:
        logger.warning(
            "%d of the requested fields are NOT available in SAP's export for %r (filled with "
            "the reason instead of data): %s",
            len(not_available), object_name, ", ".join(not_available),
        )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--object", help="Object to export from (see --list-objects)")
    parser.add_argument("--fields", help="Comma-separated field labels, exactly as they appear on the SAP screen")
    parser.add_argument("--output", help="Output CSV path (default: output_custom_exports/<object>.csv)")
    parser.add_argument("--list-objects", action="store_true", help="List every object this tool currently supports")
    parser.add_argument("--list-fields", action="store_true", help="List every field label mapped for --object")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.list_objects:
        for name, spec in FIELD_MAPS.items():
            print(f"{name}  ({len(spec['fields'])} fields mapped, source: {spec['_file']})")
        return 0

    if args.list_fields:
        spec = FIELD_MAPS.get(args.object)
        if not spec:
            logger.error("Unknown --object %r. Known objects: %s", args.object, ", ".join(FIELD_MAPS))
            return 1
        print(f"Source screen: {spec['_source_screen']}\n")
        for label, mapped in spec["fields"].items():
            status = f"NOT AVAILABLE - {mapped[1]}" if isinstance(mapped, tuple) else mapped
            print(f"  {label!r:45} -> {status}")
        return 0

    if not args.object or not args.fields:
        parser.error("--object and --fields are required (or use --list-objects / --list-fields)")

    field_labels = [f.strip() for f in args.fields.split(",") if f.strip()]
    output_path = args.output or os.path.join(OUT_DIR, f"{args.object}.csv")
    return export(args.object, field_labels, output_path)


if __name__ == "__main__":
    sys.exit(main())
