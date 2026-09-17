import csv
import os
import re


def external_id(prefix, key):
    """Build an Odoo-safe external ID like sap_bp_1000001 from a SAP key."""
    safe_key = re.sub(r"[^A-Za-z0-9_]", "_", str(key).strip())
    return f"{prefix}_{safe_key}"


def write_csv(output_dir, filename, rows, fieldnames):
    """Write rows (list of dicts) to output_dir/filename with the given column order."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path
