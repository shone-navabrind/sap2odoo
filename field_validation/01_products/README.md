# Products / Materials — field-by-field validation (sheet object #57, mandatory)

Methodology: open the real SAP screen (Product and Service Portfolio → Products), list view
and detail view, tab by tab, screenshot every screen, and match each visible field to a real
column already in this project's extracted data. Where a field is visible on screen but not in
our output, check live `$metadata`/the OData Editor to determine whether it's a genuine gap in
what's exposed as OData (fixable) or a value that's simply blank for the sample record (not a
gap). Validated live against product **730511, "Cover Assy with PCB Bajaj"**, 2026-09-28.

## Screenshots (`screenshots/`)

| # | File | What it shows |
|---|---|---|
| 01 | `01_list_view.jpg` | Products list view (Product ID, Description, Category, UoM, List Price) |
| 02 | `02_overview.jpg` | Product Overview (read-only) screen |
| 03-12 | `03_general_tab.jpg` … `12_taxes_tab.jpg` | Standard Edit screen, every tab: General, Purchasing, Logistics, Planning, Availability Confirmation, Sales, Valuation, Taxes |
| 13-16 | `13_viewall_general_tab.jpg` … `16_viewall_sales_tab.jpg` | Same product, **"View All" mode** (top-right button) — a more detailed floorplan that exposes several fields the standard Edit screen hides |

**The "View All" screens are what broke the case open.** The standard Edit screen's General tab
only shows Product ID/Description/Category/UoM. Clicking **View All** revealed five more real
fields sitting directly on the General tab that the standard screen simply doesn't render:
`Identified Stock Type`, `Batch Managed`, `MRP for India`, `HSN Code for India`, `Storage
Location`, `Manufacturer Name`.

## Findings

| Screen field | Status | Detail |
|---|---|---|
| Product ID, Description, Category, Base UoM | ✅ already mapped | `product_template.csv` |
| Purchasing UoM, Purchasing Status, Sales UoM, Item Group, Min Order Qty, Cash Discount, Reference Price Material | ✅ already mapped | via `PurchasingCollection`/`SalesCollection` |
| Valuation Level Type, Company, Business Residence, Valuation Status, Cost (₹184,446.06 confirmed exact) | ✅ already mapped | via `ValuationCollection`/`vmumaterialvaluationdata` |
| Supplier, Supplier Part Number, Supplier Lead Time | ✅ already mapped | `SupplierInformationCollection` |
| Cycle Count, Site, Serial Number Profile | ✅ already mapped | `LogisticsCollection`/`MaterialCollection` |
| **Storage Location** | ✅ **fixed 2026-09-28** | Real field `StorageLocation_KUT` on `MaterialCollection`'s `Common` node — was never selected when `vmumaterial` was first built. Added via the OData Editor. 46/3,058 materials (1.5%) have a real value; the rest are genuinely blank on this tenant. |
| **Manufacturer Name** | ✅ **fixed 2026-09-28** | Same fix, field `ManufacturerName1_KUT`. 5/3,058 materials (0.2%) have a real value. |
| Identified Stock Type | ✅ already mapped | `IdentifiedStockTypeCode` → `tracking` (`lot`/`none`) |
| **Batch Managed** | Not a separate gap | The only "batch"-named field anywhere in `vmumaterial`'s metadata is `BatchDependentIndicator`, which lives on the `QuantityConversionCollection` node (per-unit-of-measure, only 5 rows total on this tenant) — not a material-wide flag. The screen's checkbox is almost certainly a client-side derivation from `IdentifiedStockTypeCode == '01' (Batch)`, which this pipeline already captures as `tracking`. Not a genuine additional data point to extract. |
| **HSN Code for India** | Confirmed absent | Exhaustively checked: searched every property name in the live `vmumaterial` `$metadata` (grep for `hsn`/`india`, case-insensitive) — zero matches. Also confirmed the `Material` BO exposes only 6 child nodes total (`GlobalTradeItemNumber`, `Identification`, `PlanningQuantity`, `ProductCategoryAssignment`, `QuantityCharacteristic`, `QuantityConversion`, checked via the OData Editor's "Select Business Object" → BO Node Name picker) — no India-localization node exists to add. This is a real, structural gap: the field either lives on a different Business Object this project hasn't found yet, or requires a Business Configuration / country-localization extension not reachable via this BO. |
| **MRP for India** | Confirmed absent | Same finding as HSN Code — checked the same way, same conclusion. |

## What changed in the OData service (`vmumaterial`)

Re-opened `vmumaterial` in SAP's self-service OData Editor (Application and User Management →
OData Services → Custom OData Services → vmumaterial → Edit), expanded `Material.Root.Common`,
and checked the two previously-unselected fields `ManufacturerName1` and `StorageLocation`.
Saved — no separate Activate step needed (same pattern as the earlier `khbatch` fix).

**Also fixed as part of this pass**: `khbatch` had silently reverted to `Status: Inactive` after
last session's field-selection edit (a save without a subsequent Activate leaves the service
saved-but-inactive on this tenant). Re-activated it — `khbatch`'s live data was unaffected in the
interim (curl calls during the inactive window still returned the new fields, since OData reads
serve the last-activated version), but this was a real exposure worth closing.

**Also fixed**: `schema_snapshots/vmumaterial.metadata.xml` was a stale captured snapshot from
before these fields existed. `get_entity_fields()` in `src/sap_client.py` prefers a snapshot over
a live call whenever one exists (documented gotcha for the unstable-analytics-`$metadata` case),
which meant the new fields were invisible to `extract_raw.py` even after the OData Editor change
and even though live `$metadata` already had them. Re-captured the snapshot via a plain curl to
`$metadata` — confirmed both fields now appear (`grep` count 1 each) — and re-ran extraction,
which then picked them up correctly (`fields_present` includes both).

## Data location

Raw: `output_raw/vmumaterial__MaterialCollection.json` (fields `ManufacturerName1_KUT`,
`StorageLocation_KUT`). Not mapped into `output_odoo/product_template.csv` — Odoo's core
`product.template` model has no standard field for either (manufacturer name would need the
`product_manufacturer` module; storage location isn't a product-level concept in stock/purchase
apps). Available losslessly in `output_full_csv/entities/vmumaterial__MaterialCollection.csv`
and `output_full_csv/odoo_models/product_template.csv` for anyone who wants them as custom
fields, same pattern as the stock deliverables package.

## Products: closed

Every field visible on the live Products screen (standard tabs + View All) is now either mapped
into `product_template.csv`, confirmed extracted-but-with-no-standard-Odoo-home, or confirmed via
exhaustive BO-node search to not exist anywhere in this tenant's exposed OData at all. Nothing
left to check for this object without new information (e.g. a different BO the user points to for
India HSN/MRP master data).
