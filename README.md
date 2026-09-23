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

**Current state:** 39 of 67 objects done (**19 of 21 mandatory**), 256,137 records pulled from SAP
across 376 entity sets, 14,198 rows written to 46 Odoo import files, plus a full-column export of
all 3,225 SAP columns (see below). 38 of the 47 custom SAP service files are imported —
`SAP_IMPORT_PLAN.md` lists the 9 remaining, the 6 blocked by SAP authorisation, and the 1 broken
on SAP's side. All numbers are regenerated from live data on every run.

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

### Running just one object, or a limited/quick pull

Every stage above (`main`, `extract_raw`, `transform_odoo`) accepts `--only TEXT` to scope the
run to a single business object instead of the whole tenant - match against a service name,
entity set, or transform label, whichever is easiest to remember:

```bash
python -m src.main --only khcustomer            # full pipeline, just Customers
python -m src.main --only res_partner           # same thing, matched by the Odoo transform label
python -m src.extract_raw --only vmumaterial     # Stage 1 only - just re-pull Products from SAP
python -m src.transform_odoo --only product_template   # Stage 2 only - re-map from existing output_raw/
```

`extract_raw` and `main` also accept `--limit N` to cap every entity set at N rows during
extraction - useful for a quick connectivity/shape check against a new or live tenant before
committing to a full multi-hour pull:

```bash
python -m src.main --only khcustomer --limit 50   # pull 50 Customer rows and run the pipeline on them
```

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
