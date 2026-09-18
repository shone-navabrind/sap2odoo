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

**`python -m src.main`** runs both stages plus a validation pass against the registry, in order.

Why split them: re-running a transform after fixing a mapping bug doesn't require re-hitting SAP
(slow, chunked, rate-sensitive calls); and the raw dumps are themselves a useful audit trail of
exactly what SAP has, independent of any Odoo decisions made on top of them.

## Source system: SAP Business ByDesign, not S/4HANA

Tenant: `my345654.sapbydesign.com`. Real OData path pattern (confirmed working):
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

**36 files, 13,446 rows.** The authoritative, always-current list is `CONSOLIDATED_STATUS.csv`
(one row per SAP API call) and `PROJECT_STATUS.csv` (one row per sheet object) - both regenerated
from disk on every run. Highlights rather than a duplicate of those:

| File | Rows | Built from |
|---|---|---|
| `account_account.csv` | 148 | Chart of accounts, unioned from the six analytics reports that expose `CGLACCT`/`TGLACCT` (see `src/probe_gl_accounts.py`) |
| `account_tax.csv` | 60 | `GLOTAXB01` "Taxes - Product Tax Details" - the only source carrying a tax RATE |
| `account_move_open_customer.csv` | 189 | `FINDUEU04` Trade Receivables Payables Register - open items with outstanding balances |
| `res_partner.csv` | 271 | `RPBUPCSD` (customers) + `RPBUPSPP` (suppliers), deduplicated by `CBP_UUID`, `vat` from `RPBUPATAXNUMBERS` |
| `product_template.csv` | 3058 | All of `vmumaterial`'s real per-material entities; `standard_price` from `vmumaterialvaluationdata` (994/3058 priced) |
| `stock_quant_adjustment.csv` | 1805 | `SCMINBU03` Inventory Balance, grouped material x logistics area x site |
| `stock_location.csv` | 20 | 4 sites from `khlocation/LocationCollection` plus their 16 storage areas from `LogisticsAreaCollection` |
| `purchase_order.csv` / `_line.csv` | 655 / 1861 | `khpurchaseorder`; partner via type-aware party resolution (91% valid) |
| `product_supplierinfo.csv` | 34 | `vmumaterial/SupplierInformationCollection` - supplier part numbers and lead times |

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
are a *sample* rather than a complete join. `get_entity_set_all_fields()` now detects and logs
this. It currently fires on Customers (44 keys vs 50 rows) and Vendors (229 vs 237) - so a
handful of partners may show one of several addresses. The clean fix is to import `khcustomer`
and `khsupplier`, which expose the same data as plain CRUD with no chunking at all
(`SAP_IMPORT_PLAN.md`, priority 1).

## Registry validation (`python -m src.validate`)

Cross-checks `src/registry.py`'s 66+1 objects against what's actually in `output_odoo/`. Current
state: **31/67 objects have real data, including 19 of the 21 mandatory ones.** 22 objects have a
decided Odoo target but no confirmed SAP source yet (`pending_mapping`); most are waiting on one
of the 29 custom services still to be imported — `SAP_IMPORT_PLAN.md` says exactly which file
closes which object. 14 objects (Engineering/PLM, Maintenance/PM, Quality/QM) are
`not_in_bydesign` — standard ByDesign has no equivalent module.

The two mandatory objects still open:
- **#40 BOMs** — searched every one of the 1485 entity sets the tenant publishes AND every one of
  the 609 entity sets across all 47 custom service `.xml` files, for bom / "bill of material" /
  "production model" / recipe / routing / explosion: **zero matches in either**. Importing more
  services cannot produce it. Needs the PBOM data sources exposed via Business Configuration, or
  a non-OData export.
- **#34 Equipment** — ByDesign has no Preventive Maintenance module; equipment is modelled as a
  serialized product instance, not master data. Verified three times.

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

- **Only one SAP service confirmed so far.** 50 of 66 sheet objects are still `pending_mapping`
  because their real ByDesign service names aren't known yet — see "How to extend."
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
