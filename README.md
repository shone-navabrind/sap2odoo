# sap2odoo

Extracts business data from an SAP Business ByDesign tenant and produces CSV files ready to
import into Odoo.

## 👉 Start here

| If you want to… | Read / open |
|---|---|
| **Know what to do next in SAP** — which service files to import, and what each one unlocks | **[`SAP_IMPORT_PLAN.md`](SAP_IMPORT_PLAN.md)** |
| **Understand the whole project** (technical or not) — what's done, which API gives what, sample data, what's left | **[`DATA_MIGRATION_GUIDE.md`](DATA_MIGRATION_GUIDE.md)** |
| See **everything in one table** — every API call, records in/out, status | **`CONSOLIDATED_STATUS.csv`** |
| Track progress against the client's 66-object requirement sheet | `PROJECT_STATUS.csv` |
| Look up **field-by-field** mappings (SAP field → Odoo field) | `SAP_Field_Mapping.xlsx` (one tab per object) |
| Find a SAP data source by name — every entity set the tenant publishes | `SERVICE_CATALOG.csv` (1485 rows) |
| Understand the code architecture and SAP quirks | `CLAUDE.md` |

## Status (2026-09-23)

**In one paragraph:** 39 of the 67 requirement-sheet objects have real, verified SAP data flowing
into Odoo-ready CSVs today, and that includes **19 of the 21 objects marked mandatory** — every
mandatory master and transaction the client asked for is done except two, and both of those two
have been investigated exhaustively rather than left unexplained: **Equipment (#34)** doesn't
exist as a distinct data object anywhere in standard ByDesign (it's modelled as a serialized
product, not master data - confirmed against the tenant's full catalog three times), and
**BOMs (#40)** are confirmed absent from all 1,485 published + 609 XML-defined entity sets this
tenant exposes, meaning no further OData import can produce them - both need a decision from
Danlaw/the client (source elsewhere, manual entry, or descope) rather than more engineering here.
Of the remaining 28 objects, 14 are `pending_mapping` (optional; a source is believed to exist but
isn't wired up yet, mostly waiting on 9 still-unimported custom SAP service files - none of them
unlock a mandatory item) and 14 are `not_in_bydesign` (the Engineering/PLM, Maintenance/PM, and
Quality/QM modules aren't part of standard Business ByDesign at all, confirmed by a zero-match
search of the tenant's 573-entry Design Data Sources catalog).

**Mandatory objects (19/21 done):**

| Status | Count | Detail |
|---|---|---|
| ✅ Done | 19 | See the per-object table below for the exact command/file for each |
| ⛔ Not achievable via SAP OData | 1 | **#34 Equipment** - no such object exists in standard ByDesign |
| ⛔ Needs a Business Configuration change, not an import | 1 | **#40 BOMs** - confirmed absent from every entity set the tenant publishes; needs PBOM data sources exposed via SAP Business Configuration, or a manual/alternate-source export |

**Optional objects (20/46 done):**

| Status | Count | What to do next |
|---|---|---|
| ✅ Done | 20 | - |
| 🟡 `pending_mapping` | 13 | Import one of the 9 remaining custom service `.xml` files listed in `SAP_IMPORT_PLAN.md` (`byd-api-samples-main/Custom OData Services/`), then re-run `python -m src.main` (or `--only <service>` for just that one) |
| ⚪ `not_in_bydesign` | 13 | Confirm with the client whether this data genuinely needs to come from a different source system - standard ByDesign has no equivalent module |

(The registry's 14 `pending_mapping` and 14 `not_in_bydesign` totals shown above each include one
of the two remaining mandatory gaps - BOMs and Equipment respectively - already counted in the
mandatory table, hence 13 here rather than 14.)

**Next actions, in priority order:**
1. Confirm with the client/Danlaw how to handle the two remaining mandatory gaps (#34 Equipment,
   #40 BOMs) - both are data-availability questions, not code questions.
2. Import the 9 remaining optional custom services (`SAP_IMPORT_PLAN.md` has the exact file names
   and what each one unlocks) if that data is wanted.
3. Get the SDK/integration user's SAP authorizations extended for the services currently blocked
   by `RBAM_ERROR` (khcustomerquote, khproject, khlead, khcustomerreturn, tmserviceorder,
   tmservicerequest - see `SAP_IMPORT_PLAN.md`), which unlock several of the 14 `pending_mapping`
   objects without any further engineering.
4. Confirm the 14 `not_in_bydesign` objects (Engineering/PLM, Maintenance, Quality) are genuinely
   out of scope, or identify their real source system if they're needed.

All of these numbers are read live from `src/registry.py` and whatever is actually on disk - never
hand-typed - so re-run `python -m src.validate` any time to get the current true count.

## How it works (pipeline)

`python -m src.main` runs every stage below **in this exact order, one after another** (not in
parallel - each stage fully finishes before the next starts):

```
        SAP Business ByDesign tenant  (HTTP Basic auth, credentials in .env)
                          │
                          ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ STAGE 1 — EXTRACT                                                  │
 │ src/extract_raw.py  (SOURCES list drives which entities get pulled)│
 │ src/sap_client.py   (does the actual HTTP calls, pagination,       │
 │                       OLAP chunk-merge, $metadata parsing)         │
 │ writes -> output_raw/*.json  (every field SAP declares, no         │
 │           filtering, no mapping - one file per entity set)         │
 └──────────────────────────────┬───────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ STAGE 2 — TRANSFORM                                                │
 │ src/transform_odoo.py  (one build_*() per object, reads only       │
 │                          output_raw/ - no SAP calls)               │
 │ writes -> output_odoo/*.csv   (Odoo-ready import files - only the  │
 │           fields Odoo has a standard home for)                     │
 └──────────────────────────────┬───────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ STAGE 2b — FULL-COLUMN EXPORT                                      │
 │ src/export_full_csv.py  (reads output_raw/ AND output_odoo/ - no   │
 │                           SAP calls)                                │
 │ writes -> output_full_csv/    (every SAP column for every entity   │
 │           set - nothing Stage 2 left out is lost; see "Full-column │
 │           export" below)                                           │
 └──────────────────────────────┬───────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ STAGE 3 — VALIDATE                                                  │
 │ src/validate.py  (cross-checks output_odoo/ against src/registry.py,│
 │                    the 66+1 object requirement list)                │
 └──────────────────────────────┬───────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ STAGE 4 — REPORT                                                    │
 │ src/status_report.py       -> PROJECT_STATUS.csv                   │
 │ src/consolidated_report.py -> CONSOLIDATED_STATUS.csv               │
 └──────────────────────────────────────────────────────────────────┘
```

Stages 1 and 2 are deliberately separate processes reading/writing plain files on disk (not an
in-memory handoff), so Stage 2 (and 2b, 3, 4) can be re-run any number of times against the SAP
calls already made in Stage 1, without re-hitting SAP — see "Where the logic lives" below for
which file to touch for which kind of change.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`.env` already has working SAP credentials for this tenant filled in.

## Run

```bash
./test_sap_endpoints.sh       # curl-only sanity check against the real SAP tenant
python -m src.main            # full pipeline: raw extraction -> Odoo transform -> validation
```

Or run each stage independently:

```bash
python -m src.extract_raw     # Stage 1: pull raw data from SAP -> output_raw/*.json
python -m src.transform_odoo  # Stage 2: map raw data to Odoo CSVs -> output_odoo/*.csv (offline)
python -m src.validate        # Cross-check output_odoo/ against the 66-object registry
python -m src.status_report   # Regenerate PROJECT_STATUS.csv from whatever's currently on disk
python -m src.consolidated_report               # Rebuild CONSOLIDATED_STATUS.csv
python -m src.generate_field_mapping_workbook   # Rebuild SAP_Field_Mapping.xlsx
```

## Where the logic lives

| To change… | Edit this file |
|---|---|
| Which SAP (service, entity set) pairs get pulled in Stage 1 | `src/extract_raw.py` — the `SOURCES` list |
| SAP HTTP calls, pagination, the OLAP chunk/merge-key logic, `$metadata` parsing | `src/sap_client.py` |
| How a raw SAP column maps onto an Odoo field | `src/transform_odoo.py` — one `build_*()` function per object, registered in the `TRANSFORMS` list at the bottom |
| Which requirement-sheet objects exist, their mandatory flag, status, and notes | `src/registry.py` — the `REGISTRY` list of `ObjectSpec` |
| The full-column export layout, or which SAP columns attach to which Odoo model file | `src/export_full_csv.py` — `MODEL_ROOTS` (root + child entities per Odoo file) and `ODOO_KEY` (external-ID join keys) |
| Registry-vs-`output_odoo/` cross-check (Stage 3) | `src/validate.py` |
| The team status CSV (Stage 4) | `src/status_report.py` |
| The one-row-per-SAP-call CSV (Stage 4) | `src/consolidated_report.py` |
| The field-by-field Excel workbook | `src/generate_field_mapping_workbook.py` |
| Credentials, page size, timeouts, output/log directories | `.env` (values) / `src/config.py` (which env vars exist) |
| Pipeline orchestration / stage order | `src/main.py` |
| Which un-imported custom services unlock which objects next | `src/import_plan.py` → generates `SAP_IMPORT_PLAN.md` |
| Finding live/dead custom services, un-pulled entity sets, or un-mapped fields | `src/probe_services.py`, `src/coverage_gap.py` (see "Validating field coverage" below) |

### Running just one object, or a limited/quick pull

Every stage above (`main`, `extract_raw`, `transform_odoo`) accepts `--only TEXT` to scope the
run to a single business object instead of the whole tenant - matched case-insensitively as a
substring against a SAP service name, a SAP entity set name, or the Odoo transform's label
(whichever is easiest to remember). `--only` for `extract_raw`/`main` matches service/entity
names; `--only` for `transform_odoo` matches the transform label shown in its log output.

```bash
python -m src.main --only khcustomer            # full pipeline, just Customers
python -m src.main --only res_partner           # same thing, matched by the Odoo transform label
python -m src.extract_raw --only vmumaterial     # Stage 1 only - just re-pull Products from SAP
python -m src.transform_odoo --only product_template   # Stage 2 only - re-map from existing output_raw/
```

`extract_raw` and `main` also accept `--limit N` to cap every entity set at N rows during
extraction (via `$top`, so it actually limits the SAP calls made, not just what's kept) - useful
for a quick connectivity/shape check against a new or live tenant before committing to a full
multi-hour pull:

```bash
python -m src.main --only khcustomer --limit 50   # pull 50 Customer rows and run the pipeline on them
```

**Caveat:** since matching is substring-based against both service *and* entity set names, a
short/generic word can match more than you expect - e.g. `--only costcentre` also matches
`khcostcentre` and `khfunctionalunit` (their entity sets contain "CostCentre" in the name), not
just the `costcentre` service. That's harmless (the extra data simply gets pulled too and
whatever's unrelated just sits unread), but if you want precision, use a full service name from
the tables below rather than a generic word.

#### Every built object, individually

One row per object that currently has real data (39 of 67; the remaining 28 are `pending_mapping`
or `not_in_bydesign` - see `SAP_IMPORT_PLAN.md` and `PROJECT_STATUS.csv` for those). The Extract
column is the Stage 1 command(s) to re-pull just that object's raw data from SAP; Transform is the
Stage 2 command to re-map it from whatever is already in `output_raw/` (no SAP calls, runs in
seconds); Output is the `output_odoo/*.csv` file(s) it produces.

#### Accounts

| # | Object | Mandatory | Extract (Stage 1) | Transform (Stage 2) | Output |
|---|---|---|---|---|---|
| 1 | Chart of Accounts | **Yes** | `python -m src.extract_raw --only fin_costandrevenue_analytics.svc && python -m src.extract_raw --only fin_audit_analytics.svc && python -m src.extract_raw --only fin_generalledger_analytics.svc` | `python -m src.transform_odoo --only "account_account (chart of accounts)"` | `account_account.csv` |
| 2 | Taxes | **Yes** | `python -m src.extract_raw --only fin_taxmanagement_analytics.svc` | `python -m src.transform_odoo --only "account_tax (taxes)"` | `account_tax.csv` |
| 4 | Payment Terms | **Yes** | `python -m src.extract_raw --only khcustomerinvoice && python -m src.extract_raw --only khsupplierinvoice` | `python -m src.transform_odoo --only "account_payment_term"` | `account_payment_term.csv` |
| 5 | Banks | **Yes** | `python -m src.extract_raw --only khhousebankaccount && python -m src.extract_raw --only khcustomer && python -m src.extract_raw --only khsupplier` | `python -m src.transform_odoo --only "res_partner / res_bank / res_partner_bank"` | `res_bank.csv + res_partner_bank.csv` |
| 6 | Cost Centers | optional | `python -m src.extract_raw --only costcentre` | `python -m src.transform_odoo --only "account_analytic_plan / account_analytic_account (cost centers)"` | `account_analytic_account_cc.csv` |
| 7 | Analytic Accounts | optional | `python -m src.extract_raw --only khprofitcentre` | `python -m src.transform_odoo --only "account_analytic_account (profit centres)"` | `account_analytic_account.csv` |
| 8 | Open Customer Invoices | **Yes** | `python -m src.extract_raw --only fin_receivablesar_analytics.svc` | `python -m src.transform_odoo --only "account_move_open_customer (open customer invoices)"` | `account_move_open_customer.csv` |
| 9 | Open Vendor Bills | **Yes** | `python -m src.extract_raw --only khsupplierinvoice` | `python -m src.transform_odoo --only "account_move_vendor_bill(_line)"` | `account_move_open_vendor.csv` |
| 10 | Customer Payments | optional | `python -m src.extract_raw --only khpayment` | `python -m src.transform_odoo --only "account_payment"` | `account_payment.csv` |
| 11 | Vendor Payments | optional | `python -m src.extract_raw --only khpayment` | `python -m src.transform_odoo --only "account_payment"` | `account_payment.csv` |
| 12 | Bank Statements | optional | `python -m src.extract_raw --only khhousebankstatement` | `python -m src.transform_odoo --only "account_bank_statement"` | `account_bank_statement.csv` |

#### CRM

| # | Object | Mandatory | Extract (Stage 1) | Transform (Stage 2) | Output |
|---|---|---|---|---|---|
| 17 | Customers | **Yes** | `python -m src.extract_raw --only khcustomer` | `python -m src.transform_odoo --only "res_partner / res_bank / res_partner_bank"` | `res_partner.csv` |
| 18 | Salespersons | **Yes** | `python -m src.extract_raw --only khemployee` | `python -m src.transform_odoo --only "res_users (employees/salespersons)"` | `res_users.csv` |
| 20 | Opportunities | optional | `python -m src.extract_raw --only khopportunity` | `python -m src.transform_odoo --only "crm_lead (opportunities)"` | `crm_lead.csv` |
| 21 | Open Opportunities | **Yes** | `python -m src.extract_raw --only khopportunity` | `python -m src.transform_odoo --only "crm_lead (opportunities)"` | `crm_lead_open.csv` |
| 22 | Closed Opportunities | optional | `python -m src.extract_raw --only khopportunity` | `python -m src.transform_odoo --only "crm_lead (opportunities)"` | `crm_lead_closed.csv` |

#### Inventory

| # | Object | Mandatory | Extract (Stage 1) | Transform (Stage 2) | Output |
|---|---|---|---|---|---|
| 27 | Warehouses | **Yes** | `python -m src.extract_raw --only khlocation` | `python -m src.transform_odoo --only "stock_location"` | `stock_warehouse.csv` |
| 28 | Locations | **Yes** | `python -m src.extract_raw --only khlocation` | `python -m src.transform_odoo --only "stock_location"` | `stock_location.csv` |
| 29 | UOM | **Yes** | `python -m src.extract_raw --only vmumaterial` | `python -m src.transform_odoo --only "uom_uom"` | `uom_uom.csv` |
| 31 | Inventory Adjustments | optional | `python -m src.extract_raw --only scm_physicalinventory_analytics.svc` | `python -m src.transform_odoo --only "stock_quant_adjustment (inventory balances)"` | `stock_quant_adjustment.csv` |
| 32 | Stock Transfers | optional | `python -m src.extract_raw --only khinbounddelivery` | `python -m src.transform_odoo --only "stock_picking_transfer (inbound deliveries)"` | `stock_picking_transfer.csv + _line.csv` |
| 33 | Stock Moves History | optional | `python -m src.extract_raw --only khgoodsandserviceacknowledgement` | `python -m src.transform_odoo --only "stock_move_history (goods receipts)"` | `stock_move_history.csv` |

#### Manufacturing

| # | Object | Mandatory | Extract (Stage 1) | Transform (Stage 2) | Output |
|---|---|---|---|---|---|
| 42 | Work Centers | **Yes** | `python -m src.extract_raw --only khproductionorder` | `python -m src.transform_odoo --only "mrp_workcenter"` | `mrp_workcenter.csv` |
| 44 | Manufacturing Orders | optional | `python -m src.extract_raw --only khproductionorder` | `python -m src.transform_odoo --only "mrp_production"` | `mrp_production.csv` |
| 45 | Production History | optional | `python -m src.extract_raw --only khproductionorder` | `python -m src.transform_odoo --only "mrp_production_history (closed orders)"` | `mrp_production_history.csv` |

#### Purchase

| # | Object | Mandatory | Extract (Stage 1) | Transform (Stage 2) | Output |
|---|---|---|---|---|---|
| 46 | Vendors | **Yes** | `python -m src.extract_raw --only khsupplier` | `python -m src.transform_odoo --only "res_partner / res_bank / res_partner_bank"` | `res_partner.csv` |
| 47 | Vendor Pricelists | optional | `python -m src.extract_raw --only vmumaterial` | `python -m src.transform_odoo --only "product_supplierinfo (vendor pricelists)"` | `product_supplierinfo.csv` |
| 49 | RFQs | optional | `python -m src.extract_raw --only khpurchaseorder` | `python -m src.transform_odoo --only "purchase_order_rfq (draft purchase orders)"` | `purchase_order_rfq.csv` |
| 50 | Purchase Orders | **Yes** | `python -m src.extract_raw --only khpurchaseorder` | `python -m src.transform_odoo --only "purchase_order / purchase_order_line"` | `purchase_order.csv + _line.csv` |
| 51 | Vendor Bills | optional | `python -m src.extract_raw --only khsupplierinvoice` | `python -m src.transform_odoo --only "account_move_vendor_bill(_line)"` | `account_move_vendor_bill.csv + _line.csv` |
| 52 | Vendor Credit Notes | optional | `python -m src.extract_raw --only khsupplierinvoice` | `python -m src.transform_odoo --only "account_move credit notes (customer + vendor)"` | `account_move_vendor_credit.csv` |

#### Sales

| # | Object | Mandatory | Extract (Stage 1) | Transform (Stage 2) | Output |
|---|---|---|---|---|---|
| 57 | Products | **Yes** | `python -m src.extract_raw --only vmumaterial && python -m src.extract_raw --only vmumaterialvaluationdata` | `python -m src.transform_odoo --only "product_template"` | `product_template.csv` |
| 58 | Product Categories | **Yes** | `python -m src.extract_raw --only vmumaterial` | `python -m src.transform_odoo --only "product_category"` | `product_category.csv` |
| 59 | Pricelists | **Yes** | `python -m src.extract_raw --only khsalesarrangement` | `python -m src.transform_odoo --only "product_pricelist (sales arrangements)"` | `product_pricelist.csv` |
| 60 | Discount Rules | optional | `python -m src.extract_raw --only khsalesorder` | `python -m src.transform_odoo --only "product_pricelist_item (list prices)"` | `product_pricelist_item_discount.csv` |
| 63 | Sales Orders | **Yes** | `python -m src.extract_raw --only khsalesorder` | `python -m src.transform_odoo --only "sale_order / sale_order_line"` | `sale_order.csv + _line.csv` |
| 64 | Deliveries | optional | `python -m src.extract_raw --only khoutbounddelivery` | `python -m src.transform_odoo --only "stock_picking_delivery(_line)"` | `stock_picking_delivery.csv + _line.csv` |
| 65 | Customer Invoices | optional | `python -m src.extract_raw --only khcustomerinvoice` | `python -m src.transform_odoo --only "account_move_customer_invoice(_line)"` | `account_move_customer_invoice.csv + _line.csv` |
| 66 | Credit Notes | optional | `python -m src.extract_raw --only khcustomerinvoicerequest` | `python -m src.transform_odoo --only "account_move credit notes (customer + vendor)"` | `account_move_credit_note.csv` |

## Team status tracking (`PROJECT_STATUS.csv`)

Regenerated automatically at the end of every `python -m src.main` run. One row per sheet
object — module, mandatory Yes/Optional, status, the exact Odoo file + row count, the exact raw
SAP file(s) + row count(s) behind it, and notes. Hand this file to the team; it only reports what
the pipeline actually produced, never an assertion.

## Finding SAP data sources

The tenant publishes its own catalog — service names never need to be guessed:

```bash
curl -u "$SAP_USERNAME:$SAP_PASSWORD" "$SAP_BASE_URL/sap/byd/odata/"       # every service
curl -u "$SAP_USERNAME:$SAP_PASSWORD" "$SAP_BASE_URL/sap/byd/odata/<svc>/" # its entity sets
```

`python -m src.discover_catalog` walks both and `python -m src.catalog_report` turns the result
into the searchable `SERVICE_CATALOG.csv` (1485 entity sets across 48 services, each joined to its
business description). To search it:

```bash
python -m src.catalog_report "g/l account"     # or any term
```

Three URL patterns are in use: `/sap/byd/odata/<svc>.svc/<EntitySet>` (analytics),
`/sap/byd/odata/v1/<svc>/<EntitySet>` (standard) and `/sap/byd/odata/cust/v1/<svc>/<EntitySet>`
(the custom Cloud Applications Studio services imported from
`byd-api-samples-main/Custom OData Services/`).

Once a candidate is found: verify it with `./test_sap_endpoints.sh`, add the service/entity-set
pair to `SOURCES` in `src/extract_raw.py`, run the extraction, inspect the real columns in the
written `output_raw/*.json`, then map those columns in `transform_odoo.py` and flip the object's
registry status to `built`.

## Discovery and diagnostic tools

| Command | What it does |
|---|---|
| `python -m src.discover_catalog` | Walk the tenant's live service catalog → `schema_snapshots/service_catalog.json` |
| `python -m src.catalog_report [terms]` | Readable `SERVICE_CATALOG.csv`, or search it |
| `python -m src.snapshot_metadata` | Capture `$metadata` for every analytics service |
| `python -m src.parse_service_defs [svc]` | Parse the 47 custom service `.xml` files offline |
| `python -m src.probe_services` | Which custom services are live on the tenant |
| `python -m src.probe_entity_sets` | Row counts for entity sets not yet extracted |
| `python -m src.coverage_gap` | Live entity sets never pulled + SAP fields never mapped |
| `python -m src.probe_gl_accounts` | Which reports actually return G/L accounts |
| `python -m src.import_plan` | Regenerate `SAP_IMPORT_PLAN.md` |
| `python -m src.export_full_csv` | Every SAP column to CSV in `output_full_csv/` (see above) |

### `python -m src.snapshot_metadata` in detail

**What it's for:** live `$metadata` for `*_analytics.svc` (BI/OLAP report) services is unstable on
this tenant - the first call can return 90-100 real fields, and a later call, with no code or
query change, can collapse to just `ID` + `TotaledProperties` (see CLAUDE.md, "Critical gotcha:
live `$metadata` is unstable"). `src/sap_client.py`'s `get_entity_fields()` always prefers a
captured file over a live call when one exists, so a real field list, once seen, is never lost to
a later collapse.

**What it does:** walks every `*_analytics.svc` service except the three catch-all aggregators
(`ana_businessanalytics_analytics.svc` and friends - their metadata is permanently collapsed, so
walking them is pointless), fetches `$metadata`, and writes `schema_snapshots/<service>.metadata.xml`
- but only when the fetched metadata actually has real fields (more than 2 properties), so a good
existing snapshot is never overwritten with a collapsed one.

**When to run it:**
- Once, right after pointing `.env` at a **different tenant** (a new `SAP_BASE_URL`) - the
  snapshots on disk were captured against whichever tenant was live at the time, and a different
  tenant can have different fields. Custom `kh*` services don't need this (they fetch `$metadata`
  live, successfully, every run - see CLAUDE.md) but the analytics `.svc` services do.
- If an analytics-sourced Odoo file (Chart of Accounts #1, Taxes #2, Open Customer Invoices #8,
  Inventory Adjustments #31) looks like it's missing a column that should be there.
- It is **not** part of `python -m src.main` - it changes files under `schema_snapshots/` (code
  configuration, not business data), so it's run manually/occasionally, not on every extraction.

```bash
python -m src.snapshot_metadata
```

## Validating field coverage (did we capture every field?)

Four different tools answer this, at different granularity - none of them assert correctness on
their own; together they trace every claim back to `output_raw/*.json`, never to a guess:

| Question | Tool |
|---|---|
| Is this **object** (e.g. "Products") done? | `python -m src.validate` — registry-level, cross-checks `src/registry.py` against `output_odoo/` |
| Which **entity sets** does the tenant expose that we've never even pulled? Which **fields** did we pull but never map to an Odoo column? | `python -m src.coverage_gap` |
| For a specific **Odoo file**, exactly which raw SAP columns made it in vs. which stayed unmapped? | `output_full_csv/_INDEX.csv` (per entity set) and `_MODEL_INDEX.csv` (per Odoo model file) — regenerate with `python -m src.export_full_csv` |
| **Field-by-field**, human-readable: this SAP field → this Odoo column (or blank) | `SAP_Field_Mapping.xlsx` — one worksheet per object, regenerate with `python -m src.generate_field_mapping_workbook` |

In short: `coverage_gap.py` and `_INDEX.csv`/`_MODEL_INDEX.csv` are the actual field-completeness
check (they read `output_raw/*.json` and `src/transform_odoo.py`'s source and report what's
unread); `SAP_Field_Mapping.xlsx` is the same information laid out for a non-technical reviewer.

## Full-column export (`output_full_csv/`)

`output_odoo/` deliberately contains only the fields that map onto a standard Odoo field. Of the
~3,200 columns SAP returns, most have no standard Odoo home — so they would be silently dropped.
`output_full_csv/` is the answer to that: **every column of every SAP entity set, as CSV.**

```bash
python -m src.export_full_csv      # reads output_raw/ only, no SAP calls, safe to re-run
```

| Path | What it is |
|---|---|
| `odoo_models/<odoo_model_file>.csv` | **Recommended full export.** The same model-named files and Odoo import columns as `output_odoo/`, followed by every SAP root/child field that can be linked safely to that Odoo record. One-to-many child values are JSON arrays, so nothing is dropped or duplicated. |
| `entities/<service>__<EntitySet>.csv` | One file per SAP entity set, one row per SAP record, **every column**. A direct transcription of the raw JSON — this is the guarantee nothing was dropped. |
| `objects/<service>.csv` | The convenience view: each service's root entity widened with its one-to-one children, child columns prefixed `<Child>.<Field>`. |
| `_INDEX.csv` | One row per entity set — rows, columns, whether any Odoo transform reads it, which Odoo file it feeds, and whether it was merged into an object file or left standalone (with the reason). |
| `_MODEL_INDEX.csv` | One row per Odoo-model file — source lineage, column count, and direct SAP-link coverage. |

Current output: **376 entity CSVs (256,137 rows, 3,225 columns), 26 object CSVs.**
**276 of the 376 entity sets are not read by any Odoo transform** — that data exists only here.

### Using it to populate Odoo custom fields

Where a record corresponds one-to-one with an Odoo record, both layouts carry an
**`odoo_external_id`** column holding the same external ID as the matching `output_odoo/` file.
So the workflow is:

1. Import `output_odoo/*.csv` as normal — these create the records.
2. Create the custom fields you want in Odoo.
3. Use the matching `output_full_csv/odoo_models/<odoo_model_file>.csv`, mapping `id` to
   *External ID* and each `sap__...` column to its custom field. Odoo updates the existing
   records rather than creating duplicates. Use `entities/` only when you need an SAP child
   collection as its own Odoo model.

Verified end to end: `objects/vmumaterial.csv` widens Products from 18 to 130 columns and its
3,058 external IDs all match `product_template.csv`; `objects/khcustomer.csv` +
`objects/khsupplier.csv` cover all 288 IDs in `res_partner.csv`.

**Children with several rows per parent are deliberately not merged** into the object files —
flattening them would either duplicate the parent row or throw rows away. They stay in
`entities/` in full, and `_INDEX.csv` says so per entity set.

`output_full_csv/` is gitignored, like `output_raw/` and `output_odoo/` — it is real business
data. Regenerate it with the command above.

## Import into Odoo

In Odoo: Settings > Technical > Import, or each app's list view > Favorites > Import Records.
Import files from `output_odoo/` — `res_partner.csv` before `res_partner_bank.csv` (it references
partners), `res_bank.csv` before `res_partner_bank.csv` (it references banks).
