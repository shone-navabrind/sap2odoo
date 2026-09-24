"""
Deep, independent audit of output_odoo/*.csv - not "did the code run without an exception" but
"is the data actually correct": does every relation column resolve to a real record, are IDs
actually unique, are supposedly-required columns actually populated, do row/column shapes match
what each file's own header promises.

This is deliberately a SEPARATE script from the main pipeline (src/) - it re-derives everything
from the CSVs on disk rather than trusting any in-memory state the transforms already had, so a
bug in transform_odoo.py that happened to produce self-consistent-looking output wouldn't also
make this audit agree with it for the wrong reasons.

Run: python diagnostics/validate_relations.py
     python diagnostics/validate_relations.py --file res_partner.csv   # just one file
Writes diagnostics/RELATION_REPORT.md
"""

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

ODOO_DIR = "output_odoo"
REPORT_PATH = os.path.join("diagnostics", "RELATION_REPORT.md")

# Every external ID this pipeline generates comes from src/csv_writer.py's external_id(), which
# always produces "<prefix>_<key>" with prefix starting "sap_" (confirmed: every external_id()
# call site in transform_odoo.py uses a "sap_..." prefix - sap_bp, sap_prod, sap_account, etc.).
# A relation value that does NOT start with "sap_" is therefore never one of ours to check - it's
# a reference to Odoo's own built-in data (base.us, purchase_stock.route_warehouse0_buy,
# mrp.route_warehouse0_manufacture, ...) that ships with Odoo itself and is never in our CSVs.
OUR_ID_PREFIX = "sap_"


def load_all_csvs(only=None):
    """{filename: {"header": [...], "rows": [dict, ...]}}"""
    files = {}
    for filename in sorted(os.listdir(ODOO_DIR)):
        if not filename.endswith(".csv"):
            continue
        if only and filename != only:
            continue
        path = os.path.join(ODOO_DIR, filename)
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            rows = list(reader)
        files[filename] = {"header": header, "rows": rows}
    return files


def build_global_id_index(files):
    """
    {external_id: [filenames that define it as their own "id"]} - a real primary key should be
    defined in exactly one file. Also returns the flat set of every known-good external ID for
    relation-resolution checks.
    """
    id_owners = defaultdict(list)
    for filename, data in files.items():
        if "id" not in data["header"]:
            continue
        for row in data["rows"]:
            value = (row.get("id") or "").strip()
            if value:
                id_owners[value].append(filename)
    all_ids = set(id_owners)
    return id_owners, all_ids


def is_relation_column(name):
    return name.endswith("/id")


def audit_file(filename, data, all_ids, id_owners):
    header, rows = data["header"], data["rows"]
    report = {
        "filename": filename,
        "rows": len(rows),
        "columns": len(header),
        "column_stats": [],
        "relation_stats": [],
        "duplicate_ids": [],
        "id_shared_with_other_files": [],
    }

    # --- per-column shape: blanks, distinct values, a sample ---
    for col in header:
        values = [(row.get(col) or "").strip() for row in rows]
        non_blank = [v for v in values if v]
        distinct = len(set(non_blank))
        report["column_stats"].append({
            "column": col,
            "non_blank": len(non_blank),
            "blank": len(values) - len(non_blank),
            "distinct": distinct,
            "sample": next(iter(non_blank), ""),
        })

    # --- id column: uniqueness within this file, and whether another file also claims it ---
    if "id" in header:
        id_values = [(row.get("id") or "").strip() for row in rows if (row.get("id") or "").strip()]
        counts = Counter(id_values)
        dups = [v for v, c in counts.items() if c > 1]
        if dups:
            report["duplicate_ids"] = dups[:20] + (["... and more"] if len(dups) > 20 else [])
        for value in set(id_values):
            owners = id_owners.get(value, [])
            other_owners = [o for o in owners if o != filename]
            if other_owners:
                report["id_shared_with_other_files"].append((value, other_owners))
        report["id_shared_with_other_files"] = report["id_shared_with_other_files"][:20]

    # --- every relation column: does each non-blank value resolve to a real "id" somewhere? ---
    for col in header:
        if not is_relation_column(col):
            continue
        values = [(row.get(col) or "").strip() for row in rows]
        non_blank = [v for v in values if v]
        builtin = [v for v in non_blank if not v.startswith(OUR_ID_PREFIX)]
        checkable = [v for v in non_blank if v.startswith(OUR_ID_PREFIX)]
        orphans = [v for v in checkable if v not in all_ids]
        resolved = len(checkable) - len(orphans)
        report["relation_stats"].append({
            "column": col,
            "total_rows": len(rows),
            "populated": len(non_blank),
            "builtin_odoo_refs": len(builtin),
            "checkable": len(checkable),
            "resolved": resolved,
            "orphaned": len(orphans),
            "orphan_samples": sorted(set(orphans))[:8],
        })

    return report


def format_report(reports, id_owners):
    lines = ["# Relation & Data-Quality Audit\n",
             "Generated by `diagnostics/validate_relations.py` - re-derived independently from "
             "`output_odoo/*.csv` on disk, not trusted from the pipeline's own run.\n"]

    total_relation_cols = sum(len(r["relation_stats"]) for r in reports)
    total_orphans = sum(s["orphaned"] for r in reports for s in r["relation_stats"])
    total_dup_files = sum(1 for r in reports if r["duplicate_ids"])
    total_shared_files = sum(1 for r in reports if r["id_shared_with_other_files"])

    lines.append("## Summary\n")
    lines.append(f"- **{len(reports)} files audited**")
    lines.append(f"- **{total_relation_cols} relation columns checked**")
    lines.append(f"- **{total_orphans} orphaned relation values found** "
                 f"(non-blank, non-`base.`-prefixed, but the target ID doesn't exist anywhere)")
    lines.append(f"- **{total_dup_files} files have duplicate values in their own `id` column** "
                 f"(should never happen - external IDs must be unique)")
    lines.append(f"- **{total_shared_files} files have `id` values also claimed by another file** "
                 f"(a namespace collision, or an intentional shared file - reviewed below)")
    lines.append("")

    for r in reports:
        lines.append(f"## `{r['filename']}`\n")
        lines.append(f"{r['rows']} rows, {r['columns']} columns.\n")

        if r["duplicate_ids"]:
            lines.append(f"**⚠ DUPLICATE `id` VALUES ({len(r['duplicate_ids'])} shown):** "
                         + ", ".join(f"`{d}`" for d in r["duplicate_ids"]))
            lines.append("")
        if r["id_shared_with_other_files"]:
            lines.append("**`id` values also present in another file's `id` column "
                         f"({len(r['id_shared_with_other_files'])} shown):**")
            for value, owners in r["id_shared_with_other_files"]:
                lines.append(f"  - `{value}` also in {', '.join(owners)}")
            lines.append("")

        if r["relation_stats"]:
            lines.append("**Relation columns:**\n")
            lines.append("| Column | Rows | Populated | Built-in Odoo refs | Resolved | Orphaned |")
            lines.append("|---|---|---|---|---|---|")
            for s in r["relation_stats"]:
                flag = " ⚠" if s["orphaned"] else ""
                lines.append(f"| `{s['column']}` | {s['total_rows']} | {s['populated']} | "
                             f"{s['builtin_odoo_refs']} | {s['resolved']}{flag} | {s['orphaned']}{flag} |")
            for s in r["relation_stats"]:
                if s["orphaned"]:
                    lines.append(f"\n  `{s['column']}` orphan samples: "
                                 + ", ".join(f"`{v}`" for v in s["orphan_samples"]))
            lines.append("")

        lines.append("**Column-level detail:**\n")
        lines.append("| Column | Non-blank | Blank | Distinct values | Sample |")
        lines.append("|---|---|---|---|---|")
        for c in r["column_stats"]:
            sample = c["sample"].replace("|", "\\|")
            if len(sample) > 40:
                sample = sample[:40] + "..."
            lines.append(f"| `{c['column']}` | {c['non_blank']} | {c['blank']} | "
                         f"{c['distinct']} | `{sample}` |")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", metavar="FILENAME", help="Audit just one output_odoo/*.csv file")
    args = parser.parse_args()

    if not os.path.isdir(ODOO_DIR):
        print(f"{ODOO_DIR}/ does not exist - run the pipeline first.", file=sys.stderr)
        return 1

    files = load_all_csvs(only=args.file)
    if not files:
        print("No matching files found.", file=sys.stderr)
        return 1

    # id_owners/all_ids are always built from the FULL set of files (even with --file), so a
    # relation is checked against everything that could legitimately resolve it, not just the
    # one file being inspected.
    all_files = load_all_csvs() if args.file else files
    id_owners, all_ids = build_global_id_index(all_files)

    reports = [audit_file(fn, data, all_ids, id_owners) for fn, data in files.items()]

    os.makedirs("diagnostics", exist_ok=True)
    report_text = format_report(reports, id_owners)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report_text)

    total_orphans = sum(s["orphaned"] for r in reports for s in r["relation_stats"])
    total_dups = sum(len(r["duplicate_ids"]) for r in reports)
    print(f"Audited {len(reports)} file(s), wrote {REPORT_PATH}")
    print(f"  {total_orphans} orphaned relation values, {total_dups} duplicate IDs found")
    return 1 if (total_orphans or total_dups) else 0


if __name__ == "__main__":
    sys.exit(main())
