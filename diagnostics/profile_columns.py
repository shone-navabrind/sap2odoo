"""
Column-by-column profile of EVERY entity set (not just masters, not just what maps to Odoo) -
one row per column across all 497 entity sets in output_full_csv/entities/, with distinct-value
counts, sample values, and automatic flags for known problem patterns (unconverted SAP date/
duration strings, JSON arrays, suspiciously long text, embedded newlines).

This is a read-only DIAGNOSTIC over already-exported data - it doesn't fix anything itself,
it finds what still needs fixing (or confirms nothing does). Run it after any change to
src/export_full_csv.py's cell normalization to check the fix actually reached every column.

Run: python diagnostics/profile_columns.py
Writes diagnostics/COLUMN_PROFILE.csv (one row per column, sortable/filterable in Excel) and
prints a flagged-only summary to the terminal.
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

SCAN_DIRS = {
    "entities": os.path.join("output_full_csv", "entities"),   # lossless, 1 row per SAP row -
                                                                 # catches unconverted date/duration
                                                                 # strings at the true source
    "odoo_models": os.path.join("output_full_csv", "odoo_models"),  # business-facing, header-
                                                                       # per-record - catches
                                                                       # JSON-array-from-merging issues
}
REPORT_PATH_TEMPLATE = os.path.join("diagnostics", "COLUMN_PROFILE_{}.csv")

MAX_DISTINCT_TRACKED = 30  # cap per-column distinct-value tracking so one huge-cardinality
                           # column (e.g. a free-text description) can't blow up memory

_SAP_DATE_RE = re.compile(r"^/Date\((-?\d+)\)/$")
_SAP_DURATION_RE = re.compile(r"^PT(?:\d+H)?(?:\d+M)?(?:\d+(?:\.\d+)?S)?$")
_JSON_ARRAY_RE = re.compile(r"^\[.*\]$")


def flags_for(value):
    flags = []
    if _SAP_DATE_RE.match(value):
        flags.append("UNCONVERTED_SAP_DATE")
    if _SAP_DURATION_RE.match(value) and value not in ("", "PT"):
        flags.append("UNCONVERTED_SAP_DURATION")
    if _JSON_ARRAY_RE.match(value):
        flags.append("JSON_ARRAY")
    if "\n" in value or "\r" in value:
        flags.append("EMBEDDED_NEWLINE")
    if len(value) > 500:
        flags.append("VERY_LONG_TEXT")
    return flags


def profile_file(path):
    """{column: {"non_blank": n, "distinct": Counter-ish dict capped, "flags": set, "rows": n}}"""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        cols = {c: {"non_blank": 0, "distinct": {}, "flags": set(), "rows": 0} for c in header}
        rows = 0
        for row in reader:
            rows += 1
            for c in header:
                v = (row.get(c) or "").strip()
                stat = cols[c]
                stat["rows"] += 1
                if not v:
                    continue
                stat["non_blank"] += 1
                if len(stat["distinct"]) < MAX_DISTINCT_TRACKED:
                    stat["distinct"][v] = stat["distinct"].get(v, 0) + 1
                for flag in flags_for(v):
                    stat["flags"].add(flag)
    return header, cols, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", choices=sorted(SCAN_DIRS), default="entities",
                        help="Which output_full_csv/ subfolder to profile (default: entities). "
                             "Use odoo_models to check the merged, one-to-many-collapsed view "
                             "instead of the raw one-row-per-SAP-row view.")
    args = parser.parse_args()

    scan_dir = SCAN_DIRS[args.dir]
    report_path = REPORT_PATH_TEMPLATE.format(args.dir)

    if not os.path.isdir(scan_dir):
        print(f"{scan_dir}/ does not exist - run `python -m src.export_full_csv` first.", file=sys.stderr)
        return 1

    files = sorted(f for f in os.listdir(scan_dir) if f.endswith(".csv") and not f.startswith("_"))
    if not files:
        print("No CSVs found.", file=sys.stderr)
        return 1

    all_rows = []
    flagged_summary = defaultdict(int)
    total_columns = 0

    for i, filename in enumerate(files):
        path = os.path.join(scan_dir, filename)
        header, cols, rows = profile_file(path)
        total_columns += len(header)
        for col in header:
            stat = cols[col]
            distinct_count = len(stat["distinct"])
            capped = distinct_count >= MAX_DISTINCT_TRACKED
            samples = list(stat["distinct"].keys())[:5]
            all_rows.append({
                "entity_file": filename,
                "column": col,
                "rows": stat["rows"],
                "non_blank": stat["non_blank"],
                "blank": stat["rows"] - stat["non_blank"],
                "distinct_values": f"{distinct_count}+" if capped else str(distinct_count),
                "flags": ",".join(sorted(stat["flags"])),
                "sample_values": " | ".join(samples),
            })
            for flag in stat["flags"]:
                flagged_summary[flag] += 1
        if (i + 1) % 100 == 0:
            print(f"  ...{i + 1}/{len(files)} entity files profiled", file=sys.stderr)

    os.makedirs("diagnostics", exist_ok=True)
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nProfiled {len(files)} files in {scan_dir}/, {total_columns} total columns.")
    print(f"Wrote {report_path} ({len(all_rows)} rows - one per column)\n")
    if flagged_summary:
        print("Flagged columns by issue type:")
        for flag, count in sorted(flagged_summary.items(), key=lambda x: -x[1]):
            print(f"  {flag:30} {count} columns")
    else:
        print("No flagged columns found - no unconverted dates/durations, no JSON arrays, "
              "no embedded newlines, no suspiciously long text.")

    return 1 if flagged_summary else 0


if __name__ == "__main__":
    sys.exit(main())
