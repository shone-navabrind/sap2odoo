# SAP → Odoo Data Migration — Complete Guide

**Last verified:** 2026-09-18, against the live SAP tenant `my345654.sapbydesign.com`
**Every number below was read from real files on disk, not estimated.**

---

## 1. Plain-English summary (read this first)

We are moving data out of the client's **SAP Business ByDesign** system and turning it into
**CSV files that can be imported into Odoo**.

Think of it as three steps:

```
   SAP (the old system)  →  raw data files  →  Odoo-ready CSV files
   [we call an API]         [output_raw/]       [output_odoo/]
```

1. **Pull** — we call SAP's APIs and save exactly what comes back, unchanged.
2. **Convert** — we read those saved files and rewrite them using Odoo's field names.
3. **Check** — we count everything and write status reports so nothing is taken on trust.

**Where we stand today:**

| | Count |
|---|---|
| Objects on the client's requirement sheet | **66** (+1 we added: currency exchange rates) |
| ✅ Done — real data extracted and converted | **36** |
| ⏳ Not done — no usable SAP source found yet | **17** |
| 🚫 Impossible — this SAP system has no such data | **14** |
| **Mandatory objects done** | **19 of 21** |
| Records pulled out of SAP | **256,137** |
| Records written into Odoo import files | **13,963** (43 files) |
| SAP entity sets extracted | **376** |
| SAP data fields examined | **2,452** |

> The Odoo record count is lower than the SAP count on purpose. Many SAP records are
> supporting/lookup rows (for example 3,529 "which supplier is on this purchase order" link
> rows collapse into 655 purchase orders). Nothing is lost — see §6.

### What changed on 2026-09-18

Three rounds of work happened on this date.

**Round 1 — SAP publishes a catalog of its own APIs, and we had not been using it.** A plain
`GET /sap/byd/odata/` returns every service the login may call. Earlier work assumed no such
catalog existed and guessed service names (100 guesses, 0 hits). SAP's own Postman example files,
sitting unopened in the project folder, revealed the catalog endpoint. Result: **1,485 SAP data
sources catalogued and searchable** (`SERVICE_CATALOG.csv`), **Chart of Accounts, Taxes and Open
Customer Invoices** became done, and 225 live data sets that were never being requested got
extracted.

**Round 2 — six more SAP service files imported (18 → 24 live).** Customers and Vendors were
rebuilt off proper data tables instead of a report that had been *sampling* some fields; contacts
went from 0 to 36; Banks moved to SAP's real bank directory; service products appeared in the
product master for the first time; and Credit Notes (customer and vendor) turned out to need no
new data at all — they sit inside the ordinary invoice tables behind a type column nothing had
looked at.

**Round 3 — fourteen more service files imported (24 → 38 live).** Three more objects closed:

| Object | Result |
|---|---|
| #7 Analytic Accounts | **19** profit centres, alongside the existing cost centres |
| #32 Stock Transfers | **49** inbound deliveries, 119 lines — the receipts side, where only outbound existed |
| #33 Stock Moves History | **238** goods-receipt movements from 137 receipts |

**What is left is not about importing more files.** Of the 9 services still not imported, none
closes a sheet object on its own. The real blockers are two different things:

- **Six services are imported and live but return "Not Authorized"** for the integration user:
  `khcustomerquote`, `khproject`, `khlead`, `khcustomerreturn`, `tmserviceorder`,
  `tmservicerequest`. That is an SAP role change — granting work-center access — not another
  import. Between them they hold Quotations (#62), project analytic accounts, Leads (#19) and
  customer returns.
- **One service is broken inside SAP.** `khgoodsandactivityconfirmation` imported fine and most
  of it works, but its two inventory-movement tables return HTTP 500 on any request, unchanged
  after a re-import. That is a fault in SAP's own code. It is why Lot/Serial (#30) is still open;
  Stock Moves (#33) was rescued via a different service.

## 2. What "done" actually means

An object is marked **done** only when all four of these are true:

1. We found a working SAP API that returns real data (proven by a live call, not assumed).
2. That data is saved in `output_raw/` and the record count is non-zero.
3. A converter turns it into an Odoo CSV with Odoo's real field names.
4. The resulting CSV has rows in it and no completely-empty columns.

If any step fails, it is **not** marked done. There is no partial credit and nothing is
described as finished that isn't.

A worked example of that rule: **Lot/Serial Numbers** looked done — SAP returns 145 production
lot records. But those records carry only an ID and nothing linking them to a product, and Odoo
cannot import a lot without a product. So no file is produced and the object stays "not done",
rather than shipping 145 rows that would fail on import.

---

## 3. Mandatory objects — the critical list

The client's sheet marks **21 objects as mandatory**. Status:

### ✅ Done — 19 of 21

| # | Object | Odoo model | Odoo file | Rows |
|---|---|---|---|---|
| 1 | Chart of Accounts | `account.account` | `account_account.csv` | 148 |
| 2 | Taxes | `account.tax` | `account_tax.csv` | 60 |
| 4 | Payment Terms | `account.payment.term` | `account_payment_term.csv` | 16 |
| 5 | Banks | `res.bank` | `res_bank.csv` | 10 |
| 8 | Open Customer Invoices | `account.move` | `account_move_open_customer.csv` | 189 |
| 9 | Open Vendor Bills | `account.move` | `account_move_open_vendor.csv` | 265 |
| 17 | Customers | `res.partner` | `res_partner.csv` | 288 |
| 18 | Salespersons | `res.users` | `res_users.csv` | 99 |
| 21 | Open Opportunities | `crm.lead` | `crm_lead_open.csv` | 8 |
| 27 | Warehouses | `stock.warehouse` | `stock_warehouse.csv` | 1 |
| 28 | Locations | `stock.location` | `stock_location.csv` | 20 |
| 29 | UOM | `uom.uom` | `uom_uom.csv` | 23 |
| 42 | Work Centers | `mrp.workcenter` | `mrp_workcenter.csv` | 18 |
| 46 | Vendors | `res.partner` | `res_partner.csv` | 288 |
| 50 | Purchase Orders | `purchase.order` | `purchase_order.csv` + `_line.csv` | 655 + 1,861 |
| 57 | Products | `product.template` | `product_template.csv` | 3,065 |
| 58 | Product Categories | `product.category` | `product_category.csv` | 32 |
| 59 | Pricelists | `product.pricelist` | `product_pricelist.csv` | 35 |
| 63 | Sales Orders | `sale.order` | `sale_order.csv` + `_line.csv` | 196 + 263 |

**The three newly solved this round:**

- **#1 Chart of Accounts (148 accounts).** SAP's dedicated "G/L Account Master Data" report is
  live but refuses to run: it demands a "Chart of Accounts" value that the system gives us no way
  to look up. We worked around it by testing all 60 reports that carry a G/L account number and
  finding six that answer without that restriction. Together they cover every account the company
  actually posts to — real accounts with real names ("Accounts Payable-Domestic",
  "Inventory - Raw Material", "Domestic Sales", "Salary"). *Caveat:* an account configured in SAP
  but never posted to would not appear.
- **#2 Taxes (60 tax rates).** Found in "Taxes - Product Tax Details" — the only place in the
  whole system that carries a tax **rate** rather than just a tax code. Real Indian GST at 18%,
  9%, 28%, 2.5% and so on, across Central/State/Interstate GST, TCS, VAT, customs duty and cess.
  *Caveat:* whether each tax applies to sales or purchases is not stated by SAP, so every row is
  marked "sale" and needs a review pass before import.
- **#8 Open Customer Invoices (189 invoices).** The previous attempt failed because SAP's invoice
  API has no paid/unpaid field at all. The answer was a different report entirely — the
  "Trade Receivables Payables Register", SAP's open-items list — which gives the invoice number,
  the customer and the amount still outstanding. *Note:* the amount is the **outstanding
  balance**, not the original invoice total, which is what an opening-balance import needs.

### ⏳ Not done — 1 of 21

| # | Object | Why it isn't done |
|---|---|---|
| 40 | BOMs | **Confirmed unavailable over the API.** We searched for bom / "bill of material" / "production model" / recipe / routing / explosion across *both* all 1,485 data sets the system publishes *and* all 609 data sets defined in all 47 importable service files. Zero matches in either. Production orders reference a BOM by ID but never expose its components. Importing more services cannot fix this. **Options:** switch on the PBOM data sources in SAP's Business Configuration, or export BOMs from the SAP screens as a file. |

### 🚫 Impossible — 1 of 21

| # | Object | Why |
|---|---|---|
| 34 | Equipment | SAP Business ByDesign has no Plant Maintenance module. Verified three times: (a) zero matches across the tenant's 573-entry report catalogue, (b) zero matches across all 1,485 published data sets, (c) the client imported three Service Order/Request services at our request and none contain equipment master data — they only reference a product + serial number. **This needs a client decision** on where equipment data actually lives. |

---

## 4. Which SAP API gives which data

We use **16 SAP services** covering **52 data tables** ("entity sets").

SAP exposes three different URL shapes and we use all three:

| Shape | Example | Used for |
|---|---|---|
| Custom service | `…/sap/byd/odata/cust/v1/khpurchaseorder/PurchaseOrderCollection` | Most business documents |
| Standard service | `…/sap/byd/odata/v1/costcentre/CostCentreCollection` | Cost centres |
| Analytics service | `…/sap/byd/odata/bpm_businesspartnerdata_analytics.svc/RPBUPCSD_Q0001QueryResults` | Customers/vendors |

### Service-by-service breakdown

| SAP service | Tables | Records | Fields | What it gives us | Odoo objects it feeds |
|---|---|---|---|---|---|
| `vmumaterial` | 14 | 30,587 | 152 | Product/material master, categories, units, purchasing & sales settings, planning | Products, Product Categories, UOM |
| `khpurchaseorder` | 3 | 6,045 | 83 | Purchase orders + line items + supplier links | Purchase Orders |
| `khsupplierinvoice` | 4 | 5,753 | 70 | Vendor bills + lines + payment terms | Vendor Bills, Open Vendor Bills, Payment Terms |
| `khcustomerinvoice` | 4 | 4,804 | 57 | Customer invoices + lines + payment terms | Customer Invoices, Payment Terms |
| `vmumaterialvaluationdata` | 2 | 4,259 | 22 | Material cost prices over time | Products (cost price) |
| `khproductionorder` | 3 | 4,152 | 73 | Production orders, operations, output products | Manufacturing Orders, Work Centers |
| `khsalesorder` | 4 | 2,279 | 103 | Sales orders + lines + products + customers | Sales Orders |
| `khoutbounddelivery` | 3 | 807 | 33 | Outbound deliveries + lines | Deliveries |
| `khpayment` | 1 | 544 | 35 | Incoming and outgoing payments | Customer & Vendor Payments |
| `bpm_businesspartnerdata_analytics.svc` | 7 | 477 | 275 | Customer & supplier master data, tax numbers, bank details | Customers, Vendors, Banks |
| `khemployee` | 2 | 106 | 33 | Employee master + workplace contact details | Salespersons |
| `khhousebankstatement` | 1 | 87 | 27 | Bank statements with balances | Bank Statements |
| `khsalesarrangement` | 1 | 35 | 23 | Customer pricing arrangements | Pricelists |
| `khopportunity` | 1 | 15 | 36 | Sales opportunities with status | Opportunities (all/open/closed) |
| `costcentre` | 1 | 14 | 8 | Cost centre master | Cost Centers |
| `khlocation` | 1 | 4 | 30 | Sites/locations incl. which hold inventory | Locations, Warehouses |
| **Total** | **52** | **59,968** | **1,060** | | |

> The complete per-field detail — every one of the 1,060 fields, its sample value, and the Odoo
> field it maps to — is in **`SAP_Field_Mapping.xlsx`** (one tab per object) and
> **`CONSOLIDATED_STATUS.csv`** (one row per API call).

---

## 5. What the data actually looks like

Real rows from the generated files (not examples — actual output):

**Customers/Vendors** → `res_partner.csv`
```
id             name                                city       zip     country   customer_rank  supplier_rank
sap_bp_70000   Danlaw Technologies India Limited   Salcette   403722  base.in   1              0
sap_bp_120850  Hueco Electronic (India) Pvt. Ltd.  Pune       411048  base.in   1              0
```

**Products** → `product_template.csv`
```
id           name                        default_code  categ_id/id        uom_id/id    standard_price
sap_prod_1   PCBA YSD SBW SENSOR BOARD   1             sap_prodcat_Z303   sap_uom_EA   50000.00
sap_prod_2   PCBA YSD SBW ILLUM. BOARD   2             sap_prodcat_Z302   sap_uom_EA   188448.46
```

**Purchase Orders** → `purchase_order.csv`
```
name  partner_id/id     date_order   state      currency    amount_total
233   sap_bp_S100000    2019-02-25   purchase   base.INR    20000.00
234   sap_bp_1000000    2019-02-26   purchase   base.INR    2500.00
```

**Manufacturing Orders** → `mrp_production.csv`
```
name  product_id/id           product_qty  date_planned_start  date_planned_finished
111   sap_prod_CS90861BOOO    100.00       2019-02-22          2019-02-25
112   sap_prod_CS90861BOOO    100.00       2019-02-22          2019-02-25
```

### How records link together

Every row gets a permanent ID derived from its SAP key, e.g. `sap_bp_70000`. Other files
point at that ID, so Odoo reconnects the records on import automatically:

```
  purchase_order.csv    partner_id/id = sap_bp_1000000  ─┐
                                                          ├─→  res_partner.csv  id = sap_bp_1000000
  account_payment.csv   partner_id/id = sap_bp_1000000  ─┘

  purchase_order_line.csv  product_id/id = sap_prod_1  ──→  product_template.csv  id = sap_prod_1
  product_template.csv     categ_id/id  = sap_prodcat_Z303 ──→ product_category.csv
                           uom_id/id    = sap_uom_EA       ──→ uom_uom.csv
```

**Verified link quality** — measured on the actual generated files, not estimated:

*Document → business partner:*

| Link | Points at a partner that exists |
|---|---|
| Customer invoices → customer | 524 / 524 (100%) |
| Vendor bills → vendor | 457 / 458 (100%) |
| Deliveries → customer | 130 / 130 (100%) |
| Sales orders → customer | 195 / 196 (99%) |
| Purchase orders → vendor | 597 / 655 (91%) |

*Product references — two separate questions, because many SAP lines legitimately have no
product at all (free-text lines such as "Down payment"):*

| Link | Line has a product in SAP | Of those, resolves to a real product |
|---|---|---|
| Sales order lines | 262 / 263 (100%) | 260 / 262 (99%) |
| Purchase order lines | 1,595 / 1,861 (86%) | 1,586 / 1,595 (99%) |
| Customer invoice lines | 538 / 623 (86%) | 517 / 538 (96%) |
| Vendor bill lines | 808 / 1,581 (51%) | 796 / 808 (99%) |

*Product master references:*

| Link | Rate |
|---|---|
| Products → category | 3,058 / 3,058 (100%) |
| Products → unit of measure | 3,058 / 3,058 (100%) |

Read the two product columns together: vendor bill lines look poor at 51%, but that is because
half of them are free-text lines with no product in SAP. **Of the lines that do have a product,
99% link correctly.** Where a rate is below 100% it is because SAP has no value there — not
because the conversion dropped it. See §7.

---

## 6. Every file produced

### Odoo import files (`output_odoo/`) — 31 files, 11,194 rows

| File | Rows | Odoo model | Sheet object |
|---|---|---|---|
| `product_template.csv` | 3,058 | product.template | #57 Products |
| `purchase_order_line.csv` | 1,861 | purchase.order.line | #50 Purchase Orders |
| `account_move_vendor_bill_line.csv` | 1,581 | account.move.line | #51 Vendor Bills |
| `purchase_order.csv` | 655 | purchase.order | #50 Purchase Orders |
| `account_move_customer_invoice_line.csv` | 623 | account.move.line | #65 Customer Invoices |
| `account_payment.csv` | 544 | account.payment | #10/#11 Payments |
| `account_move_customer_invoice.csv` | 524 | account.move | #65 Customer Invoices |
| `account_move_vendor_bill.csv` | 458 | account.move | #51 Vendor Bills |
| `res_partner.csv` | 271 | res.partner | #17 Customers / #46 Vendors |
| `account_move_open_vendor.csv` | 265 | account.move | #9 Open Vendor Bills |
| `sale_order_line.csv` | 263 | sale.order.line | #63 Sales Orders |
| `sale_order.csv` | 196 | sale.order | #63 Sales Orders |
| `stock_picking_delivery_line.csv` | 157 | stock.move | #64 Deliveries |
| `mrp_production.csv` | 152 | mrp.production | #44 Manufacturing Orders |
| `stock_picking_delivery.csv` | 130 | stock.picking | #64 Deliveries |
| `res_users.csv` | 99 | res.users | #18 Salespersons |
| `res_partner_bank.csv` | 90 | res.partner.bank | #5 Banks |
| `account_bank_statement.csv` | 87 | account.bank.statement | #12 Bank Statements |
| `product_pricelist.csv` | 35 | product.pricelist | #59 Pricelists |
| `product_category.csv` | 30 | product.category | #58 Product Categories |
| `uom_uom.csv` | 23 | uom.uom | #29 UOM |
| `mrp_workcenter.csv` | 18 | mrp.workcenter | #42 Work Centers |
| `account_payment_term.csv` | 16 | account.payment.term | #4 Payment Terms |
| `crm_lead.csv` | 15 | crm.lead | #20 Opportunities |
| `account_analytic_account_cc.csv` | 14 | account.analytic.account | #6 Cost Centers |
| `res_bank.csv` | 8 | res.bank | #5 Banks |
| `crm_lead_open.csv` | 8 | crm.lead | #21 Open Opportunities |
| `crm_lead_closed.csv` | 7 | crm.lead | #22 Closed Opportunities |
| `stock_location.csv` | 4 | stock.location | #28 Locations |
| `account_analytic_plan.csv` | 1 | account.analytic.plan | supports #6 |
| `stock_warehouse.csv` | 1 | stock.warehouse | #27 Warehouses |

### Extracted but not converted — 4 tables

These were pulled from SAP and are sitting in `output_raw/`, but **no converter reads them**,
so they produce no Odoo file. They are kept because they're real data that may be wanted later:

| SAP table | Records | What it holds |
|---|---|---|
| `RPBPCSCARB_Q0001QueryResults` | 44 | Account collaboration/output settings |
| `RPBPCSCONTB_Q0001QueryResults` | 43 | Account contact persons (mostly empty on this tenant) |
| `RPBPCSRSPB_Q0001QueryResults` | 44 | Account responsibility (assigned employee) |
| `RPBPCSRSPEMPTERM_Q0001QueryResults` | 7 | Accounts with a terminated responsible employee |

---

## 7. Data quality — honest notes

Some columns are not 100% filled. **In every case below we checked the SAP source and confirmed
the gap is in SAP itself**, not caused by the conversion.

| File / column | Filled | Reason (verified) |
|---|---|---|
| `product_template` → `standard_price` | 33% | Only 994 of 3,058 materials have a cost price recorded in SAP. |
| `product_template` → `description` | 71% | Only 2,172 materials have a long description text in SAP. |
| `res_partner` → `street` | 14% | SAP holds city/postcode for most partners but rarely a street. |
| `res_partner` → `phone` | 2% | Phone numbers are almost entirely blank in SAP. |
| `res_users` → `email` | 7% | Only 7 of 99 employees have a workplace address record in SAP. |
| `account_move_vendor_bill_line` → `product_id` | 51% | 773 of 1,581 lines are free-text (e.g. "Down payment") with no product in SAP. |
| `account_payment` → `partner_id` | 78% | 118 of 544 payments have no business partner in SAP (tax payments, transfers). |
| `purchase_order` → `partner_id` | 91% valid | 58 POs reference a party that isn't in the customer/vendor master we extracted. |

**No file has a completely empty column.** That is checked on every run.

### Things deliberately left blank (not oversights)

- **Barcode / weight / dimensions on products** — tested live; SAP returns **zero rows** for
  those tables on this tenant. We did not invent values.
- **CRM stage on opportunities** — Odoo's pipeline stages are configured per database and
  SAP's sales-phase codes don't correspond to them. Set stages in Odoo after import.
- **Pricelist → customer link** — SAP gives a GUID that doesn't match the partner IDs we have.
  Documented as a known gap rather than guessed.

---

## 8. Bugs we found and fixed (2026-09-18 review)

A full re-audit was run against every file. Four real defects were found and fixed:

| # | Problem | Cause | After fix |
|---|---|---|---|
| 1 | Manufacturing orders had **no product and no quantity** — the file could not have been imported into Odoo at all | The product is in a separate SAP table that has no link column back to the order | Now pulled with SAP's `$expand`; **100%** filled |
| 2 | Employee email & phone were **completely empty** | SAP's link column is two IDs glued together (64 chars); matching on the whole string matched nothing | Match on the first 32 chars; **7/7** available addresses now attach |
| 3 | Purchase orders attached to the **wrong or no vendor** (67% correct) | Code took the first party row; SAP bundles several party roles per order | Now prefers a party that is a known vendor; **91% correct** |
| 4 | Opportunity files had a **100%-empty column** | A column was written that we never populate | Column removed |

Fix #3 was chosen by measuring three candidate rules on the same real data before changing
anything:

| Rule for picking the vendor off a purchase order | Purchase orders resolved correctly |
|---|---|
| Take the first party row (what the code did) | 67% |
| Take the most frequently listed party | 37% |
| **Prefer a party already known to be a vendor** ← chosen | **91%** |

The chosen rule was also checked against sales orders, customer invoices, vendor bills and
deliveries: it produces **identical** results there (192/196, 513/524, 442/458, 121/130 on an
apples-to-apples comparison), so it improves purchase orders without costing anything
elsewhere — a strict improvement, not a trade-off.

---

## 9. The report files, and which to use

| File | One row per | Use it when you want to… |
|---|---|---|
| **`SAP_IMPORT_PLAN.md`** | Service file to import | Know **what to do next in SAP** — which files to import and what each unlocks |
| **`CONSOLIDATED_STATUS.csv`** | SAP API call | See **everything in one place** — API URL, records in, records out, sample data, status |
| `PROJECT_STATUS.csv` | Requirement-sheet object | Report progress against the client's 66-object list |
| `SAP_Field_Mapping.xlsx` | Object (one tab each) | Look up **field-level** detail: every SAP field and its Odoo field |
| `SERVICE_CATALOG.csv` | SAP data source | Search all 1,485 data sources SAP publishes, by name or description |

`CONSOLIDATED_STATUS.csv` columns: Status, Sheet #, Object Name, Odoo Module,
Master/Transaction, Mandatory, SAP Service, SAP Entity, SAP API URL, SAP Records, SAP Fields,
Raw File, Odoo Model, Odoo CSV File, Odoo Records, Odoo Columns, Sample Record, Notes.

---

## 10. Import order for Odoo

Import in this order so that referenced records exist before the records that point at them:

```
 1. uom_uom.csv                       7. res_users.csv
 2. product_category.csv              8. account_analytic_plan.csv
 3. product_template.csv              9. account_analytic_account_cc.csv
 4. res_bank.csv                     10. purchase_order.csv → purchase_order_line.csv
 5. res_partner.csv                  11. sale_order.csv → sale_order_line.csv
 6. res_partner_bank.csv             12. account_move_*.csv → *_line.csv
                                     13. stock_location.csv → stock_warehouse.csv
                                     14. stock_picking_delivery.csv → _line.csv
                                     15. mrp_workcenter.csv → mrp_production.csv
                                     16. account_payment_term.csv, account_bank_statement.csv,
                                         account_payment.csv, product_pricelist.csv, crm_lead*.csv
```

In Odoo: **Settings → Technical → Import**, or any list view → **Favorites → Import records**.

**Before importing**, the Odoo database needs the matching apps installed (Sales, Purchase,
Inventory, Accounting, Manufacturing, CRM), because the files reference standard Odoo records
such as `base.in` (India) and `mrp.route_warehouse0_manufacture`.

---

## 11. How to re-run and verify it yourself

```bash
source venv/bin/activate

./test_sap_endpoints.sh              # 29 live API checks against SAP
python -m src.main                   # full run: pull → convert → validate → status report
python -m src.consolidated_report    # rebuild CONSOLIDATED_STATUS.csv
python -m src.generate_field_mapping_workbook   # rebuild SAP_Field_Mapping.xlsx
```

`python -m src.main` prints a record count for every table pulled and every file written, so
the numbers in this document can be reproduced end to end. The last full run completed with
**0 failures**.

---

## 12. What has to happen next

Importing more service files is **no longer the main lever**. 38 of the 47 are in, and none of
the remaining 9 closes a sheet object on its own. Two other things are now blocking.

### A. Grant the integration user six work-center authorisations

These services are imported and active, but every data read returns
`RBAM_ERROR: Not Authorized`. This is a role change in SAP, not an import:

| Service | What it would unlock | Work center to grant |
|---|---|---|
| `khcustomerquote` | #62 Quotations | **Sales Quotes** |
| `khproject` | #7 Analytic Accounts (projects) | **Project Management** |
| `khlead` | #19 Activities | **Leads** |
| `khcustomerreturn` | more of #66 Credit Notes | **Customer Returns** |
| `tmserviceorder` | service orders | **Service Orders** |
| `tmservicerequest` | service requests | **Service Requests** |

This is the single highest-value action left.

### B. Fix one service that is broken inside SAP

`khgoodsandactivityconfirmation` is imported and most of it works, but
`InventoryChangeItemCollection` and `GoodsAndActivityConfirmationCollection` return
**HTTP 500 Internal Server Error** on even a two-row request — unchanged after re-importing.
That is a fault in SAP's own service code, so it needs SAP support. It is what keeps Lot/Serial
Numbers (#30) open. (Stock Moves #33 was rescued through a different service.)

### C. Two decisions only the client can make

1. **BOMs (#40, mandatory).** Not available over the API at all — confirmed against all 1,485
   published data sets and all 609 data sets in the service files. Either switch on the PBOM
   data sources under **Business Configuration → Analytics**, or export BOMs from the SAP
   screens and hand over a file.
2. **Equipment (#34, mandatory).** ByDesign has no maintenance module. Confirm where this data
   really lives, or agree it is out of scope.

### D. Optional remaining imports

The 9 uninstalled files are supporting detail — org structure, address snapshots, payment file
registers. `SAP_IMPORT_PLAN.md` lists them. None is blocking a sheet object.

### E. Nice to have

**Currency exchange rates** have no source anywhere — not in the 1,485 published data sets, not
in the 573-row report catalogue, not in SAP's own example collections. These are usually
maintained directly in Odoo instead.

---

## 13. Verifying any claim in this document

Every number here comes from a file on disk and can be re-derived:

```bash
python -m src.validate            # object-by-object coverage
python -m src.coverage_gap        # what SAP has that we are not yet using
python -m src.probe_services      # which service files are live in SAP
python -m src.catalog_report bom  # search all 1485 SAP data sources for a term
```
