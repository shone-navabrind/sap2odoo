import logging
import sys
from datetime import datetime

from src.config import load_config
from src.extract_raw import extract_all
from src.sap_client import SAPODataClient
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
    config = load_config()
    setup_logging(config.log_dir)
    logger = logging.getLogger("sap2odoo")

    logger.info("=== Stage 1: raw extraction from SAP -> output_raw/ ===")
    client = SAPODataClient(config)
    raw_summary = extract_all(client, "output_raw")

    logger.info("=== Stage 2: transform raw data -> Odoo CSVs in output_odoo/ ===")
    odoo_summary = {}
    for label, fn in TRANSFORMS:
        logger.info("Transforming %s...", label)
        try:
            odoo_summary.update(fn())
        except Exception:
            logger.exception("FAILED transform: %s", label)

    logger.info("=== Stage 3: validate against the 66-object registry ===")
    rows = validate()
    covered = [r for r in rows if r[2]]

    logger.info("=== Stage 4: write PROJECT_STATUS.csv ===")
    status_path = write_status_report(build_status_rows())
    logger.info("Wrote %s - share this with the team for a completed/pending breakdown", status_path)

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
