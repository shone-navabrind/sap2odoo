"""
Deletes generated pipeline output so the next run starts from a clean slate.

This is destructive - it removes real extracted/transformed data, not source code - so it
requires either an interactive "yes" confirmation or --yes on the command line. Never runs
without one or the other.

Run: python -m src.clean               # asks what to delete, interactively
     python -m src.clean --all --yes   # delete everything, no prompt (for scripts)
     python -m src.clean --raw --yes   # just output_raw/ (forces a full re-extraction)
"""

import argparse
import logging
import os
import shutil
import sys

logger = logging.getLogger("sap2odoo.clean")

TARGETS = {
    "raw": ["output_raw"],
    "odoo": ["output_odoo"],
    "full-csv": ["output_full_csv"],
    "logs": ["logs"],
    "reports": ["CONSOLIDATED_STATUS.csv", "PROJECT_STATUS.csv"],
}


def _size_of(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    if not os.path.isdir(path):
        return 0
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total


def _human(num_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.0f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


def remove(paths):
    removed = []
    for path in paths:
        if os.path.isdir(path):
            shutil.rmtree(path)
            removed.append(path)
        elif os.path.isfile(path):
            os.remove(path)
            removed.append(path)
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", action="store_true", help="Delete output_raw/ (forces a full re-extraction from SAP)")
    parser.add_argument("--odoo", action="store_true", help="Delete output_odoo/")
    parser.add_argument("--full-csv", action="store_true", help="Delete output_full_csv/")
    parser.add_argument("--logs", action="store_true", help="Delete logs/")
    parser.add_argument("--reports", action="store_true", help="Delete CONSOLIDATED_STATUS.csv and PROJECT_STATUS.csv")
    parser.add_argument("--all", action="store_true", help="All of the above")
    parser.add_argument("--yes", action="store_true", help="Don't ask for confirmation (for scripts/CI)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    selected_groups = [
        g for g, flag in [("raw", args.raw), ("odoo", args.odoo), ("full-csv", args.full_csv),
                          ("logs", args.logs), ("reports", args.reports)]
        if flag or args.all
    ]
    if not selected_groups:
        parser.error("Nothing selected - pass --raw / --odoo / --full-csv / --logs / --reports / --all")

    paths = []
    for g in selected_groups:
        paths.extend(TARGETS[g])
    existing = [p for p in paths if os.path.exists(p)]

    if not existing:
        logger.info("Nothing to delete - none of the selected paths exist.")
        return 0

    total_size = sum(_size_of(p) for p in existing)
    logger.info("This will permanently delete:")
    for p in existing:
        logger.info("  %-25s %s", p, _human(_size_of(p)))
    logger.info("Total: %s", _human(total_size))

    if not args.yes:
        answer = input("Type 'yes' to confirm: ").strip().lower()
        if answer != "yes":
            logger.info("Cancelled - nothing deleted.")
            return 1

    removed = remove(existing)
    logger.info("Deleted: %s", ", ".join(removed))
    logger.info("Run `python -m src.main` (or `--only`/`--resume`) to regenerate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
