# CLAUDE.md — sap2odoo

## Purpose

Extracts business data from an SAP Business ByDesign tenant and produces CSV files ready to
import into Odoo. Scope is the 66-object list from `Danlaw SAP Data Migration-Jul2026 -
Sheet1.csv`, tracked object-by-object in `src/registry.py` (source of truth for what's built vs.
pending vs. not applicable — run `python -m src.validate` for a live report).

## Data source catalog research (2026-09-15)

The user exported ByDesign's **Business Analytics → Design Data Sources (All)** list
(`schema_snapshots/ByDesign_Design_Data_Sources_catalog.csv`, 573 rows: Data Source Name,
Technical ID, Description, "Exposed" flag). Cross-checking it against all 66 registry objects
confirmed that underlying reporting data genuinely exists in this tenant for most objects (Chart
of Accounts, Sales Orders, Purchase Orders, Customer/Supplier Invoices, Fixed Assets, BOMs,
Manufacturing Orders, Opportunities, Employees, etc.) — see each `ObjectSpec.note` in
`src/registry.py` for the matched Technical ID(s). It also confirmed **zero matches** for
Equipment/Maintenance or Quality-related terms, reinforcing the `not_in_bydesign` flags for those
14 objects.

**This export does NOT give us live OData service names.** Its "Exposed" column was "No" for
*every single row*, including "Account Details" (BPCSDB) — which we know for certain is live,
since it's the confirmed working `RPBUPCSD_Q0001QueryResults` entity. So this particular screen
tracks report/analytics *definitions*, not live OData *publishing* status. Also confirmed: the
live `.svc` name (`bpm_businesspartnerdata_analytics`) has no derivable string relationship to
its Data Source Technical IDs (`BPCSDB`, `BPCSCARB`, etc.) — not a prefix, not an anagram. Two
full rounds of pattern-based guessing using confirmed module prefixes pulled straight from this
catalog (`CRM`, `SRM`, `SCM`, `FIN`, `HCM` — ByDesign's real internal module abbreviations,
confirmed by the Technical IDs themselves) still produced **0/100 hits**. Further guessing has
next to no expected value at this point.

> **Superseded on 2026-09-18 — see "The service catalog" below.** The paragraph above is kept
> because its *negative* findings still hold (the Design Data Sources export is not an OData
> publication catalog, and its Technical IDs are not service names), but its conclusion —
> that service names had to be guessed or read out of the Communication Arrangements screen —
> was wrong. The tenant publishes a live catalog, and one GET replaces all of that guessing.

## The service catalog (2026-09-18): guessing is over

The tenant answers a plain authenticated GET with a complete list of every OData service the
current user may call:

```
GET {SAP_BASE_URL}/sap/byd/odata/            -> Atom feed of 48 <service>.svc links
GET {SAP_BASE_URL}/sap/byd/odata/<svc>/      -> that service's entity sets (Atom or JSON)
```

`python -m src.discover_catalog` walks both and writes `schema_snapshots/service_catalog.json`:
**48 services, 1485 entity sets.** `python -m src.catalog_report` joins each entity set to the
business description from the Design Data Sources export and writes the readable
`SERVICE_CATALOG.csv`; pass search terms to grep it (`python -m src.catalog_report "g/l account"`).

This was found by reading SAP's own Postman collections in `byd-api-samples-main/Postman/`,
which had been sitting in the repo unexamined. The two prior rounds of name-guessing (0/100)
were unnecessary.

**Analytics entity sets are named `RP<DataSourceID>_<Query>QueryResults`**, and `<DataSourceID>`
is exactly the Technical ID column of the Design Data Sources export — so that 573-row CSV is
now useful after all, as a description lookup rather than as a service catalog.

**Every report is reachable twice**: through the catch-all `ana_businessanalytics_analytics.svc`
(582 entity sets) and through its own module service (`fin_generalledger_analytics.svc` etc).
Always use the module service — `$metadata` collapses to `ID` + `TotaledProperties` on the
catch-all but resolves fully on the module services. `python -m src.snapshot_metadata` captures
all of them into `schema_snapshots/`, which is where `get_entity_fields()` looks first.

**Analytics reports can declare mandatory variables.** `RPFINGLAU17` ("G/L Account Master Data")
has precisely the right fields but returns 0 rows and rejects any filter until a Chart of Accounts
key is supplied, and the tenant exposes no way to read the valid keys. Where a report is blocked
this way, check whether other reports carry the same characteristic without the variable — see
`src/probe_gl_accounts.py`, which recovered all 122 G/L accounts in use from six unblocked reports.

## BREAKTHROUGH (2026-09-15): custom OData services via Cloud Applications Studio

The user has a `byd-api-samples-main/Custom OData Services/` folder containing **47 custom
OData service definitions** (`.xml` files, one per business object: `khsalesorder.xml`,
`khcustomerinvoice.xml`, `khsupplierinvoice.xml`, `khcostcentre.xml`, `khemployee.xml`,
`khopportunity.xml`, `khproductionorder.xml`, `khpayment.xml`, `khlocation.xml`, etc. — almost a
1:1 match against the registry's `pending_mapping` objects). These are Cloud Applications Studio
custom business objects, importable into the ByDesign tenant and then live as real OData
services. This is a **third, distinct path pattern** on top of the two already documented:

```
{SAP_BASE_URL}/sap/byd/odata/cust/v1/<service_name>/<EntitySet>
```

(`cust` = custom namespace, confirmed from each XML's `<SERVICE_NAMESPACE>cust</SERVICE_NAMESPACE>`
and `<SERVICE_NAME>kh...</SERVICE_NAME>` tags.) Unlike the analytics `.svc` services, these are
**plain CRUD OData v2** — no drill-down field-count limit, `$select` with any number of fields
works, `ID` is a real business field (not a synthetic composite), and standard `$expand` works on
navigation properties.

**First one wired up and confirmed working: Purchase Orders.** The user imported
`khpurchaseorder.xml` into the tenant. Real service, real data:

| Entity set | What it is | Rows | Key fields |
|---|---|---|---|
| `PurchaseOrderCollection` | PO header | 655 | `ObjectID` (key), `ID` (real PO number) |
| `ItemCollection` | PO line items | 1861 | `ParentObjectID` → header `ObjectID` |
| `SupplierCollection` | PO → supplier BP link | 3529 | `ParentObjectID` → header, `PartyID` → matches `res_partner.csv`'s `CBP_UUID`-based external IDs directly |

→ `purchase_order.csv` (655 rows) + `purchase_order_line.csv` (1861 rows), `partner_id/id`
resolves for 496/655 (the rest have no linked `SupplierCollection` row on this tenant — a data
gap in SAP, not a bug here). `product_id/id` on lines references `sap_prod_<ProductID>` but won't
resolve until Products (#57) is separately wired up (1595/1861 lines have a real `ProductID`).

**Next step, once the user imports more `kh*.xml` files:** same pattern as
`get_entity_set_all_fields()` already handles correctly for both plain-CRUD and OLAP entity
shapes (see the docstring on that method) — curl-verify the service exists
(`sap/byd/odata/cust/v1/<name>/$metadata`), add its entity sets to `extract_raw.py`'s `SOURCES`,
inspect the real columns in `output_raw/`, write a transform. No blind guessing needed anymore —
the `.xml` files name the exact `SERVICE_NAME` to use.

## Architecture: two stages, kept strictly separate

**Stage 1 — `python -m src.extract_raw`** pulls data out of SAP and dumps it exactly as SAP
returns it, into `output_raw/*.json`. No field curation, no Odoo mapping — every field SAP
declares for a confirmed entity set gets pulled and written. Field mapping decisions are made
*after* seeing what's actually there, not guessed beforehand (a lesson learned the hard way: an
earlier version pre-selected ~9 fields per entity by hand and missed ~95 real, useful columns).

**Stage 2 — `python -m src.transform_odoo`** reads `output_raw/*.json` (offline, no SAP calls)
and writes Odoo-ready CSVs to `output_odoo/*.csv`, using real column names confirmed present in
the raw dumps.

**Stage 2b — `python -m src.export_full_csv`** writes EVERY column of every entity set to
`output_full_csv/`, because Stage 2 maps only the fields Odoo has a standard home for - roughly
a tenth of the ~3,225 columns SAP returns. `entities/` is a lossless one-CSV-per-entity-set
transcription; `objects/` widens each service's root entity with its one-to-one children;
`odoo_models/` mirrors `output_odoo/`'s file-per-Odoo-object layout but with every linkable SAP
column attached (one-to-many children become a JSON array per cell); `_INDEX.csv` /
`_MODEL_INDEX.csv` say what is in each and which of it no Odoo transform reads. Reads only
`output_raw/`; no SAP calls.

> **Fixed 2026-09-24**: `python -m src.coverage_gap` found 193 entity sets that were confirmed
> LIVE on the tenant (real business fields, not codelists) but had never been added to `SOURCES`
> at all - so they weren't extracted, full stop, regardless of what Stage 2b claims to capture.
> 122 of the 193 belonged to 7 whole services (`khcustomerquote`, `khcustomerreturn`, `khlead`,
> `khproject`, `tmserviceconfirmation`, `tmserviceorder`, `tmservicerequest`) that were imported
> into SAP but never wired into the code at all; the other 71 were supplementary tables (Notes,
> Attachments, extra contact details) on services that were already partly wired up. All 193 were
> generated programmatically from `missing_entity_sets()` and appended to `SOURCES` (now 569
> entries, was 376) - `coverage_gap.py --sets` reports 0 remaining. Note this is separate from,
> and does not affect, "GAP 2" (SAP fields extracted but not mapped into a standard Odoo column) -
> that data was never missing, it was always in `output_full_csv/entities/`; only GAP 1 (entity
> sets never in `SOURCES` at all) meant genuinely-missing data.

> **Fixed 2026-09-23**: `write_odoo_model_csvs()` used to rescan a child entity's ENTIRE row list
> for every parent row (`[r for r in child["rows"] if ...]` inside the parent loop) instead of
> indexing children by parent key once. On a small tenant this was invisible; on the live tenant
> (`khsupplierinvoice` alone has ~55k parent rows joining against ~680k child rows per child
> collection) it made this stage run for hours and had to be killed with Ctrl+C. Children are now
> indexed into a `{parent_id: [rows]}` dict once per service before the parent loop runs -
> confirmed the same local dataset that used to take unbounded time now finishes in ~5s.

**`python -m src.main`** runs all stages plus a validation pass against the registry, in order.

Why split them: re-running a transform after fixing a mapping bug doesn't require re-hitting SAP
(slow, chunked, rate-sensitive calls); and the raw dumps are themselves a useful audit trail of
exactly what SAP has, independent of any Odoo decisions made on top of them.

### Running one object, or a limited pull

`python -m src.main`, `extract_raw`, and `transform_odoo` all accept `--only TEXT` (matched
case-insensitively against a service name, entity set, or transform label) to scope a run to one
business object - e.g. `python -m src.main --only khcustomer` or `--only res_partner`.
`extract_raw`/`main` also take `--limit N` to cap every entity set at N rows, for a quick
connectivity/shape check before committing to a full pull; `--workers N` to pull entity sets
concurrently via a thread pool instead of one at a time (default stays sequential; confirmed safe
at `--workers 5` on this tenant on 2026-09-24 - ~5 entity sets/minute including 300k+ row ones
that took 5-11 minutes each sequentially, no new errors); and `--resume` to skip anything already
in `output_raw/` (switch an in-progress or interrupted run to a different `--workers` count
without re-pulling). `--only`/`--limit` were added because the full
tenant pull is a multi-hour, many-hundred-call operation, and until now there was no way to
re-pull or re-test just one object without re-running everything.

> **Real incident, fixed 2026-09-25**: a `--limit 5` connectivity check on `khhousebankaccount`
> left 6 files on disk at 5-6 rows each. A later `--resume` run saw those files already existed
> and treated them as done, since `--resume` originally only checked "does the file exist," not
> whether it was a full pull. Result: `res_bank.csv` silently degraded from 287 to 279 real bank
> directory records for about a day, losing real region/state data (432 of 956 banks have a
> populated `RegionCode`/`RegionCodeText`) until a user noticed banks were missing region data
> they'd seen before. Fixed at the source: every extracted file now records `"limited": true`
> in its JSON when `--limit` was used, and `--resume` skips a file only when that flag is absent
> - a limited pull can never again be mistaken for a complete one, and a warning is logged
> listing exactly which files it's re-pulling for this reason. If you ever run a `--limit` test
> and then want the FULL data, `--resume` now handles it correctly on its own - no manual
> cleanup needed.

## Source system: SAP Business ByDesign, not S/4HANA

Tenant: originally `my345654.sapbydesign.com` (documented below); the user has since pointed
`.env` at a second, live tenant (`my346623.sapbydesign.com`) with the same architecture. **The
`schema_snapshots/*.metadata.xml` files were captured against the first tenant.** Custom `kh*`
services fetch metadata live every run (see "Critical gotcha: live `$metadata` is unstable"
below) so they self-correct against whichever tenant `.env` points at; the catch-all-avoiding
`*_analytics.svc` snapshots do not, and could miss fields the live tenant has that the captured
tenant didn't. If analytics output looks like it's missing columns on the new tenant, re-run
`python -m src.snapshot_metadata` against it before assuming the field doesn't exist.

Real OData path pattern (confirmed working):
`{SAP_BASE_URL}/sap/byd/odata/<service_name>.svc/<EntitySet>` — a small named `.svc` service per
business area with SAP-internal names (not S/4HANA's `/API_XXX_SRV/...`, and not a single
flat catalog). The names do not have to be guessed: `GET {SAP_BASE_URL}/sap/byd/odata/` lists
every one of them — see "The service catalog" above. `test_sap_endpoints.sh` still curl-verifies
individual endpoints.

**Confirmed working:** `bpm_businesspartnerdata_analytics.svc`, with 7 entity sets pulled in
full:

| Entity set | What it is | Rows | Business key field |
|---|---|---|---|
| `RPBUPCSD_Q0001QueryResults` | Account Details (customer-side) | 44 | `CBP_UUID` |
| `RPBUPSPP_Q0001QueryResults` | Supplier Details | 229 | `CBP_UUID` |
| `RPBUPATAXNUMBERS_Q0001QueryResults` | Tax numbers | 66 | `CBUPA_UUID` |
| `RPBPCSCARB_Q0001QueryResults` | Account Collaboration Data | 44 | `CROOT_UUID` |
| `RPBPCSCONTB_Q0001QueryResults` | Account Contact Data | 43 | `CBP_UUID` |
| `RPBPCSRSPB_Q0001QueryResults` | Account Responsibility Data | 44 | `CROOT_UUID` |
| `RPBPCSRSPEMPTERM_Q0001QueryResults` | Accounts w/ term. resp. employee | 7 | `CACCOUNT_UUID` |

### Critical gotcha: these are OLAP/BI queries, not CRUD entities

Selecting more than ~8-11 dimension fields at once fails with
`Program error in class CL_RSBOLAP_QV_RESULT_SET: TOO_MANY_DRILL_DOWN_OBJECTS`. Worse: **the
result is a GROUP BY over whichever dimensions you select** — different field subsets return
*different row counts* for the same entity set (confirmed by testing: one subset gave 44 rows,
another gave 4, another 48, for the identical `RPBUPCSD_Q0001QueryResults` entity, depending only
on which fields were in `$select`). This makes naive chunk-and-merge-by-position unsafe.

`SAPODataClient.get_entity_set_all_fields()` (`src/sap_client.py`) handles this by:
1. Getting the entity's full field list (see snapshot note below).
2. Dropping `P_*`/`PARA_*` fields (query *parameters*, not real columns — SAP rejects them in
   `$select` with "Invalid Property") and the synthetic `ID`/`TotaledProperties` fields.
3. Picking the first field whose name contains `UUID` as the **business key** and keeping it in
   every chunk's `$select`.
4. Fetching each chunk, then merging by that key's *value* (not row position) into one row per
   business object.

### Critical gotcha: live `$metadata` is unstable on this tenant

The very first `$metadata` call the user made (via curl) returned ~90-108 real properties per
entity type. Later calls — including from this tool, no code changes in between — returned only
`ID` + `TotaledProperties` (a collapsed/generic schema). This is almost certainly because these
are BI report-backed OData services and `$metadata` reflects some live, mutable report/session
state on the SAP side, not a fixed data dictionary. **Consequence:** live `$metadata` cannot be
trusted as the source of truth for "what fields exist."

Fix: `schema_snapshots/<service>.metadata.xml` holds a captured-known-good copy of the full
metadata, and `get_entity_fields()` in `src/sap_client.py` prefers that snapshot over a live call
whenever one exists. The underlying data via named `$select` fields still works fine even when
`$metadata` has collapsed — only the metadata *listing* is unstable, not the actual data access.

**Update 2026-09-18 — the collapse is not random.** `$metadata` collapses on the *catch-all*
analytics services (`ana_businessanalytics_analytics.svc` with its 582 entity sets, and the two
other aggregator services) and resolves correctly on the *per-module* services. Running
`python -m src.snapshot_metadata` captured real field lists for **35 of 36** analytics services
in one pass — 402 entity types, every one with its full property list. So the rule is now:
address a report through its module service (`fin_generalledger_analytics.svc`), never through
the catch-all, and re-run `snapshot_metadata` rather than hand-capturing curl output.

## Odoo output today (`output_odoo/`)

**46 files, 14,198 rows.** The authoritative, always-current list is `CONSOLIDATED_STATUS.csv`
(one row per SAP API call) and `PROJECT_STATUS.csv` (one row per sheet object) - both regenerated
from disk on every run. Highlights rather than a duplicate of those:

| File | Rows | Built from |
|---|---|---|
| `account_account.csv` | 148 | Chart of accounts, unioned from the six analytics reports that expose `CGLACCT`/`TGLACCT` (see `src/probe_gl_accounts.py`) |
| `account_tax.csv` | 60 | `GLOTAXB01` "Taxes - Product Tax Details" - the only source carrying a tax RATE |
| `account_move_open_customer.csv` | 189 | `FINDUEU04` Trade Receivables Payables Register - open items with outstanding balances |
| `res_partner.csv` | 288 | `khcustomer` + `khsupplier` (plain CRUD), with address/tax/bank children joined on `ParentObjectID[:32]` |
| `res_partner_contact.csv` | 36 | `khcustomer/RelationshipCollection`, keyed by `InternalID1`/`InternalID2` |
| `product_template.csv` | 3065 | All of `vmumaterial`'s real per-material entities; `standard_price` from `vmumaterialvaluationdata` (994/3058 priced) |
| `stock_quant_adjustment.csv` | 1805 | `SCMINBU03` Inventory Balance, grouped material x logistics area x site |
| `res_bank.csv` | 10 | `khhousebankaccount/BankDirectoryEntryCollection` - the real bank directory |
| `stock_location.csv` | 19 | 1 site from `khlocation/LocationCollection` (was 4 sites earlier in this project; confirmed live on 2026-09-25 the tenant now only has 1, "Danlaw Technologies India Limited" - a real tenant-side change, not a pipeline bug) plus its 18 storage areas from `LogisticsAreaCollection` |
| `purchase_order.csv` / `_line.csv` | 655 / 1861 | `khpurchaseorder`; partner via type-aware party resolution (91% valid) |
| `product_supplierinfo.csv` | 34 | `vmumaterial/SupplierInformationCollection` - supplier part numbers and lead times |
| `product_pricelist.csv` / `_item_discount.csv` | 226 / 1109 | `khsalesorder`, resolved via `_resolve_order_to_arrangement()` to each order's real SAP Sales Arrangement (Customer + SalesOrg + DistributionChannel business key) - not one synthetic catch-all pricelist. 61/226 pricelists carry real product/price lines; the remaining 8 unresolvable orders fall into a small "SAP List Prices - Unmatched Sales Arrangement" fallback |

Odoo CSV conventions: every row's `id` is an external ID (`sap_bp_<CBP_UUID>`,
`sap_bank_<name>`); relation columns use Odoo's `field/id` syntax (`partner_id/id`,
`bank_id/id`); countries use `base.<lowercase-iso2>` external IDs.

### Reading analytics numbers: C* is raw, F*/K* is formatted

A trap worth remembering. In an analytics report the `C*` dimension fields come back as raw
values, but the `F*`/`K*` key figures come back **pre-formatted for display in the tenant's
locale, with the unit or currency appended**. On this tenant that locale is European:

| Field | Value as returned | Means |
|---|---|---|
| `CPRODTAX_RATE_PERCENT` (dimension) | `"18.000000"` | 18% - a plain decimal |
| `FCOUTSTANDING_AMNT` (key figure) | `"1.770,00 USD"` | 1770.00 USD - "." groups thousands |
| `FCENDING_QUANTITY` (key figure) | `"20.000 NOS"` | 20000 units |

Applying the wrong parser to either one silently produces numbers wrong by 1000x. See
`_parse_sap_measure()` and `_tax_rate()` in `src/transform_odoo.py`.

### Known data-quality caveat: OLAP chunk merging can sample

When an analytics entity needs more fields than one `$select` allows, the chunks are merged on a
business key. If that key is not unique within a chunk, the non-key fields of the surviving row
are a *sample* rather than a complete join. `get_entity_set_all_fields()` detects and logs this.

**Resolved for Customers and Vendors (2026-09-18).** That warning used to fire on both (44 keys
vs 50 rows; 229 vs 237). `khcustomer`/`khsupplier` are now imported and `build_res_partner()`
reads those instead - plain CRUD, no chunking, so no sampling. The switch was verified as strictly
additive first: the CRUD services return all 271 external IDs the analytics route produced plus
17 more, so no document reference broke. The analytics rows are still read afterwards purely to
backfill fields the CRUD services leave blank.

The caveat still applies to any *other* analytics entity pulled without an explicit `select`.

### Child entities join on `ParentObjectID[:32]`, not the whole string

On the partner services SAP returns `ParentObjectID` as **two 32-character ObjectIDs
concatenated**; only the first half is the parent. Measured: joining addresses on the full string
matches 0 of 44, on the first 32 characters matches 44 of 44. `_join_by_parent()` in
`src/transform_odoo.py` always slices. (The same trap was hit earlier on `res_users`.)

## Registry validation (`python -m src.validate`)

Cross-checks `src/registry.py`'s 66+1 objects against what's actually in `output_odoo/`. Current
state: **38/67 objects have real data** (`python -m src.validate` is the live count — always
re-run rather than trusting this number, since tenant data can go empty between runs; see the
crm_lead caveat below). 14 objects have a decided Odoo target but no confirmed SAP source yet
(`pending_mapping`); most are waiting on one of the remaining custom services still to be
imported — `SAP_IMPORT_PLAN.md` says exactly which file closes which object. 14 objects
(Engineering/PLM, Maintenance/PM, Quality/QM) are `not_in_bydesign` — standard ByDesign has no
equivalent module.

**Every mandatory object is now closed (2026-09-28) — #40 BOMs and #34 Equipment were the last
two, both closed the same way: real standard ByDesign master data that had simply never been
published as OData on this tenant, exposed by building a brand-new custom service directly
through SAP's self-service **OData Editor** (no Cloud Applications Studio `.xml` import needed).**

**#40 BOMs** — real component data was found via the ByDesign UI ("Bills of Material Variants"),
exposed as `khbomvariant` (Business Object `ProductionBillOfMaterial`, Work Center View
`SCM_PRODNBILLSOFMATERIAL`). See `sap_odata_editor_walkthrough/bom_service_setup/README.md` for
the full build story and `build_boms()`/`build_bom_combined()` in `src/transform_odoo.py`: 400 BOM
headers, 448 product variants, 14,948 component lines, written to `mrp_bom.csv` /
`mrp_bom_line.csv` / the combined deliverable `mrp_bom_with_lines.csv`. Known limitation:
components are attached to the BOM header, not to a specific variant, so a header with more than
one variant (48 of 400) repeats the same component list for each of its variants — a real limit
of what the tenant's data supports, not a guess.

**#34 Equipment** — earlier notes in this file (now superseded) correctly found no standalone
"Equipment" master data object anywhere in this tenant's standard or custom services; ByDesign
genuinely has no Preventive Maintenance module. What actually resolves the sheet requirement is
the real standard Business Object `EquipmentResource` ("Supply Chain Design Master Data" ->
"Resources" in the live UI) — 18 real records (SMT lines, wave soldering, final inspection, etc.),
genuinely typed "Equipment Resource" on this tenant. Exposed as `khequipmentresource`, Work Center
View `SCM_RESOURCES` — found by searching the OData Editor's Work Center View picker for
"Resources"; the picker only matches a view's own display name, never its technical ID or parent
Work Center name, which is why searching "Equipment"/"Design"/"Master Data" all returned zero
results first. See `build_equipment()` in `src/transform_odoo.py`: writes `maintenance_equipment.csv`
(`id`, `name`, `serial_no` — the only fields this entity has real data for; category/technician/
location would all be guesses with no source).

## Team status report (`PROJECT_STATUS.csv`, `python -m src.status_report`)

Auto-generated by `python -m src.main` (Stage 4) after every full run, and re-runnable standalone
against whatever is currently on disk in `output_raw/`/`output_odoo/`. One row per sheet object:
module, category, mandatory Yes/Optional, status (`COMPLETED` / `PENDING - ...` /
`NOT APPLICABLE - ...`), the exact Odoo output file + row count, the exact raw SAP JSON file(s) +
row count(s) that feed it, and the registry note explaining what's known so far. This is the file
to hand to the team — it can't drift from reality since it only reads `src/registry.py` and the
actual files on disk, never asserts anything the pipeline didn't actually produce. Update
`ObjectSpec.raw_sources` in `src/registry.py` (a tuple of `output_raw/` filenames) whenever a new
object gets wired up, so the report keeps tracing status back to real source files.

## How to extend

1. Find a new working service: add a candidate to `test_sap_endpoints.sh` and curl it directly
   (fastest feedback, no Python). Confirm it's the real data, not just a UI codelist (e.g.
   `crm_woc_salesorders.svc` exists but only exposes `codelists`, not actual sales orders).
2. Add `(service, entity_set)` to `SOURCES` in `src/extract_raw.py`. Run it. Inspect the written
   `output_raw/*.json`'s `fields_present` list — that's the real, confirmed column set.
3. Write a transform function in `src/transform_odoo.py` using those confirmed column names,
   register it in `TRANSFORMS`, and pick Odoo columns based on what real data showed up (not
   guesses).
4. Flip the relevant `src/registry.py` `ObjectSpec.status` to `"built"`.
5. Re-run `python -m src.main` and confirm `python -m src.validate` shows the new coverage.

## Known limitations

- **44/67 objects built** (`python -m src.validate` is the live count) — 7 optional objects are
  genuinely `pending_mapping` (confirmed no OData source exists anywhere on this tenant: #3 Fiscal
  Positions, #48 Purchase Requisitions, #0 Currency Exchange Rates, #16 Attachments, #41 Routings
  header - see below; #19 Activities is auth-blocked; #20/21/22 Opportunities and #62 Quotations
  are code-correct but the tenant genuinely has 0 rows right now), and 13 are `not_in_bydesign`
  (Engineering/PLM, Quality, and 5 of Maintenance's 6 objects don't exist as concepts in standard
  ByDesign).
- **A systematic re-check of every `pending_mapping` object against the live catalog closed 5
  more, 2026-09-28**, after a real bug was found in `get_entity_set_all_fields()`
  (`src/sap_client.py`): `BYD_P_*` fields (e.g. `BYD_P_TARCUR`, a currency-conversion parameter
  present on 21 of the tenant's 48 analytics services) weren't being excluded from bulk `$select`
  the way `P_*`/`PARA_*` fields already were, so any report carrying one 400'd on every request -
  fixed, and three reports that had been sitting as "data source confirmed" but never actually
  pulled came through clean: **#13 Journal Entries** (157 real G/L lines, genuinely balanced),
  **#14 Fixed Assets** (3,279 real assets) and **#15 Asset Depreciation** (1,565 current
  depreciation positions), all from `fin_generalledger_analytics.svc`/`fin_fixedassets_analytics
  .svc`. Closing #13 also surfaced 6 real G/L accounts missing from #1 Chart of Accounts (used in
  postings but not covered by the original six analytics sources) - added as a 7th source, now
  219 accounts, zero orphaned account references from Journal Entries. Separately: **#43
  Operations** was closed by finally transforming `khproductionorder/OperationCollection`
  (82,859 raw rows, already extracted, flagged as a gap in #44's own docstring since it was
  written) - deduplicated to the 22 real distinct operation-per-workcenter combinations. And
  **#59 Pricelists' `partner_id/id`** (previously blank - the tenant's `CustomerUUID` didn't
  match `res_partner`'s external IDs) now resolves for 225/226 pricelists via `khcustomer`'s own
  UUID→InternalID lookup (the same pattern already used for products) - which also closes **#61
  Customer Price Lists**, since it's the same file, now genuinely per-customer.
  **#41 Routings** (the routing header, distinct from #43's operations) remains open - its data
  source, "Released Execution Production Model Operation" (`SCM_REPM_OPER`), is a real report
  definition (confirmed in the Design Data Sources catalog) but was never published as a live
  OData service on this tenant, same situation BOMs and Equipment were in before those were
  closed via the OData Editor - closing it needs that same self-service live-service-creation
  work, not more searching.
- **Team feedback caught two real gaps, both fixed 2026-09-28**: `stock_move_history.csv` was
  only reading `khgoodsandserviceacknowledgement` (137 receipts / 238 lines) when a much larger
  source, `khgoodsandactivityconfirmation`'s inventory-movement ledger (147,979 confirmations,
  366,585 lines), was already sitting fully extracted in `output_raw/` and simply never wired
  into a transform — the docstring's claim that this service 500'd was true once but stopped
  being true and nobody re-checked. Now rebuilt from it: 260,682 rows (paired issue+receipt legs
  on the same confirmation+material merged into real two-sided moves; everything else uses
  Odoo's own virtual Suppliers/Customers/Inventory-adjustment location for the unattached side).
  That source's embedded `IdentifiedStockCollection` node initially closed **#30 Lot/Serial
  Numbers** too, previously blocked because the only other candidate source had no product
  reference at all. Then, after the user separately asked where production/expiration dates
  were, `stock_lot.csv` was rebuilt again (2026-09-28): the embedded node only exposes 4 fields
  (no dates at all - confirmed via live `$metadata`), because `IdentifiedStock` is actually its
  own full standalone Business Object with `ExpirationDateTime` and `ProductionDateTime`, never
  exposed as OData until built directly via the OData Editor (service `khbatch`, Work Center
  View `MMA_PHYSICALINVENTORY` - the third object closed this way, after `khbomvariant` and
  `khequipmentresource`). 52,101 real batch records, 51,864 (99.5%) with a real production date,
  1,743 (3.3%) with a real expiration date - most materials on this tenant simply aren't
  expiry-tracked. See `build_stock_moves()`/`build_stock_lots()` in `src/transform_odoo.py`.
- **Seven `*AttachmentFolderCollection`/`AttachmentCollection` entity sets return 400 Bad Request
  no matter what's queried** (confirmed: single field, no field, and a parent-scoped `$filter` all
  fail identically) — see "Entity sets that exist but can't be queried at all" in
  `SAP_IMPORT_PLAN.md`. Low priority (document/attachment *metadata*, not any of the 66 sheet
  objects), but worth knowing before assuming every entity set in `$metadata` is actually
  reachable.
- **`RPBPCSCONTB` (contacts)** extracted successfully but is mostly empty on this tenant (no
  contact persons linked to most accounts) — not yet transformed into an Odoo file since there's
  little real data to map.
- **Address data** uses one address per partner (main address); ByDesign supports multiple.
- **Large transactional extracts**, once mapped, should be bounded with `SAP_DATE_FROM`/
  `SAP_DATE_TO` in `.env` to avoid pulling a system's entire history in one run.

## Running

```bash
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
# .env already has real, working SAP credentials
./test_sap_endpoints.sh          # curl-only sanity check against the real tenant
python -m src.main                # full pipeline: extract_raw -> transform_odoo -> validate
python -m src.extract_raw         # Stage 1 only (re-pull from SAP)
python -m src.transform_odoo      # Stage 2 only (re-run mapping against existing output_raw/)
python -m src.validate            # registry coverage report
```
