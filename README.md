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

**Current state:** 31 of 67 objects done (**19 of 21 mandatory**), 206,499 records pulled from SAP
across 195 entity sets, 13,446 rows written to 36 Odoo import files. All numbers are regenerated
from live data on every run — see `DATA_MIGRATION_GUIDE.md` §11 to reproduce them yourself.

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

## Import into Odoo

In Odoo: Settings > Technical > Import, or each app's list view > Favorites > Import Records.
Import files from `output_odoo/` — `res_partner.csv` before `res_partner_bank.csv` (it references
partners), `res_bank.csv` before `res_partner_bank.csv` (it references banks).
