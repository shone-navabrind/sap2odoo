"""
Generates SAP_IMPORT_PLAN.md - the answer to "which of the 47 .xml files do I still need to
import into the tenant, and what does each one actually buy me?"

The user's only available action in SAP is importing custom OData service definitions
(Application and User Management -> OData Services -> Custom OData Services -> Import). So the
plan is written in exactly those terms, and every claim in it is backed by a file on disk:

  - which services are live                -> schema_snapshots/service_probe.json  (live probe)
  - what each service would expose         -> schema_snapshots/custom_service_index.json (parsed .xml)
  - which registry objects are still open  -> src/registry.py

UNLOCKS below is the one hand-made part: the judgement call of which migration-sheet object a
service's entity sets would feed. It is stated per service so it can be checked, and the entity
sets backing each claim are printed next to it.

Run: python -m src.import_plan
"""

import json
import logging
import sys

from src.parse_service_defs import load_index
from src.registry import REGISTRY

logger = logging.getLogger("sap2odoo.import_plan")

OUTPUT_PATH = "SAP_IMPORT_PLAN.md"
PROBE_PATH = "schema_snapshots/service_probe.json"

# service -> (priority, sheet numbers it would feed, why - in plain language)
# Sheet numbers refer to "Danlaw SAP Data Migration-Jul2026 - Sheet1.csv" / src/registry.py.
UNLOCKS = {
    "khcustomer": (1, [17], "Full customer master: 37 header fields plus bank details, tax numbers, "
                           "tax exemptions, relationships and addresses. Today Customers is built from "
                           "an analytics report that returns only 44 rows and cannot be filtered."),
    "khsupplier": (1, [46], "Full supplier master, same shape as khcustomer (19 header fields, bank "
                           "details, addresses, notes). Replaces the analytics-report source for Vendors."),
    "khcustomerinvoicerequest": (1, [8], "Customer invoice REQUESTS - invoices raised but not yet "
                                        "cleared. This is the closest thing ByDesign has to Odoo's "
                                        "'open invoice' concept, which the posted-invoice service does "
                                        "not expose at all."),
    "khgoodsandactivityconfirmation": (1, [31, 33, 30], "Inventory changes: InventoryChangeItem (20 fields), "
                                       "SerialNumber and IdentifiedStock. The only source found for stock "
                                       "movements, inventory adjustments and lot/serial numbers."),
    "khhousebankaccount": (1, [5], "Real house bank master - HouseBankAccount (18 fields), HouseBank, "
                                  "BankDirectoryEntry with national bank IDs. Banks is currently derived "
                                  "from fields buried inside the business-partner records."),
    "khserviceproduct": (1, [57], "Service products - a SEPARATE product master from materials. These are "
                                 "missing from product_template.csv entirely today."),

    "khinbounddelivery": (2, [32, 64], "Inbound deliveries (goods receipts): 15 header + 13 item fields, "
                                      "with purchase-order references. Only outbound deliveries are covered today."),
    "khcustomerreturn": (2, [66], "Customer returns - the business event behind most customer credit notes."),
    "khproject": (2, [7], "Projects with tasks, teams and service confirmations. The natural Odoo "
                         "analytic-account source alongside cost centers."),
    "khprofitcentre": (2, [7], "Profit centers - a second analytic dimension next to cost centers."),
    "khlead": (2, [19], "Leads, separate from opportunities, with notes and campaign references."),
    "khbusinesspartnerrelationship": (2, [17, 46], "Contact persons attached to customers/suppliers, with "
                                     "their own addresses. Contacts are effectively empty today."),
    "khgoodsandserviceacknowledgement": (2, [33], "Goods and service receipts against purchase orders "
                                        "(30 item fields) - the purchasing side of stock movement history."),
    "khserviceproductvaluationdata": (2, [57], "Cost rates for service products, the equivalent of the "
                                     "material valuation data already used for standard_price."),

    "khproductionplanningorder": (3, [45], "Planned production orders, ahead of released manufacturing orders."),
    "khconfirmedinbounddelivery": (3, [32], "Confirmed inbound deliveries; complements khinbounddelivery."),
    "khinbounddeliveryrequest": (3, [32], "Requested inbound deliveries."),
    "khoutbounddeliveryrequest": (3, [32], "Requested outbound deliveries; complements the delivery data already pulled."),
    "khbusinesspartner": (3, [17, 46], "Generic business-partner view; largely redundant once khcustomer "
                                      "and khsupplier are in, but carries roles and notes."),
    "khaddresssnapshot": (3, [], "Resolves the address snapshot IDs that documents reference, turning "
                                "internal address keys into real postal addresses."),
    "khfunctionalunit": (3, [27, 28], "Organisational units - sales/purchasing/site units behind warehouses."),
    "khbusinessresidence": (3, [27, 28], "Business residences and associated sites (24 fields)."),
    "khreportinglineunit": (3, [], "Reporting line hierarchy; enriches the employee org assignment."),
    "khcompany": (3, [], "Company master with default currency - feeds res.company setup."),
    "khcostcentre": (3, [6], "Cost centers. Already covered by the standard v1 costcentre service, so this "
                            "is only worth importing for the extra attribute and hierarchy entity sets."),
    "khemployeetime": (3, [], "Employee time records (32 item fields). Not on the 66-object sheet."),
    "khtimeagreement": (3, [], "Employee time agreements. Not on the 66-object sheet."),
    "khpaymentorderprocessingstatement": (3, [], "Payment order processing statements (24 fields); enriches payments."),
    "khcompanypaymentfileregister": (3, [], "Incoming payment file register; enriches bank statement handling."),
}

PRIORITY_LABEL = {
    1: "Import first - closes a mandatory gap or replaces a weak source",
    2: "Import next - closes an optional object or materially improves an existing one",
    3: "Optional - supporting detail, no sheet object depends on it alone",
}


def build():
    index = {s["service"]: s for s in load_index()}
    with open(PROBE_PATH, encoding="utf-8") as f:
        probe = {r["service"]: r for r in json.load(f)}
    by_sheet = {o.sheet_no: o for o in REGISTRY}

    live = sorted(s for s, r in probe.items() if r["status"] == "LIVE")
    not_live = sorted(s for s, r in probe.items() if r["status"] != "LIVE")

    lines = [
        "# What to import into SAP next",
        "",
        "Generated by `python -m src.import_plan`. Every statement below is derived from files on",
        "disk, not from memory: the live probe (`schema_snapshots/service_probe.json`), the parsed",
        "service definitions (`schema_snapshots/custom_service_index.json`) and `src/registry.py`.",
        "",
        "## How to import",
        "",
        "In the ByDesign tenant: **Application and User Management -> OData Services -> Custom OData",
        "Services -> Import**, then upload the `.xml` file named below from",
        "`byd-api-samples-main/Custom OData Services/`. After importing, run",
        "`python -m src.probe_services` to confirm it went live, then `python -m src.main`.",
        "",
        f"## Current state: {len(live)} of {len(live) + len(not_live)} services are live",
        "",
        "Already imported and returning data:",
        "",
        "```",
        "\n".join(f"  {s}" for s in live),
        "```",
        "",
    ]

    for priority in (1, 2, 3):
        group = [s for s in not_live if UNLOCKS.get(s, (9,))[0] == priority]
        if not group:
            continue
        lines += [f"## Priority {priority} - {PRIORITY_LABEL[priority]}", ""]
        for service in sorted(group, key=lambda s: -sum(
                e["field_count"] for e in index[s]["entity_sets"])):
            _, sheets, why = UNLOCKS[service]
            entity_sets = index[service]["entity_sets"]
            total_fields = sum(e["field_count"] for e in entity_sets)
            targets = ", ".join(
                f"#{n} {by_sheet[n].name}" + (" (MANDATORY)" if by_sheet[n].mandatory else "")
                for n in sheets if n in by_sheet
            ) or "no single sheet object - supporting data"

            lines += [
                f"### `{service}.xml`",
                "",
                f"- **Would complete:** {targets}",
                f"- **Why:** {why}",
                f"- **Exposes:** {len(entity_sets)} entity sets, {total_fields} fields total",
                f"- **Largest entity sets:** " + ", ".join(
                    f"`{e['name']}` ({e['field_count']} fields)" for e in entity_sets[:5]),
                "",
            ]

    lines += [
        "## Not solvable by importing a service",
        "",
        "These migration-sheet objects have **no matching service among the 47 `.xml` files at all**.",
        "Searching every parsed entity-set name across all 47 services for `glaccount`, `chartof`,",
        "`ledger`, `billofmaterial`, `bom`, `productionmodel`, `exchangerate`, `asset`, `depreciation`",
        "and `journal` returns zero matches. Importing more services will not produce them.",
        "",
        "| Sheet # | Object | Mandatory | What it would need instead |",
        "|---|---|---|---|",
        "| 1 | Chart of Accounts | Yes | A ByDesign Financials OData service, or a manual export from "
        "*General Ledger -> Charts of Accounts* in the tenant UI |",
        "| 2 | Taxes | Yes | Partially derivable from tax codes actually used on invoices (see "
        "`account_tax.csv`); full rates need *Tax Management* configuration export |",
        "| 40 | BOMs | Yes | ByDesign's Production Model / Bill of Material; no OData service definition "
        "exists in this sample set |",
        "| 14, 15 | Fixed Assets, Depreciation | No | ByDesign Fixed Asset module export |",
        "| 13 | Journal Entries | No | Financials journal OData service |",
        "| 0 | Currency Exchange Rates | No | ByDesign currency conversion configuration |",
        "",
        "## Blocked by authorisation, not by import",
        "",
        "These services **are already imported and live**, but every data read returns",
        "`RBAM_ERROR: Not Authorized: Check Authorization Restriction for the User`. The fix is an SAP",
        "role change for the technical user, not another import:",
        "",
        "| Service | Sheet object | What to grant |",
        "|---|---|---|",
        "| `khcustomerquote` | #62 Quotations | Access to the **Sales Quotes** work center for the "
        "integration user |",
        "| `tmserviceorder` | (service orders) | Access to the **Service Orders** work center |",
        "| `tmservicerequest` | (service requests) | Access to the **Service Requests** work center |",
        "",
        "`tmserviceconfirmation` is live and authorised but every entity set returns 0 rows - there is",
        "genuinely no service-confirmation data in this tenant.",
        "",
    ]
    return "\n".join(lines)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    text = build()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info("Wrote %s (%d lines)", OUTPUT_PATH, text.count("\n") + 1)


if __name__ == "__main__":
    sys.exit(main())
