# Customers / Vendors (`res_partner.csv`) — field-by-field validation (sheet objects #17 & #46, both mandatory)

Triggered by the user pasting a list of fields visible on the live SAP Customer/Vendor screens
but missing from `res_partner.csv`. Same methodology as `field_validation/01_products/`: open
the real screens, screenshot every tab, match each field to either real (already-extracted-but-
unmapped) data or a confirmed genuine gap.

## Screenshots (`screenshots/`)

| # | File | What it shows |
|---|---|---|
| 01 | `01_accounts_list.jpg` | Account Management → Accounts list view |
| 02 | `02_customer_overview.jpg` | Corporate Account Overview (read-only) — Sales Data already shows Incoterms/Payment Terms/Incoterms Location |
| 03-05 | `03_customer_general_tab.jpg` … `05_customer_financial_data_tab.jpg` | Customer Edit screen: General (Additional Name, Prospect, Industry), Sales Data (Document Blocks: Order/Delivery/Invoice Block), Financial Data |
| 06-08 | `06_supplier_overview.jpg` … `08_supplier_purchasing_tab.jpg` | Supplier Edit screen: General (**every** pasted field visible here — Bidder, Warehouse Provider, Freight Forwarder, Trade Name, Minimum PO Value, Certified According To/Valid To, Non-Company), Purchasing (Payment Terms, Incoterms, Incoterms Location, PO Currency, ERS Invoice Number Prefix, Calendar Year as Suffix, Restart Doc ID Each Cal Year) |

**Key finding**: almost every field the user listed as "Customer" is actually visible on the
**Supplier** edit screen, not the Customer one (`06`-`08`) — the pasted table's Type column looks
mislabeled for several rows. Cross-checked directly against the live tenant.

## Findings

| Screen field | Status | Detail |
|---|---|---|
| **Payment Terms** (Supplier) | ✅ **fixed 2026-09-28** | `khsupplier/PurchasingDataCollection.PaymentTermsCodeText` — real data existed in `output_raw/` already, just never joined back to a supplier (see fix below). 2,000/2,147 suppliers (93%) have a real value. |
| **Incoterms** / **Incoterms Location** (Supplier) | ✅ **fixed 2026-09-28** | Same source, `IncotermsCodeText`/`IncotermsLocationName`. 1,909/2,147 (89%). |
| **Purchase Order Currency** (Supplier) | ✅ **fixed 2026-09-28** | Same source, `PurchaseOrderCurrencyCodeText`. 2,120/2,147 (99%). |
| **Industry** (Customer & Supplier) | ✅ **fixed 2026-09-28** | `IndustrialSectorCodeText` — already a field on both `CustomerCollection`/`SupplierCollection`, just never read by the transform. |
| **Order Block Reason / Delivery Block / Invoice Block** (Customer) | ✅ mapped, genuinely blank | `OrderBlockingReasonCodeText`/`FulfilmentBlockingReasonCodeText`/`InvoicingBlockingReasonCodeText` — already-extracted fields, now mapped. Checked directly: all 259 customers on this tenant have these blank (nobody is currently blocked) — real data, not a join failure. |
| **Bidder** / **Warehouse Provider** / **Freight Forwarder** (Supplier) | ✅ **fixed 2026-09-28** | These are Business Partner *roles*, not simple fields. `khsupplier/RoleCollection` (already extracted) carries one row per role; codes confirmed live against `RoleRoleCodeCollection`: `BBP001`=Bidder, `SCM002`=Warehouse Provider, `CRMS04`=Freight Forwarder. Derived as booleans per supplier. |
| **Additional Name**, **Trade Name**, **Non-Company**, **Minimum Purchase Order Value**, **Certified According To/Valid To**, **ERS Invoice Number Prefix**, **Calendar Year as Suffix**, **Restart Doc. ID Each Cal. Year** | Confirmed absent | Visible on the live Supplier screen, but exhaustively checked against `khsupplier`'s full Business Object field tree via the OData Editor — none of these exist as a property anywhere on the `Supplier` BO. A real structural gap (possibly a separate BO or a Business Configuration setting), not a missed selection. |
| **Payment Terms / Incoterms / Incoterms Location** (Customer specifically) | Confirmed absent from `khcustomer` | The Customer Overview screen (`02`) shows these under "Sales Data" — but that data lives on a separate CRM **Account** object (`crm.acm`), not on `khcustomer`'s own Business Object (`khcustomer`'s metadata has no `SalesData`/`PurchasingData`-equivalent node — checked). Would need a new custom OData service built against the Account BO to close, same pattern as `khbomvariant`/`khbatch`. |
| **Account ID / Account Name at Supplier** | Partially found | The Supplier Purchasing tab's "Customer ID at Supplier" field (`BuyerPartySellerID`) is now mapped, but is blank for all suppliers on this tenant (0/2,147 populated) — real absence, not a bug. |

## What changed in code (no OData Editor changes needed this time)

**`khsupplier/SupplierCollection` extraction now uses `$expand=PurchasingData`**
(`src/extract_raw.py`). `PurchasingDataCollection`'s own rows carry only `ObjectID` — no
`ParentObjectID` back to the supplier that owns them (confirmed: none of 2,133 rows' ObjectIDs
share even a partial prefix with any Supplier ObjectID) — so it was extracted but effectively
orphaned data. Same situation as `khproductionorder`/`MainProductOutput`, already solved there
with `$expand`; applied the identical fix here. `SupplierCollection` now returns
`PurchasingData.PaymentTermsCodeText` etc. inline, flattened automatically by
`sap_client._flatten_expanded()`.

**`build_res_partner()` (`src/transform_odoo.py`) now reads three sources it wasn't reading
before**: the newly-joined `PurchasingData.*` fields, `khsupplier/RoleCollection` (for the role
booleans), and fields that were already present on `CustomerCollection`/`SupplierCollection` but
simply never mapped (`IndustrialSectorCodeText`, the three blocking-reason fields). Also fixed a
real merge bug caught while adding this: for a business partner that's both customer and
supplier, the old "fill only if the existing field is blank" merge would have silently kept the
customer-side row's `is_bidder = "False"` over the supplier-side row's real `"True"`, since
`"False"` is a non-empty string. Booleans now OR together on merge instead.

## Result

`res_partner.csv`: 11 new columns (`industry`, `payment_terms`, `incoterms`,
`incoterms_location`, `purchase_order_currency`, `order_block_reason`, `delivery_block`,
`invoice_block`, `is_bidder`, `is_warehouse_provider`, `is_freight_forwarder`), all backed by
real SAP data, most of it previously extracted-but-unused rather than newly pulled. 2,404 total
partner rows (up from the previously-documented 288 — the tenant's supplier list itself grew
substantially since that number was last checked, confirmed via `khsupplier/SupplierCollection`
now returning 2,147 rows vs. 250 before).
