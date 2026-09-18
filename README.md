# sap2odoo

Extracts business data from an SAP Business ByDesign tenant and produces CSV files ready to
import into Odoo.

## 👉 Start here

| If you want to… | Read / open |
|---|---|
| **Understand the whole project** (technical or not) — what's done, which API gives what, sample data, what's left | **[`DATA_MIGRATION_GUIDE.md`](DATA_MIGRATION_GUIDE.md)** |
| See **everything in one table** — every API call, records in/out, status | **`CONSOLIDATED_STATUS.csv`** |
| Track progress against the client's 66-object requirement sheet | `PROJECT_STATUS.csv` |
| Look up **field-by-field** mappings (SAP field → Odoo field) | `SAP_Field_Mapping.xlsx` (one tab per object) |
| Understand the code architecture and SAP quirks | `CLAUDE.md` |

**Current state:** 26 of 67 objects done (16 of 21 mandatory), 59,968 records pulled from SAP,
11,194 rows written to Odoo import files. All numbers are regenerated from live data on every
run — see `DATA_MIGRATION_GUIDE.md` §11 to reproduce them yourself.

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

## Current scope: 6 of 66 objects have real data

This is the current, honest pipeline state, not a failed SAP download. 47 objects are
`pending_mapping`: 46 of those already have a matching `.xml` in
`byd-api-samples-main/Custom OData Services/` — the ByDesign source exists, it just needs to be
imported into the tenant and wired up (same pattern as Purchase Orders and Products below). 14
objects are `not_in_bydesign` (Engineering/Maintenance/Quality modules ByDesign doesn't have).

Confirmed working sources so far: standard OData v1 `costcentre` (Cost Centers), the
`bpm_businesspartnerdata_analytics.svc` analytics service (Customers, Vendors, Banks), and two
custom Cloud Applications Studio services the user imported — `khpurchaseorder` (Purchase
Orders) and `vmumaterial` + `vmumaterialvaluationdata` (Products, with cost pricing).

To find more service names in the ByDesign tenant, use an administrator account and open
**Application and User Management → Communication Arrangements**. Open the arrangement used by
the integration user, then use **Edit → Service URLs** (or the **Outbound Services** tab,
depending on the tenant UI). Export or copy the service URLs that include
`/sap/byd/odata/`. The service URL is authoritative: Analytics services use a named `.svc`, while
standard services can use the `/sap/byd/odata/v1/<service>/$metadata` form. Open that URL to get
its exact entity-set and field names. Do not infer a service name from a report's Data Source ID:
ByDesign generates the published service name independently.

Run `./test_sap_endpoints.sh` after adding a candidate service to verify it with the configured
technical user. Once confirmed, add its service/entity-set pair to `SOURCES`, extract a raw JSON
file, map its actual columns in `transform_odoo.py`, then change that object's registry status to
`built`.

## Import into Odoo

In Odoo: Settings > Technical > Import, or each app's list view > Favorites > Import Records.
Import files from `output_odoo/` — `res_partner.csv` before `res_partner_bank.csv` (it references
partners), `res_bank.csv` before `res_partner_bank.csv` (it references banks).
