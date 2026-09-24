# Project Status — sap2odoo

**Last full run completed:** 2026-09-24, against the live SAP Business ByDesign tenant
(`my346623.sapbydesign.com`). All numbers below are from that run's real output — not estimates.

## Headline numbers

| | |
|---|---|
| Requirement-sheet objects complete | **39 of 67** |
| Mandatory objects complete | **19 of 21** |
| Total Odoo-ready records | **599,565** rows across 46 CSV files |
| Full-column backup (every SAP field, not just what maps to Odoo) | **12,200,300 rows, 3,345 columns**, 497 entity files |
| SAP entity sets extracted | 497 of 569 attempted (remainder are known, documented gaps - see below) |

## Every completed object

| # | Module | Mandatory | Object | Odoo Model | File | Rows |
|---|---|---|---|---|---|---|
| 1 | Accounts | Yes | Chart of Accounts | account.account | account_account.csv | 213 |
| 2 | Accounts | Yes | Taxes | account.tax | account_tax.csv | 115 |
| 4 | Accounts | Yes | Payment Terms | account.payment.term | account_payment_term.csv | 31 |
| 5 | Accounts | Yes | Banks | res.bank | res_bank.csv | 287 |
| 6 | Accounts | | Cost Centers | account.analytic.account | account_analytic_account_cc.csv | 13 |
| 7 | Accounts | | Analytic Accounts | account.analytic.account | account_analytic_account.csv | 2 |
| 8 | Accounts | Yes | Open Customer Invoices | account.move | account_move_open_customer.csv | 846 |
| 9 | Accounts | Yes | Open Vendor Bills | account.move | account_move_open_vendor.csv | 1,417 |
| 10 | Accounts | | Customer Payments | account.payment | account_payment.csv | 59,493 |
| 11 | Accounts | | Vendor Payments | account.payment | account_payment.csv (same file as #10) | — |
| 12 | Accounts | | Bank Statements | account.bank.statement | account_bank_statement.csv | 404 |
| 17 | CRM | Yes | Customers | res.partner | res_partner.csv | 2,404 |
| 18 | CRM | Yes | Salespersons | res.users | res_users.csv | 102 |
| 20 | CRM | | Opportunities | crm.lead | crm_lead.csv | 0 (none on tenant) |
| 21 | CRM | Yes | Open Opportunities | crm.lead | crm_lead_open.csv | 0 |
| 22 | CRM | | Closed Opportunities | crm.lead | crm_lead_closed.csv | 0 |
| 27 | Inventory | Yes | Warehouses | stock.warehouse | stock_warehouse.csv | 1 |
| 28 | Inventory | Yes | Locations | stock.location | stock_location.csv | 19 |
| 29 | Inventory | Yes | UOM | uom.uom | uom_uom.csv | 38 |
| 31 | Inventory | | Inventory Adjustments | stock.quant | stock_quant_adjustment.csv | 3,868 |
| 32 | Inventory | | Stock Transfers | stock.picking | stock_picking_transfer.csv (+3,142 lines) | 1,108 |
| 33 | Inventory | | Stock Moves History | stock.move | stock_move_history.csv | 15,337 |
| 42 | Manufacturing | Yes | Work Centers | mrp.workcenter | mrp_workcenter.csv | 17 |
| 44 | Manufacturing | | Manufacturing Orders | mrp.production | mrp_production.csv | 9,557 |
| 45 | Manufacturing | | Production History | mrp.production | mrp_production_history.csv | 9,412 |
| 46 | Purchase | Yes | Vendors | res.partner | res_partner.csv (same file as #17) | — |
| 47 | Purchase | | Vendor Pricelists | product.supplierinfo | product_supplierinfo.csv | 8,556 |
| 49 | Purchase | | RFQs | purchase.order | purchase_order_rfq.csv | 139 |
| 50 | Purchase | Yes | Purchase Orders | purchase.order | purchase_order.csv (+45,981 lines) | 15,797 |
| 51 | Purchase | | Vendor Bills | account.move | account_move_vendor_bill.csv (+211,733 lines) | 55,261 |
| 52 | Purchase | | Vendor Credit Notes | account.move | account_move_vendor_credit.csv | 152 |
| 57 | Sales | Yes | Products | product.template | product_template.csv | 9,287 |
| 58 | Sales | Yes | Product Categories | product.category | product_category.csv | 24 |
| 59 | Sales | Yes | Pricelists | product.pricelist | product_pricelist.csv | 226 |
| 60 | Sales | | Discount Rules | product.pricelist.item | product_pricelist_item_discount.csv | 932 |
| 63 | Sales | Yes | Sales Orders | sale.order | sale_order.csv (+9,921 lines) | 4,308 |
| 64 | Sales | | Deliveries | stock.picking | stock_picking_delivery.csv (+32,793 lines) | 28,672 |
| 65 | Sales | | Customer Invoices | account.move | account_move_customer_invoice.csv (+35,514 lines) | 30,081 |
| 66 | Sales | | Credit Notes | account.move | account_move_credit_note.csv | 802 |

(46 physical files total once header/line files and shared files like res_partner.csv are
counted individually - see `output_odoo/` for the exact list. Total rows across all 46 files,
counted once each: **599,565**.)

## What's still pending, and exactly why

### The 2 remaining mandatory gaps

Both are **data-availability problems, not permission problems** - this distinction matters, so
here is the detail:

**#34 Equipment** - **not an access issue at all.** Standard SAP Business ByDesign does not have
a Preventive Maintenance / Equipment module as a concept. There is no API to be denied access to,
because there is nothing to expose:
- Searched the tenant's full catalog of 573 known reporting data sources for "equipment" or
  "maintenance" - zero matches.
- Tested the closest available data (`tmserviceorder`, `tmserviceconfirmation`,
  `tmservicerequest` - Service Order/Confirmation/Request) - their `ReferenceObjectCollection`
  only carries a `ProductID` (+ optional `SerialID`), i.e. ByDesign treats "equipment" as a
  serialized instance of a regular Product, not as its own master-data object.
- **Conclusion:** if Equipment/maintenance data is genuinely needed, it has to come from a
  different system - this data was never in ByDesign to begin with.

**#40 BOMs** - **this one IS an access/exposure issue, but at the platform-configuration level,
not a per-user permission level.** SAP definitely has this data internally (every ByDesign tenant
running Manufacturing has bills of material - `khproductionorder`'s
`ProductionModelID`/`ProductionModelVersionID` fields directly reference them) - it's just not
published as an OData service:
- Searched every one of the 1,485 entity sets the tenant publishes through its live catalog, and
  every one of the 609 entity sets defined across all 47 imported custom service `.xml` files,
  for "bom", "bill of material", "production model", "recipe", "routing", "explosion" - **zero
  matches in either.**
- SAP calls this internal BOM data "PBOM." It exists, but by default a ByDesign tenant does not
  expose PBOM through any OData service.
- **Fix required:** someone with **SAP Business Configuration admin access** (not just a data
  read role - actual system configuration rights) needs to go into that admin area and turn on
  external/OData access to the PBOM data sources. This is a one-time setup action on SAP's side,
  not something achievable by importing another custom service `.xml` file or granting the
  integration user more work-center roles.

### The 4 objects blocked by an actual permission problem (fixable by SAP admin, no code needed)

These ARE the "we can see it exists but don't have rights to read it" case - the opposite of
BOMs/Equipment above. The service is imported, live, and its field structure (`$metadata`) is
visible - but every attempt to read actual rows returns
`RBAM_ERROR: Not Authorized: Check Authorization Restriction for the User`:

| Service | What it would unlock | Work center the integration user needs granted |
|---|---|---|
| `khproject` | #7-adjacent analytic accounts (Project-based) | **Project Management** |
| `khlead` | #19 Activities | **Leads** |
| `tmserviceorder` | Service order data (no direct sheet object) | **Service Orders** |
| `tmservicerequest` | Service request data (no direct sheet object) | **Service Requests** |

**Fix:** ask whoever administers SAP user roles to grant the SDK/integration user access to
these four work centers. Once granted, re-running `python -m src.main --only <service>` will
pull the data with no code changes needed - this is purely a rights-grant, not an engineering task.

**Re-tested 2026-09-24 and found NOT actually blocked** (previously miscategorized): `khcustomerquote`
and `tmserviceconfirmation` are live, authorised, and return HTTP 200 - just 0 rows, meaning no
data exists in this tenant, not an access problem. `khcustomerreturn` is live, authorised, and
had 810 real rows that were simply never extracted before (now fixed and mapped into #66 Credit
Notes - see `SAP_IMPORT_PLAN.md`).

### 13 optional objects: `pending_mapping` (a source is believed to exist, not yet wired up)

Fiscal Positions (#3), Journal Entries (#13), Fixed Assets (#14), Asset Depreciation (#15),
Attachments/Documents (#16), Activities (#19), Lot/Serial Numbers (#30), Routings (#41),
Operations (#43), Purchase Requisitions (#48), Customer Price Lists (#61), Quotations (#62),
Currency Exchange Rates (#0). Most are waiting on one of the remaining un-imported custom SAP
service files - see `SAP_IMPORT_PLAN.md` for exactly which file unlocks which object.

### 14 optional objects: `not_in_bydesign` (confirmed, no equivalent module exists)

Engineering/PLM (Engineering Items, Document Revisions, ECO, Drawings - 4 objects), Maintenance
(Equipment-adjacent: Maintenance Teams, Checklists, Preventive Schedule, Requests, History - 5
objects beyond #34 itself), Quality (Quality Teams, Quality Points, Quality Checks, Inspection
Results - 4 objects). Confirmed via a zero-match search of the tenant's full 573-entry Design
Data Sources catalog for each module's terminology.

## Full-column export (`output_full_csv/`)

For completeness beyond the standard Odoo mapping: **12,200,300 rows across 3,345 columns**,
covering every field SAP returned for every one of the 497 entity sets extracted - including the
385 entity sets that have no standard Odoo field to map into. See README.md's "Full-column
export" section for how to load these as Odoo custom fields.

## Data-quality audit (`diagnostics/`)

`python diagnostics/validate_relations.py` independently re-checks every `output_odoo/*.csv`
file from scratch - every relation column, does it actually resolve to a real record; is every
`id` column actually unique; column-by-column blank/distinct counts. It found and led to fixing
one real bug (`stock_picking_transfer.csv` had 3 duplicate external IDs, because SAP itself
reuses the same delivery `ID` across two different `ObjectID`s for a handful of Customer
Returns - now keyed on the always-unique `ObjectID` instead). The remaining **760 unresolved
relation values (0.13% of all 599,565 rows)** were each traced to a real, explained cause - not
a bug - documented in `diagnostics/FINDINGS.md`. Full per-file numbers in
`diagnostics/RELATION_REPORT.md`.

## Known SAP-side issues (not fixable from this codebase)

- `khpurchaseorder/NotesCollection` returns `HTTP 500 Internal Server Error` at `$skip=2000` -
  reproducible on every run, appears to be a defect in SAP's own service, not a query issue.
- `bpm_businesspartnerdata_analytics.svc/RPBPCSRSPEMPTERM_Q0001QueryResults` times out (~4
  minutes) before failing - a known-flaky analytics report on this tenant.
- 7 `*AttachmentFolderCollection`/`AttachmentCollection` entity sets return `400 Bad Request` no
  matter what's queried (confirmed: single-field select, no select, parent-scoped filter - all
  fail identically) - the entity sets themselves aren't exposed for direct queries on this
  tenant. Low priority: attachment/document *metadata* only, not any of the 66 sheet objects.

Regenerate this file's numbers any time with `python -m src.validate` and by reading the current
`output_odoo/*.csv` row counts - nothing here is hand-maintained.
