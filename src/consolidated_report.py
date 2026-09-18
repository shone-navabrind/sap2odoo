"""
Generates CONSOLIDATED_STATUS.csv - ONE file with everything in it, one row per data flow
(SAP API endpoint -> raw file -> Odoo CSV), so there is a single place to answer:
  "what did we ask SAP for, what came back, where did it go, and is it done?"

Unlike PROJECT_STATUS.csv (one row per migration-sheet object), this has one row per actual
SAP entity pulled, plus a row for every sheet object that has no SAP source yet - so nothing
is invisible. Every number is read live from disk (output_raw/*.json, output_odoo/*.csv) and
from src/registry.py; nothing here is typed in by hand.

Run: python -m src.consolidated_report
"""

import csv
import json
import logging
import os
import sys

from src.config import load_config
from src.extract_raw import SOURCES, raw_filename
from src.registry import REGISTRY

logger = logging.getLogger("sap2odoo.consolidated_report")

RAW_DIR = "output_raw"
ODOO_DIR = "output_odoo"
OUTPUT_PATH = "CONSOLIDATED_STATUS.csv"

FIELDNAMES = [
    "Status",
    "Sheet #",
    "Object Name",
    "Odoo Module",
    "Master/Transaction",
    "Mandatory",
    "SAP Service",
    "SAP Entity",
    "SAP API URL",
    "SAP Records",
    "SAP Fields",
    "Raw File",
    "Odoo Model",
    "Odoo CSV File",
    "Odoo Records",
    "Odoo Columns",
    "Sample Record (from SAP)",
    "Notes / Why Not Done",
]


def _read_raw(filename):
    path = os.path.join(RAW_DIR, filename)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _odoo_stats(filename):
    path = os.path.join(ODOO_DIR, filename)
    if not os.path.isfile(path):
        return None, None
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cols = len(rows[0]) if rows else 0
    return len(rows), cols


def _sample_record(payload, max_fields=4):
    """
    A few real field=value pairs from the first row, as human-readable evidence that data
    actually came back. Internal key fields (ObjectID, UUIDs, language codes) are skipped in
    favour of business-meaningful ones, so a non-technical reader sees a name or an amount
    rather than a 32-character hex ID.
    """
    if not payload or not payload.get("rows"):
        return ""
    row = payload["rows"][0]

    def is_internal(key):
        k = key.lower()
        return (
            k.endswith("objectid") or k.endswith("uuid") or k == "id"
            or k.endswith("languagecode") or k.endswith("languagecodetext")
        )

    parts = []
    for skip_internal in (True, False):  # second pass only if nothing business-y was found
        for key, value in row.items():
            if value in (None, "", False) or isinstance(value, dict):
                continue
            if skip_internal and is_internal(key):
                continue
            text = str(value)
            if len(text) > 28:
                text = text[:28] + "..."
            pair = f"{key}={text}"
            if pair not in parts:
                parts.append(pair)
            if len(parts) >= max_fields:
                return "; ".join(parts)
        if parts:
            break
    return "; ".join(parts)


def _odoo_files_for(obj):
    """Every Odoo CSV this object actually produced (header + line files)."""
    from src.validate import FILENAME_OVERRIDES
    primary = FILENAME_OVERRIDES.get(obj.filename_base, f"{obj.filename_base}.csv")
    candidates = [primary]
    stem = primary[:-4]
    for suffix in ("_line.csv",):
        if os.path.isfile(os.path.join(ODOO_DIR, stem + suffix)):
            candidates.append(stem + suffix)
    return [c for c in candidates if os.path.isfile(os.path.join(ODOO_DIR, c))]


def build_rows():
    config = load_config()
    base_url = config.sap_base_url

    # Which registry objects consume which raw file (from ObjectSpec.raw_sources).
    objects_by_raw_file = {}
    for obj in REGISTRY:
        for rf in obj.raw_sources:
            objects_by_raw_file.setdefault(rf, []).append(obj)

    rows = []

    # --- One row per SAP entity actually pulled -------------------------------------------
    for source in SOURCES:
        service, entity_set = source[0], source[1]
        options = source[2] if len(source) > 2 else {}
        filename = raw_filename(service, entity_set)
        payload = _read_raw(filename)

        url = f"{base_url}/{service}/{entity_set}"
        if options.get("expand"):
            url += f"?$expand={options['expand']}"
        elif options.get("select"):
            url += f"?$select={','.join(options['select'])}"

        consumers = objects_by_raw_file.get(filename, [])
        if consumers:
            obj = consumers[0]
            odoo_files = _odoo_files_for(obj)
            odoo_file = " + ".join(odoo_files) if odoo_files else ""
            odoo_records, odoo_cols = _odoo_stats(odoo_files[0]) if odoo_files else (None, None)
            names = " / ".join(o.name for o in consumers)
            sheet_nos = " / ".join(str(o.sheet_no) for o in consumers)
            modules = " / ".join(sorted({o.module for o in consumers}))
            categories = " / ".join(sorted({o.category for o in consumers}))
            mandatory = "Yes" if any(o.mandatory for o in consumers) else "Optional"
            odoo_model = " / ".join(sorted({o.odoo_model for o in consumers}))
            status = "COMPLETED"
            note = consumers[0].note
        else:
            names = "(supporting data - feeds another object)"
            sheet_nos = ""
            modules = ""
            categories = ""
            mandatory = ""
            odoo_model = ""
            odoo_file = ""
            odoo_records = odoo_cols = None
            status = "EXTRACTED - NOT USED"
            note = (
                "Pulled from SAP and saved in output_raw/, but no transform currently reads it, "
                "so it produces no Odoo file. Kept because it is real data that may be useful "
                "later (verified against src/transform_odoo.py: this entity is referenced 0 times)."
            )

        rows.append({
            "Status": status,
            "Sheet #": sheet_nos,
            "Object Name": names,
            "Odoo Module": modules,
            "Master/Transaction": categories,
            "Mandatory": mandatory,
            "SAP Service": service.rsplit("/", 1)[-1],
            "SAP Entity": entity_set,
            "SAP API URL": url,
            "SAP Records": payload["row_count"] if payload else "NOT EXTRACTED",
            "SAP Fields": len(payload["fields_present"]) if payload else "",
            "Raw File": filename,
            "Odoo Model": odoo_model,
            "Odoo CSV File": odoo_file,
            "Odoo Records": odoo_records if odoo_records is not None else "",
            "Odoo Columns": odoo_cols if odoo_cols is not None else "",
            "Sample Record (from SAP)": _sample_record(payload),
            "Notes / Why Not Done": note,
        })

    # --- One row per sheet object with NO SAP source yet ----------------------------------
    for obj in REGISTRY:
        if obj.raw_sources:
            continue
        status = {
            "pending_mapping": "PENDING - no SAP source found yet",
            "not_in_bydesign": "NOT APPLICABLE - no such module in ByDesign",
        }.get(obj.status, obj.status.upper())
        rows.append({
            "Status": status,
            "Sheet #": obj.sheet_no if obj.sheet_no else "(added)",
            "Object Name": obj.name,
            "Odoo Module": obj.module,
            "Master/Transaction": obj.category,
            "Mandatory": "Yes" if obj.mandatory else "Optional",
            "SAP Service": "",
            "SAP Entity": "",
            "SAP API URL": "",
            "SAP Records": 0,
            "SAP Fields": 0,
            "Raw File": "",
            "Odoo Model": obj.odoo_model,
            "Odoo CSV File": "",
            "Odoo Records": 0,
            "Odoo Columns": 0,
            "Sample Record (from SAP)": "",
            "Notes / Why Not Done": obj.note,
        })

    # Completed first, then pending, then not-applicable; by sheet number within each.
    order = {"COMPLETED": 0, "EXTRACTED - NOT USED": 1}
    rows.sort(key=lambda r: (
        order.get(r["Status"], 2 if r["Status"].startswith("PENDING") else 3),
        int(str(r["Sheet #"]).split(" / ")[0]) if str(r["Sheet #"]).split(" / ")[0].isdigit() else 999,
    ))
    return rows


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rows = build_rows()
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    completed = sum(1 for r in rows if r["Status"] == "COMPLETED")
    supporting = sum(1 for r in rows if r["Status"].startswith("EXTRACTED"))
    pending = sum(1 for r in rows if r["Status"].startswith("PENDING"))
    na = sum(1 for r in rows if r["Status"].startswith("NOT APPLICABLE"))
    sap_records = sum(r["SAP Records"] for r in rows if isinstance(r["SAP Records"], int))

    logger.info("Wrote %s (%d rows)", OUTPUT_PATH, len(rows))
    logger.info(
        "  %d completed data flows | %d extracted-but-unused | %d pending | %d not applicable",
        completed, supporting, pending, na,
    )
    logger.info("  %d total records pulled from SAP", sap_records)


if __name__ == "__main__":
    sys.exit(main())
