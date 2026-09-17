"""
Cross-checks src/registry.py (the 66 sheet objects + 1 addition) against what output_odoo/
actually contains, so "done" is a verifiable claim, not an assumption.
"""

import logging
import os
import csv

from src.extract_raw import SOURCES
from src.registry import REGISTRY
from src.transform_odoo import TRANSFORMS

logger = logging.getLogger("sap2odoo.validate")

ODOO_DIR = "output_odoo"

# Registry filename_base -> real output_odoo filename, where they differ (e.g. one raw source
# feeding two Odoo files, like Banks coming out of the same pipeline as Customers/Vendors).
FILENAME_OVERRIDES = {
    "res_bank": "res_bank.csv",
    "res_users_salesperson": "res_users.csv",
    "account_payment_customer": "account_payment.csv",
    "account_payment_vendor": "account_payment.csv",
}


def csv_has_records(path):
    """Return whether an Odoo CSV has a header and at least one importable row."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return bool(reader.fieldnames) and next(reader, None) is not None
    except (OSError, csv.Error):
        return False


def validate():
    rows = []
    for obj in REGISTRY:
        filename = FILENAME_OVERRIDES.get(obj.filename_base, f"{obj.filename_base}.csv")
        has_data = csv_has_records(os.path.join(ODOO_DIR, filename))
        rows.append((obj, filename, has_data))

    return rows


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rows = validate()

    covered = [r for r in rows if r[2]]
    not_covered_mapped = [r for r in rows if not r[2] and r[0].status != "not_in_bydesign"]
    not_in_bydesign = [r for r in rows if r[0].status == "not_in_bydesign"]

    logger.info("=== Validation: %d/%d sheet objects have real data in output_odoo/ ===", len(covered), len(rows))
    logger.info(
        "Pipeline scope: %d confirmed SAP entity sets, %d Odoo transform group(s). "
        "Only objects marked built have extraction and transformation code.",
        len(SOURCES),
        len(TRANSFORMS),
    )
    for obj, filename, _ in covered:
        logger.info("  DATA      #%3s %-30s -> %s", obj.sheet_no, obj.name, filename)

    logger.info("--- %d objects with a decided Odoo target but no data yet ---", len(not_covered_mapped))
    for obj, filename, _ in not_covered_mapped:
        logger.info("  PENDING   #%3s %-30s -> %s (status=%s)", obj.sheet_no, obj.name, filename, obj.status)

    logger.info("--- %d objects flagged as not standard in ByDesign (need user confirmation) ---", len(not_in_bydesign))
    for obj, filename, _ in not_in_bydesign:
        logger.info("  NOT_FOUND #%3s %-30s (%s)", obj.sheet_no, obj.name, obj.note)

    return rows


if __name__ == "__main__":
    main()
