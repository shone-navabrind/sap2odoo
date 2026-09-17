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

**What's actually needed:** the live OData service names, which live in a different ByDesign
screen: **Application and User Management → Communication Arrangements**. Open the arrangement
used by the integration user, then inspect **Edit → Service URLs** (or **Outbound Services**, in
some tenant UIs) and copy/export URLs containing `/sap/byd/odata/`. Analytics services use a
published `.svc` name; standard services can instead use `/sap/byd/odata/v1/<service>/$metadata`.
The URL shown by the tenant is authoritative. Open its `$metadata` URL to find the exact entity-set
names. This is the authoritative discovery path; the Design Data Sources catalog is not an OData
publication catalog.

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
flat catalog). There is no public catalog of these names; each has to be found and curl-verified
(`test_sap_endpoints.sh`).

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

Fix: `schema_snapshots/bpm_businesspartnerdata_analytics.svc.metadata.xml` is a captured-known-good
copy of the full metadata (from the user's original working curl). `get_entity_fields()` in
`src/sap_client.py` prefers this snapshot over a live call when one exists for a service. The
underlying data via named `$select` fields still works fine even when `$metadata` has collapsed —
only the metadata *listing* is unstable, not the actual data access.

## Odoo output today (`output_odoo/`)

| File | Rows | Built from |
|---|---|---|
| `res_partner.csv` | 271 | `RPBUPCSD` (customers) + `RPBUPSPP` (suppliers), deduplicated by `CBP_UUID`, `vat` from `RPBUPATAXNUMBERS` |
| `res_bank.csv` | 8 | Bank fields inside `RPBUPCSD`/`RPBUPSPP` |
| `res_partner_bank.csv` | 90 | Same source, linking partners to their bank accounts |
| `account_analytic_plan.csv` | 1 | Synthetic "SAP Cost Centers" plan (Odoo requires a plan for every analytic account) |
| `account_analytic_account_cc.csv` | 14 | Standard OData v1 `costcentre` service, `CostCentreCollection` |
| `purchase_order.csv` | 655 | `khpurchaseorder` custom service, `PurchaseOrderCollection` + `SupplierCollection` for the partner link |
| `purchase_order_line.csv` | 1861 | `khpurchaseorder`'s `ItemCollection`; `product_id/id` resolves for 1586/1595 lines now that Products is wired up |
| `product_template.csv` | 3058 | `vmumaterial`'s `MaterialCollection`, `standard_price` enriched from `vmumaterialvaluationdata`'s latest `ValuationPriceCollection` row per material (994/3058 have a price on this tenant) |

Odoo CSV conventions: every row's `id` is an external ID (`sap_bp_<CBP_UUID>`,
`sap_bank_<name>`); relation columns use Odoo's `field/id` syntax (`partner_id/id`,
`bank_id/id`); countries use `base.<lowercase-iso2>` external IDs.

## Registry validation (`python -m src.validate`)

Cross-checks `src/registry.py`'s 66+1 objects against what's actually in `output_odoo/`. Current
state: **6/67 objects have real data** (Customers, Vendors, Banks, Cost Centers, Purchase
Orders, Products). 47 objects have a decided Odoo target (model + filename) but no confirmed SAP
source yet (`pending_mapping`) — 46 of those now have a matching `.xml` file sitting in
`byd-api-samples-main/Custom OData Services/`, ready to import and wire up the same way Purchase
Orders/Products were. 14 objects (Engineering/PLM, Maintenance/PM, Quality/QM) are flagged
`not_in_bydesign` — standard ByDesign has no equivalent module, so these need the user to confirm
whether that data lives in this SAP system at all before any extraction logic is written for them.

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
