import logging
import sys
from datetime import datetime

from src.config import load_config
from src.extract_raw import extract_all
from src.sap_client import SAPODataClient
from src.consolidated_report import build_rows as build_consolidated_rows
from src.status_report import build_rows as build_status_rows, write_report as write_status_report
from src.transform_odoo import TRANSFORMS
from src.validate import validate


def setup_logging(log_dir):
    import os
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"sap2odoo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_file)],
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Full pipeline: extract_raw -> transform_odoo -> export_full_csv -> validate."
    )
    parser.add_argument(
        "--only", metavar="TEXT",
        help="Run only the SAP sources and Odoo transform(s) matching TEXT (case-insensitive "
             "substring of the service name, entity set, or transform label), e.g. "
             "--only khcustomer or --only res_partner. Everything else in the pipeline "
             "(export_full_csv, validate, status reports) still runs against whatever is "
             "already on disk from prior runs.",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N",
        help="Cap each SAP entity set at N rows during extraction - for a quick/limited test "
             "run instead of pulling a whole tenant's history.",
    )
    parser.add_argument(
        "--workers", type=int, default=1, metavar="N",
        help="Pull N entity sets concurrently in Stage 1 instead of one at a time (default: 1, "
             "sequential). See `python -m src.extract_raw --help` for caveats.",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip any Stage 1 source whose output_raw/*.json file already exists - continue an "
             "interrupted run, or switch it to a different --workers count, without re-pulling.",
    )
    args = parser.parse_args()

    config = load_config()
    setup_logging(config.log_dir)
    logger = logging.getLogger("sap2odoo")

    logger.info("=== Stage 1: raw extraction from SAP -> output_raw/ ===")
    client = SAPODataClient(config)
    raw_summary = extract_all(
        client, "output_raw", only=args.only, limit=args.limit, workers=args.workers, resume=args.resume
    )

    logger.info("=== Stage 2: transform raw data -> Odoo CSVs in output_odoo/ ===")
    transforms = TRANSFORMS
    if args.only:
        needle = args.only.lower()
        transforms = [(label, fn) for label, fn in TRANSFORMS if needle in label.lower()]
        if not transforms:
            logger.warning("--only %r matched no transform label - Stage 2 will do nothing", args.only)
    odoo_summary = {}
    for label, fn in transforms:
        logger.info("Transforming %s...", label)
        try:
            odoo_summary.update(fn())
        except Exception:
            logger.exception("FAILED transform: %s", label)

    logger.info("=== Stage 2b: full-column CSV export -> output_full_csv/ ===")
    try:
        from src.export_full_csv import main as export_full_csv
        export_full_csv()
    except Exception:
        logger.exception("FAILED full-column CSV export (output_odoo/ is unaffected)")

    logger.info("=== Stage 3: validate against the 66-object registry ===")
    rows = validate()
    covered = [r for r in rows if r[2]]

    logger.info("=== Stage 4: write status reports ===")
    status_path = write_status_report(build_status_rows())
    logger.info("Wrote %s - per requirement-sheet object", status_path)

    import csv as _csv
    from src.consolidated_report import FIELDNAMES as CONSOLIDATED_FIELDNAMES, OUTPUT_PATH as CONSOLIDATED_PATH
    consolidated = build_consolidated_rows()
    with open(CONSOLIDATED_PATH, "w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=CONSOLIDATED_FIELDNAMES)
        writer.writeheader()
        writer.writerows(consolidated)
    logger.info("Wrote %s - one row per SAP API call, everything in one place", CONSOLIDATED_PATH)

    logger.info("=== Run summary ===")
    logger.info("Raw sources pulled from SAP:")
    for service, entity_set, status, count in raw_summary:
        logger.info("  %-70s %-8s %d rows", f"{service.rsplit('/', 1)[-1]}/{entity_set}", status, count)
    logger.info("Odoo CSVs written:")
    for filename, count in odoo_summary.items():
        logger.info("  %-30s %d rows", filename, count)
    logger.info(
        "Registry coverage: %d/%d sheet objects have real data (see src/validate.py for the full breakdown)",
        len(covered), len(rows),
    )


if __name__ == "__main__":
    sys.exit(main())
