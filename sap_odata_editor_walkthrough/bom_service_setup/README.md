# khbomvariant — real BOM data, closed the last mandatory registry gap

**Status: created, all key fields exposed, Active, and verified against the live tenant.**

This closes sheet object **#40 BOMs**, previously documented in `CLAUDE.md`/`src/registry.py` as
"not obtainable over OData from this tenant" after an exhaustive search of all 1485+609 entity
sets across every existing service and custom `.xml` file. That search was correct at the time —
this data was never published as OData. What changed: the user found it live in the ByDesign UI
under Enterprise Search → category **"Bills of Material Variants"**, and separately under
**Supply Chain Design Master Data → Resources** (Equipment Resources, closing #34's supplementary
Resource data too). Both are genuine SAP standard Business Objects that were simply never exposed
as a custom OData service before — so the fix wasn't more guessing, it was creating that service
ourselves via the same self-service OData Editor tool already used for every other `kh*` service.

## What was created

**Service name:** `khbomvariant`
**Base URL:** `https://my346623.sapbydesign.com/sap/byd/odata/cust/v1/khbomvariant/`
**Business Object:** `ProductionBillOfMaterial` (found by searching the OData Editor's BO Name
field — the full name `ProductionBillOfMaterialVariant` gave no results, the shorter
`ProductionBillOfMaterial` did)
**Work Center View:** `SCM_PRODNBILLSOFMATERIAL` ("Production Bills Of Material") — required
before the service can be Activated; found by searching the Work Center View field for
"Production" and picking the entry whose technical ID clearly matched.

### Entity types exposed (5, all Active)

| # | Entity Set | BO Node | What it is | Key fields exposed |
|---|---|---|---|---|
| 1 | `ProductionBillOfMaterialCollection` | Root | BOM header | `ID` (business key), `LogisticsPreparationFunctionalUnitID`/`UUID`, `UUID`, `Description/content` |
| 2 | `ProductionBillOfMaterialVariantCollection` | Variant | One variant of a BOM (e.g. a voltage/config variant) | `ID`, `MaterialUUID` (the variant's own output product), `ObsoleteIndicator`, `Quantity/content`+`unitCode` |
| 3 | `ProductionBillOfMaterialItemGroupCollection` | ItemGroup | A group of component lines within a variant | `ID`, `ItemRequiredIndicator`, `MultipleSelectionAllowedIndicator`, `UUID`, `Key/BillOfMaterialID`, `Key/BillOfMaterialItemGroupID` |
| 4 | `ProductionBillOfMaterialItemGroupItemCollection` | ItemGroupItem | A line-item pointer within a group (thin — no product/qty fields itself) | `ID` only — real component data lives in entity 5 |
| 5 | `ProductionBillOfMaterialItemGroupItemChangeStateCollection` | ItemGroupItemChangeState | **The actual component line: product + quantity + unit + ECO** | `MaterialUUID` (component product), `Quantity`, `unitCode`, `QuantityFixedIndicator`, `DeletedIndicator`, `EngineeringChangeOrderID`/`UUID`, `UUID` |

Entity 5 is the one that matches exactly what the live ByDesign UI screen shows in its "Bill of
Material Variant Overview" line-item table (Line Item Group ID / Line Item ID / Product ID /
Product Description / Quantity / Fixed / ECO ID / Status / Valid From) — see
`06_bom_variant_with_real_components.jpg`. Getting to it required going one level past the naive
guess: `ItemGroupItem` itself is a thin pointer node with no product/quantity fields of its own;
the real data lives on its `ItemGroupItemChangeState` child, which is ByDesign's versioned/
audited holder of the actual line content (found via that node's own field list once expanded in
the OData Editor).

## Verification against the live tenant (not just Activated — actually queried)

```
GET .../ProductionBillOfMaterialCollection/$count                              -> 400
GET .../ProductionBillOfMaterialVariantCollection/$count                       -> 448
GET .../ProductionBillOfMaterialItemGroupCollection/$count                     -> 982
GET .../ProductionBillOfMaterialItemGroupItemChangeStateCollection/$count      -> 12,945
```

Sample component row (real data, `sample_component_data.json` has the full response):

```json
{
  "ObjectID": "00163E5F7A721ED9898C368946036402",
  "DeletedIndicator": false,
  "EngineeringChangeOrderID": "BGMBETTCARD7",
  "MaterialUUID": "00163E5F-7A72-1ED9-88DC-8CE74D157AE0",
  "QuantityFixedIndicator": false,
  "Quantity": "1.00000000000000",
  "unitCode": "ZNO"
}
```

`MaterialUUID` was cross-checked against `output_raw/vmumaterial__MaterialCollection.json` (the
same product master already used everywhere else in this pipeline) and resolves to a real
product: **"PCBA FOR GEAR MOTOR"**. This is the same UUID-matching convention (dashes stripped,
compared case-insensitively) already used for every other SAP entity in this project — no new
join logic needed to wire this into `product_template.csv`.

## Files in this folder

- `khbomvariant_metadata.xml` — the service's live `$metadata` response, captured for reference.
- `sample_component_data.json` — 3 real component rows pulled live, pretty-printed.
- `04_equipment_resources_live_confirmed.jpg` — live Supply Chain Design Master Data → Resources
  screen, confirming the Equipment Resources master data is real and still present (10 rows:
  SMT Line, Wave Soldering, Final Inspection, etc.).
- `05_bom_search_results_live_confirmed.jpg` — Enterprise Search, category "Bills of Material
  Variants", confirming real BOM header records exist and are searchable.
- `06_bom_variant_with_real_components.jpg` — the ByDesign native UI screen for one real BOM
  (`CS90861DOOO`, "DRP MODEL A (3Ph 415 V)"), showing its 5 real component lines with product ID,
  description, and quantity — this is the target shape entity 5 above now serves over OData.
- `07_khbomvariant_editor_initial.jpg` — the OData Editor screen right after re-opening the
  service for this session's field-completeness pass.

## What's NOT done yet (deliberately, per your instruction to confirm before proceeding)

No code changes have been made anywhere in `src/`. `src/registry.py` still shows #40 BOMs as
`pending_mapping`. Nothing has been added to `extract_raw.py`'s `SOURCES`, no transform has been
written, and `output_raw/`/`output_odoo/` have not been touched by this service. That's the next
step once you confirm — it would follow the exact same pattern as every other `kh*` service:
add the 5 `(service, entity_set)` pairs to `SOURCES`, run `extract_raw`, inspect the real columns
in `output_raw/`, write `build_boms()` in `transform_odoo.py` mapping to `mrp.bom` /
`mrp.bom.line`, register it, flip the registry status to `built`, and re-run the full pipeline
with `python -m src.main` so `PROJECT_STATUS.csv`/`CONSOLIDATED_STATUS.csv` reflect the closed
gap.
