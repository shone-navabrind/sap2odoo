"""
Generates PROJECT_STATUS.csv at the project root - a single file the user can hand to their
team showing exactly what's done and what's left, across all 66 sheet objects (+1 addition).

One row per object: module, category, mandatory, Odoo model/file, the exact raw SAP JSON
file(s) and Odoo CSV that back it, row counts (read live from disk when the files exist), status,
and notes. Source of truth is src/registry.py; this script only reads it and the output
directories - it doesn't decide anything, so it can't drift from what main.py actually produced.
"""

import csv
import json
import logging
import os
import sys

from src.registry import REGISTRY
from src.validate import FILENAME_OVERRIDES

logger = logging.getLogger("sap2odoo.status_report")

RAW_DIR = "output_raw"
ODOO_DIR = "output_odoo"
OUTPUT_PATH = "PROJECT_STATUS.csv"

FIELDNAMES = [
    "Sheet #",
    "Odoo Module (grouping)",
    "Category",
    "Object Name",
    "Mandatory",
    "Status",
    "Odoo Model",
    "Odoo Output File",
    "Odoo Row Count",
    "Raw SAP Source File(s)",
    "Raw Row Count(s)",
    "Notes",
]

STATUS_LABELS = {
    "built": "COMPLETED",
    "pending_mapping": "PENDING - SAP source not yet confirmed",
    "not_in_bydesign": "NOT APPLICABLE - no standard ByDesign module for this",
}


def _odoo_row_count(filename_base):
    filename = FILENAME_OVERRIDES.get(filename_base, f"{filename_base}.csv")
    path = os.path.join(ODOO_DIR, filename)
    if not os.path.isfile(path):
        return filename, ""
    with open(path, encoding="utf-8") as f:
        count = sum(1 for _ in f) - 1  # minus header
    return filename, max(count, 0)


def _raw_row_counts(raw_sources):
    counts = []
    for raw_filename in raw_sources:
        path = os.path.join(RAW_DIR, raw_filename)
        if not os.path.isfile(path):
            counts.append("")
            continue
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        counts.append(payload.get("row_count", ""))
    return counts


def build_rows():
    rows = []
    for obj in REGISTRY:
        odoo_file, odoo_count = _odoo_row_count(obj.filename_base)
        raw_counts = _raw_row_counts(obj.raw_sources)
        rows.append(
            {
                "Sheet #": obj.sheet_no if obj.sheet_no else "(added)",
                "Odoo Module (grouping)": obj.module,
                "Category": obj.category,
                "Object Name": obj.name,
                "Mandatory": "Yes" if obj.mandatory else "Optional",
                "Status": STATUS_LABELS.get(obj.status, obj.status),
                "Odoo Model": obj.odoo_model,
                "Odoo Output File": odoo_file if obj.status == "built" else "",
                "Odoo Row Count": odoo_count if obj.status == "built" else "",
                "Raw SAP Source File(s)": "; ".join(obj.raw_sources),
                "Raw Row Count(s)": "; ".join(str(c) for c in raw_counts),
                "Notes": obj.note,
            }
        )
    return rows


def write_report(rows, path=OUTPUT_PATH):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return path


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rows = build_rows()
    path = write_report(rows)

    completed = sum(1 for r in rows if r["Status"] == "COMPLETED")
    pending = sum(1 for r in rows if r["Status"].startswith("PENDING"))
    not_applicable = sum(1 for r in rows if r["Status"].startswith("NOT APPLICABLE"))

    logger.info("Wrote %s (%d rows)", path, len(rows))
    logger.info(
        "%d completed / %d pending / %d not applicable (out of %d total)",
        completed, pending, not_applicable, len(rows),
    )


if __name__ == "__main__":
    sys.exit(main())
