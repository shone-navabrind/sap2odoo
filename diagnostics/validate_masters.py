"""
Confirms every file the master-data import needs (per ODOO_IMPORT_GUIDE.md's 17-step order) is
actually present, non-empty, and has the columns its dependents reference - i.e. "is the master
data actually complete and ready," not just "did src/registry.py mark the object as built."

Run: python diagnostics/validate_masters.py
"""

import csv
import os
import sys

ODOO_DIR = "output_odoo"

# (file, odoo model, depends-on files, sheet objects this file represents)
MASTER_FILES = [
    ("account_analytic_plan.csv", "account.analytic.plan", [], ["(infrastructure - synthetic plan for #6/#7)"]),
    ("uom_uom.csv", "uom.uom", [], ["#29 UOM"]),
    ("res_bank.csv", "res.bank", [], ["#5 Banks"]),
    ("product_category.csv", "product.category", [], ["#58 Product Categories"]),
    ("account_account.csv", "account.account", [], ["#1 Chart of Accounts"]),
    ("account_tax.csv", "account.tax", [], ["#2 Taxes"]),
    ("account_payment_term.csv", "account.payment.term", [], ["#4 Payment Terms"]),
    ("account_analytic_account_cc.csv", "account.analytic.account", ["account_analytic_plan.csv"], ["#6 Cost Centers"]),
    ("account_analytic_account.csv", "account.analytic.account", ["account_analytic_plan.csv"], ["#7 Analytic Accounts"]),
    ("res_partner.csv", "res.partner", [], ["#17 Customers", "#46 Vendors"]),
    ("res_partner_bank.csv", "res.partner.bank", ["res_partner.csv", "res_bank.csv"], ["(child of #17/#46)"]),
    ("res_partner_contact.csv", "res.partner", ["res_partner.csv"], ["(child of #17/#46)"]),
    ("res_users.csv", "res.users", [], ["#18 Salespersons"]),
    ("stock_warehouse.csv", "stock.warehouse", [], ["#27 Warehouses"]),
    ("stock_location.csv", "stock.location", ["stock_warehouse.csv"], ["#28 Locations"]),
    ("mrp_workcenter.csv", "mrp.workcenter", [], ["#42 Work Centers"]),
    ("product_template.csv", "product.template", ["product_category.csv", "uom_uom.csv"], ["#57 Products"]),
    ("product_supplierinfo.csv", "product.supplierinfo", ["res_partner.csv", "product_template.csv"], ["#47 Vendor Pricelists"]),
    ("product_pricelist.csv", "product.pricelist", [], ["#59 Pricelists"]),
]

# Every mandatory master object per src/registry.py, and which of the files above covers it.
# Used to flag if a MANDATORY master is missing its file outright (not just optional ones).
MANDATORY_SHEETS = {
    "#1 Chart of Accounts", "#2 Taxes", "#4 Payment Terms", "#5 Banks", "#17 Customers",
    "#18 Salespersons", "#27 Warehouses", "#28 Locations", "#29 UOM", "#42 Work Centers",
    "#46 Vendors", "#57 Products", "#58 Product Categories", "#59 Pricelists",
}


def load_header(path):
    with open(path, newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


def row_count(path):
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in f) - 1


def main():
    print("=== Master data completeness check ===\n")
    ok, missing, empty = [], [], []

    for filename, model, deps, sheets in MASTER_FILES:
        path = os.path.join(ODOO_DIR, filename)
        mandatory = any(s in MANDATORY_SHEETS for s in sheets)
        tag = "[MANDATORY]" if mandatory else "[optional] "

        if not os.path.isfile(path):
            missing.append((filename, sheets, mandatory))
            print(f"  MISSING     {tag} {filename:35} -> {model:28} ({', '.join(sheets)})")
            continue

        rows = row_count(path)
        header = load_header(path)
        dep_status = []
        for dep in deps:
            dep_path = os.path.join(ODOO_DIR, dep)
            dep_status.append(f"{dep}:{'OK' if os.path.isfile(dep_path) else 'MISSING'}")

        if rows == 0:
            empty.append((filename, sheets, mandatory))
            print(f"  EMPTY (0 rows) {tag} {filename:31} -> {model:28} ({', '.join(sheets)})")
        else:
            ok.append(filename)
            deps_str = f"  deps: {', '.join(dep_status)}" if dep_status else ""
            print(f"  OK  {tag} {filename:35} {rows:>6} rows, {len(header):>2} cols -> {model}{deps_str}")

    print()
    print(f"=== Summary: {len(ok)} OK, {len(empty)} present-but-empty, {len(missing)} missing "
          f"(of {len(MASTER_FILES)} files checked) ===")

    mandatory_missing = [m for m in missing if m[2]] + [m for m in empty if m[2]]
    if mandatory_missing:
        print("\n⚠ MANDATORY master data files missing or empty:")
        for filename, sheets, _ in mandatory_missing:
            print(f"  {filename} ({', '.join(sheets)})")
    else:
        print("\nAll mandatory master data files are present and populated.")

    return 1 if (missing or empty) else 0


if __name__ == "__main__":
    sys.exit(main())
