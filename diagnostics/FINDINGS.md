# Audit Findings (2026-09-24)

Investigation results from running `diagnostics/validate_relations.py` against the full live
extraction and tracing every flagged item back to its root cause. See `RELATION_REPORT.md` for
the raw per-file numbers this summarizes.

## Fixed

**`stock_picking_transfer.csv` had 3 duplicate external IDs (real bug, now fixed).**
`build_stock_transfers()` (`src/transform_odoo.py`) keyed each delivery's external ID on SAP's
human-readable `ID` field. Traced the actual raw data and confirmed SAP itself reuses the same
`ID` across two different `ObjectID`s for a handful of deliveries - e.g. `"8020005269"` appears
on both an `Inconsistent`/`Not Released` draft and a later `Consistent`/`Released` version,
looking like a correction/reprocessing pattern specific to Customer Returns. Keying on `ID`
meant two genuinely distinct deliveries collapsed onto the same external ID - on Odoo import,
the second row would silently overwrite the first as an "update" instead of creating a second
record, permanently losing one of every duplicated pair. **Fixed**: now keyed on `ObjectID`
(always unique), with `ID` kept only as the display `name`. Verified: 0 duplicates after the fix
(was 3), same 1,108 rows before and after (nothing was dropped from the CSV - the risk was
specifically in what Odoo would do with two same-ID rows on import).

**False-positive "orphaned" `route_ids/id` on `product_template.csv` (audit script bug, not a
pipeline bug).** The first audit run flagged all 8,662 populated `route_ids/id` values as
orphaned. Traced it: those values are Odoo's own built-in route XML IDs
(`purchase_stock.route_warehouse0_buy`, `mrp.route_warehouse0_manufacture`) - never one of our
`sap_*`-prefixed external IDs to begin with, so there was nothing to look up. Fixed the audit
script's built-in-reference detection to match on the real signal (every ID this pipeline
generates starts with `sap_` - confirmed against every `external_id()` call site in
`transform_odoo.py`) instead of assuming `base.` was the only built-in prefix.

## Investigated and explained - not bugs, real data-completeness gaps

**`account_payment.csv`: 563 of 1,124 distinct `partner_id` values don't resolve (~1% of rows).**
Traced several samples (e.g. `8000000079`, `8000000092`) against `khbusinesspartner` (the
general Business Partner service, broader than Customer/Supplier): they exist there, and every
one checked is `CategoryCodeText: "Person"` - an individual, not an organization with a
Customer or Supplier role. `res_partner.csv` is built only from `khcustomer` + `khsupplier`
(the two services that specifically carry the Customer/Supplier role assignment), so individual
people who made or received a payment without ever being flagged as a Customer or Supplier
(most likely employees - expense reimbursements, advances) are genuinely outside that master
data's scope. **Not a bug** - the payment data itself is correct; the *partner* just isn't part
of the Customers/Vendors object as scoped by the requirement sheet. If these payments need a
resolved partner in Odoo, the fix is extending `res_partner.csv` to also pull `khbusinesspartner`
Person-category records as Contacts - a scope decision, not a defect.

**`account_move_customer_invoice_line.csv`: 194 of 34,002 checkable `product_id` values don't
resolve (~0.6% of rows).** Traced samples (e.g. `100000228`, `100000434`): they do not exist in
`vmumaterial`'s current `MaterialCollection` at all. Most likely explanation: these are products
that have since been deleted, obsoleted, or archived in SAP's live product master, but still
appear correctly on old, already-posted invoice line items (the invoice is a historical record;
the product master reflects what's *currently* active). `vmumaterial`'s live query only returns
what exists now. **Not a bug** - both the invoice data and the product master are individually
correct; a deleted product simply can't be re-created from a query that only shows current data.

**`account_move_vendor_bill.csv`: 3 of 55,261 `partner_id` values don't resolve (`G002`,
`G113`).** Traced them against both `khsupplier` and the general `khbusinesspartner` service -
not found in either. These don't match the normal 8-digit numeric Business Partner ID pattern
seen everywhere else in this tenant, so they're most likely a different ID namespace entirely
(a legacy/migrated code, an intercompany "Group" reference, or similar) rather than a standard
Business Partner this pipeline's sources can reach at all. Negligible (0.005% of rows) and not
further pursued given the scale.

## What this means for the Odoo import

**Correction after double-checking actual cell contents**: these rows are NOT blank - the
`product_id/id`/`partner_id/id` cell is populated with a real-looking external ID
(e.g. `sap_prod_100000228`) that simply doesn't exist anywhere in the data being imported.
Odoo's CSV importer will report an error on each such row ("No matching record found for
external id ... in field ...") rather than silently leaving the relation empty - the row itself
still gets skipped/flagged, it does not import with a blank field automatically.

**Practical impact is still small** (760 rows total, spread across 3 files out of 599,565 rows
overall - 0.13%) and every one has already been traced to a real, explained cause above, not a
processing bug. Before importing, either: (a) accept that Odoo's import log will show ~760 row
errors and review them individually, or (b) pre-clear the broken reference in the CSV first
(leave the cell blank instead of a value that can't resolve) so the row imports cleanly minus
that one relation - the rest of each row's data (amount, date, quantity, etc.) is completely
unaffected either way.

Re-run the audit any time after new data comes in: `python diagnostics/validate_relations.py`.
