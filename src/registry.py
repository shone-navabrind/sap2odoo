"""
Master registry of every data object from the Danlaw SAP Data Migration sheet (66 objects),
plus additional relational data identified as needed but not on that sheet (e.g. currency
exchange rates, needed because Odoo ships base currencies/countries but not historical FX
rates for FY20-21 through FY25-26).

status values:
  "built"              - a raw source is confirmed (see src/extract_raw.py SOURCES) and
                         src/transform_odoo.py produces real data for this object's Odoo file
  "pending_mapping"    - Odoo-side target is defined below; SAP-side service/entity name is
                         NOT YET CONFIRMED against this tenant's real OData catalog (ByDesign
                         has no public API catalog like S/4HANA's API Business Hub - confirm
                         new ones via test_sap_endpoints.sh, add to extract_raw.py's SOURCES,
                         then write a transform in transform_odoo.py)
  "not_in_bydesign"    - object has no equivalent in standard SAP Business ByDesign; likely
                         requires a custom BO/extension on the tenant, or simply isn't
                         available from this source system - flag to the user, don't guess

Run `python -m src.validate` to cross-check this registry against what output_odoo/ actually
contains right now.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ObjectSpec:
    sheet_no: int  # 0 = not on the original sheet (added for completeness)
    module: str
    category: str  # Master / Transaction
    name: str  # name as it appears on the Danlaw sheet
    mandatory: bool
    odoo_model: str
    filename_base: str  # without numeric prefix, e.g. "res_partner"
    status: str
    note: str = ""
    raw_sources: tuple = ()  # output_raw/ filenames feeding this object, for status reporting


REGISTRY = [
    # --- Accounts ---
    ObjectSpec(1, "Accounts", "Master", "Chart of Accounts", True, "account.account", "account_account", "built",
               "REAL DATA: the G/L Account Master report (FINGLAU17) is live but declares a mandatory Chart of Accounts "
               "variable with no default and no readable value list, so it returns 0 rows. Built instead by unioning the "
               "six analytics reports that expose CGLACCT/TGLACCT without a blocking variable - see src/probe_gl_accounts.py, "
               "which tested all 60 candidate reports. Covers every G/L account carrying postings; an account configured in "
               "ByDesign but never posted to would not appear. account_type is derived from the account number band, each "
               "band confirmed against the real account names (see ACCOUNT_TYPE_BANDS in src/transform_odoo.py). Added "
               "2026-09-28: a 7th source, RPFINGLAU03_Q0001QueryResults (Journal Entries, #13's source) - 6 real "
               "accounts used in journal postings weren't covered by the original six reports; adding this source "
               "closed the gap, confirmed zero orphaned account references from #13 afterward. 219 accounts total.",
               raw_sources=("fin_costandrevenue_analytics.svc__RPFINCACU04_Q0002QueryResults.json",
                            "fin_audit_analytics.svc__RPFINGLAU02_Q0002QueryResults.json",
                            "fin_generalledger_analytics.svc__RPFINFXAU05_Q0001QueryResults.json",
                            "fin_generalledger_analytics.svc__RPFINFCDU02_Q0001QueryResults.json",
                            "fin_audit_analytics.svc__RPFININVU03_Q0001QueryResults.json",
                            "fin_audit_analytics.svc__RPFINGLAU02_Q0003QueryResults.json",
                            "fin_generalledger_analytics.svc__RPFINGLAU03_Q0001QueryResults.json")),
    ObjectSpec(2, "Accounts", "Master", "Taxes", True, "account.tax", "account_tax", "built",
               "REAL DATA: 'Taxes - Product Tax Details' (GLOTAXB01) on fin_taxmanagement_analytics.svc - the only source on "
               "this tenant carrying a tax RATE. Every custom service exposes tax codes on documents but never the percentage "
               "behind them. Real Indian GST rates confirmed (18%, 9%, 28%, 2.5%, 0.075%) across Central/State/Interstate GST, "
               "TCS, VAT, customs duty and cess. These are the taxes actually applied on documents, not the full configured "
               "tax table - ByDesign publishes no tax-code master over OData. type_tax_use defaults to 'sale' and needs review.",
               raw_sources=("fin_taxmanagement_analytics.svc__RPGLOTAXB01_Q0001QueryResults.json",)),
    ObjectSpec(3, "Accounts", "Master", "Fiscal Positions", False, "account.fiscal.position", "account_fiscal_position", "pending_mapping", "No matching data source found"),
    ObjectSpec(4, "Accounts", "Master", "Payment Terms", True, "account.payment.term", "account_payment_term", "built", "REAL DATA: found inside CashDiscountTermsCollection on the already-imported khcustomerinvoice/khsupplierinvoice services (no new upload needed) - deduplicated code list",
               raw_sources=("khcustomerinvoice__CashDiscountTermsCollection.json", "khsupplierinvoice__CashDiscountTermsCollection.json")),
    ObjectSpec(5, "Accounts", "Master", "Banks", True, "res.bank", "res_bank", "built",
               "REAL DATA: khhousebankaccount/BankDirectoryEntryCollection - the actual bank directory (10 banks with "
               "country, city and bank standard ID), replacing the previous approach of scraping bank NAMES out of "
               "business-partner records. Partner bank accounts (88) come from khcustomer/khsupplier BankDetailsCollection.",
               raw_sources=("khhousebankaccount__BankDirectoryEntryCollection.json", "khcustomer__BankDetailsCollection.json", "khsupplier__BankDetailsCollection.json")),
    ObjectSpec(6, "Accounts", "Master", "Cost Centers", False, "account.analytic.account", "account_analytic_account_cc", "built", "14 records from standard OData v1 service costcentre, entity set CostCentreCollection. Imported as analytic accounts under the SAP Cost Centers analytic plan.",
               raw_sources=("costcentre__CostCentreCollection.json",)),
    ObjectSpec(7, "Accounts", "Master", "Analytic Accounts", False, "account.analytic.account", "account_analytic_account", "built",
               "REAL DATA: khprofitcentre - 19 profit centres, ByDesign's second analytic dimension alongside the cost "
               "centres already built as #6. They share the one synthetic analytic plan Odoo requires. The readable name "
               "comes from a date-versioned NameCollection, not from the profit centre record itself, which carries only "
               "ID/ObjectID/UUID. khproject would add project-based analytic accounts too, but it is authorisation-blocked "
               "(RBAM_ERROR) - see SAP_IMPORT_PLAN.md.",
               raw_sources=("khprofitcentre__ProfitCentreCollection.json", "khprofitcentre__NameCollection.json")),
    ObjectSpec(8, "Accounts", "Transaction", "Open Customer Invoices", True, "account.move", "account_move_open_customer", "built",
               "REAL DATA: the 'dunning/open-item report not yet found' called for by the earlier investigation is ByDesign's "
               "Trade Receivables Payables Register (FINDUEU04) on fin_receivablesar_analytics.svc - 189 still-unsettled "
               "invoices with the invoice number, customer and outstanding balance. This replaces the abandoned approach of "
               "deriving payment status from khcustomerinvoice, which has no payment-status field at all and whose ID-to-payment "
               "join only ever reached 7% (sequential-number coincidence, not a real link). Note amount_total here is the "
               "OUTSTANDING balance, not the original invoice total - that is what an opening-balance import needs.",
               raw_sources=("fin_receivablesar_analytics.svc__RPFINDUEU04_Q0007QueryResults.json",)),
    ObjectSpec(9, "Accounts", "Transaction", "Open Vendor Bills", True, "account.move", "account_move_open_vendor", "built", "REAL DATA: same khsupplierinvoice source as #51, filtered on LifeCycleStatusCode (confirmed real/populated: excludes Paid=12, Canceled=9, Voided=7) - 265/458 open",
               raw_sources=("khsupplierinvoice__SupplierInvoiceCollection.json", "khsupplierinvoice__ItemCollection.json", "khsupplierinvoice__SellerPartyCollection.json")),
    ObjectSpec(10, "Accounts", "Transaction", "Customer Payments", False, "account.payment", "account_payment_customer", "built", "REAL DATA: khpayment custom service, 59,493 payments total - customer vs vendor split via partner_type column (looked up against res_partner's customer_rank/supplier_rank), combined with #11 in one account_payment.csv. Fixed 2026-09-28, caught by a team cross-validation pass: partner_id/id used to be emitted for every row with a real BusinessPartnerID even when that ID wasn't in res_partner.csv (563/59,493 rows, 0.9% - a real Business Partner per khbusinesspartner, but neither a customer nor supplier) - a broken external-id reference Odoo would reject. Now left blank in both that case and the 5,133/59,493 (8.6%) rows with no BusinessPartnerID at all on SAP's side; 0 broken references confirmed.",
               raw_sources=("khpayment__PaymentCollection.json",)),
    ObjectSpec(11, "Accounts", "Transaction", "Vendor Payments", False, "account.payment", "account_payment_vendor", "built", "REAL DATA: same khpayment source and account_payment.csv as #10, split by partner_type column",
               raw_sources=("khpayment__PaymentCollection.json",)),
    ObjectSpec(12, "Accounts", "Transaction", "Bank Statements", False, "account.bank.statement", "account_bank_statement", "built", "REAL DATA: khhousebankstatement custom service, 87 statements",
               raw_sources=("khhousebankstatement__HouseBankStatementCollection.json",)),
    ObjectSpec(13, "Accounts", "Transaction", "Journal Entries", False, "account.move", "account_move_journal", "built",
               "REAL DATA, closed 2026-09-28: fin_generalledger_analytics.svc/RPFINGLAU03_Q0001QueryResults ('Journal "
               "Entries'). Was 'data source confirmed' but never actually pulled - the report 400'd on every request "
               "because of a real bug in get_entity_set_all_fields() (BYD_P_* parameter fields, e.g. BYD_P_TARCUR, "
               "weren't being excluded from bulk $select the way P_*/PARA_* fields already were - see src/sap_client.py). "
               "157 real G/L line items, genuinely balanced (net debit-credit ~0 across the whole file, confirmed). "
               "KCBALANCE_CURRCOMP is signed (positive=debit, negative=credit), so line_ids/debit and line_ids/credit "
               "are derived directly from its sign, not guessed.",
               raw_sources=("fin_generalledger_analytics.svc__RPFINGLAU03_Q0001QueryResults.json",)),
    ObjectSpec(14, "Accounts", "Transaction", "Fixed Assets", False, "account.asset", "account_asset", "built",
               "REAL DATA, closed 2026-09-28: fin_fixedassets_analytics.svc/RPFINFXAU04_Q0001QueryResults ('Fixed "
               "Assets - Master Data'), same BYD_P_* bug fix as #13. 3,279 real fixed assets (FACTORY LAND, LAPTOP, "
               "FEEDER F1-12, etc.). Fields extended 2026-09-30 (caught in a pre-import team validation pass): the "
               "file used to write only id/name even though asset_class/status (CASSETCLASS/TASSETCLASS, "
               "CLC_STAT/TLC_STAT, from this same RPFINFXAU04 report) and acquisition_cost/accumulated_depreciation/"
               "net_book_value (from RPFINFXAU01, see #15) were already sitting unused in output_raw/. 1,714/3,279 "
               "assets (52%) have no cost/depreciation figures - a genuine data gap, matches RPFINFXAU01's own row "
               "count of 1,565 exactly, not a join bug.",
               raw_sources=("fin_fixedassets_analytics.svc__RPFINFXAU04_Q0001QueryResults.json",
                             "fin_fixedassets_analytics.svc__RPFINFXAU01_Q0001QueryResults.json")),
    ObjectSpec(15, "Accounts", "Transaction", "Asset Depreciation", False, "account.asset.depreciation.line", "account_asset_depreciation_line", "built",
               "REAL DATA, closed 2026-09-28: fin_fixedassets_analytics.svc/RPFINFXAU01_Q0001QueryResults ('Fixed "
               "Assets Values'), same fix as #13/#14. 1,565 rows - each asset's CURRENT accumulated depreciation "
               "position (KCPOSTED_DEPR), not a history of individual depreciation postings - no period dimension was "
               "selected, so this is one row per asset, not per-period. Called out explicitly rather than implying "
               "more granularity than the source actually gives.",
               raw_sources=("fin_fixedassets_analytics.svc__RPFINFXAU01_Q0001QueryResults.json",)),

    # --- Common ---
    ObjectSpec(16, "Common", "Master", "Attachments/Documents", False, "ir.attachment", "ir_attachment", "pending_mapping", "SAP attachments are typically binary content on GOS/DMS - needs a per-record download pass, not a flat OData select"),

    # --- CRM ---
    ObjectSpec(17, "CRM", "Master", "Customers", True, "res.partner", "res_partner", "built",
               "REAL DATA: khcustomer custom service (plain CRUD) - full address, tax number, bank "
               "detail and contact-person data. Replaced the bpm_businesspartnerdata_analytics.svc report, which had to "
               "be fetched in field-chunks and merged on a key SAP does not guarantee unique (44 keys vs 50 rows), so "
               "some fields were an arbitrary sample. Verified before switching: the CRUD source returns a strict "
               "superset - all 271 previous external IDs plus 17 more - so no document reference broke. Contact persons "
               "now populate res_partner_contact.csv from RelationshipCollection; they were empty before. Extended "
               "2026-09-28 after the user compared res_partner.csv against the live Customer/Vendor screens: added "
               "industry, order_block_reason/delivery_block/invoice_block (all already-extracted CustomerCollection "
               "fields, just never mapped), plus payment_terms/incoterms/incoterms_location/purchase_order_currency "
               "and is_bidder/is_warehouse_provider/is_freight_forwarder on the supplier side - the latter two needed "
               "real fixes: khsupplier/SupplierCollection now pulls with $expand=PurchasingData (PurchasingDataCollection "
               "carries no ParentObjectID, same un-joinable-child situation as khproductionorder/MainProductOutput), "
               "and the role booleans are derived from khsupplier/RoleCollection (already extracted, RoleCode "
               "BBP001/SCM002/CRMS04 = Bidder/Warehouse Provider/Freight Forwarder, confirmed against "
               "RoleRoleCodeCollection). See field_validation/02_customers_vendors/README.md. Confirmed NOT available "
               "anywhere on this tenant: Additional Name, Trade Name, Non-Company, Minimum Purchase Order Value, "
               "Certified According To/Valid To, ERS Invoice Number Prefix, Calendar Year as Suffix, Restart Doc ID "
               "Each Cal Year (checked exhaustively via the OData Editor's full BO field tree, not just $metadata); "
               "Payment Terms/Incoterms on the Customer side specifically live on a separate CRM Account object this "
               "pipeline doesn't read.",
               raw_sources=("khcustomer__CustomerCollection.json", "khcustomer__PostalAddressCollection.json", "khcustomer__TaxNumberCollection.json", "khcustomer__RelationshipCollection.json", "khsupplier__RoleCollection.json")),
    ObjectSpec(18, "CRM", "Master", "Salespersons", True, "res.users", "res_users_salesperson", "built", "REAL DATA: khemployee custom service, 99 employees (output file is res_users.csv - see FILENAME_OVERRIDES)",
               raw_sources=("khemployee__EmployeeCollection.json", "khemployee__WorkplaceAddressCollection.json")),
    ObjectSpec(19, "CRM", "Master", "Activities", False, "mail.activity", "mail_activity", "pending_mapping",
               "BLOCKED BY AUTHORISATION, not by a missing service: khlead is imported and live, but every one of its 19 "
               "entity sets returns RBAM_ERROR (Not Authorized) for the SDK user. Granting that user the Leads work center "
               "makes it readable - an SAP role change, not another import."),
    ObjectSpec(20, "CRM", "Transaction", "Opportunities", False, "crm.lead", "crm_lead", "built", "khopportunity custom service - transform code is real and correct, but confirmed live on "
               "2026-09-26 (direct curl, $inlinecount=allpages) that OpportunityCollection currently has 0 rows on this "
               "tenant - down from the 15 this note previously described. Not a pipeline bug: crm_lead.csv/_open/_closed "
               "are correctly empty because the source data is currently empty. Re-check live if this matters before "
               "go-live; the code will pick up real rows automatically the moment the tenant has any.",
               raw_sources=("khopportunity__OpportunityCollection.json",)),
    ObjectSpec(21, "CRM", "Transaction", "Open Opportunities", True, "crm.lead", "crm_lead_open", "built", "Same khopportunity source and same currently-empty-tenant caveat as #20 (filter logic: LifeCycleStatusCode "
               "Open=1/In Process=2, confirmed correct in code, just has nothing to filter right now).",
               raw_sources=("khopportunity__OpportunityCollection.json",)),
    ObjectSpec(22, "CRM", "Transaction", "Closed Opportunities", False, "crm.lead", "crm_lead_closed", "built", "Same khopportunity source and same currently-empty-tenant caveat as #20 (filter logic: LifeCycleStatusCode "
               "Won=4/Lost=5, confirmed correct in code, just has nothing to filter right now).",
               raw_sources=("khopportunity__OpportunityCollection.json",)),

    # --- Engineering ---
    ObjectSpec(23, "Engineering", "Master", "Engineering Items", False, "product.template", "product_template_engineering", "not_in_bydesign", "PLM engineering items are not part of standard ByDesign; confirm source system with user"),
    ObjectSpec(24, "Engineering", "Master", "Document Revisions", False, "product.document", "product_document_revision", "not_in_bydesign"),
    ObjectSpec(25, "Engineering", "Master", "ECO", False, "mrp.eco", "mrp_eco", "not_in_bydesign", "Engineering Change Orders require the PLM module - not standard in ByDesign"),
    ObjectSpec(26, "Engineering", "Master", "Drawings", False, "ir.attachment", "ir_attachment_drawings", "not_in_bydesign"),

    # --- Inventory ---
    ObjectSpec(27, "Inventory", "Master", "Warehouses", True, "stock.warehouse", "stock_warehouse", "built", "REAL DATA: same khlocation source as #28, filtered on InventoryManagedLocationIndicator (ByDesign's own signal for 'this location tracks inventory') - 1/1 locations qualify as of 2026-09-25 (tenant used to have 4 sites; confirmed live it now has only 1, 'Danlaw Technologies India Limited' - a tenant-side change, not a pipeline gap)",
               raw_sources=("khlocation__LocationCollection.json",)),
    ObjectSpec(28, "Inventory", "Master", "Locations", True, "stock.location", "stock_location", "built", "REAL DATA: khlocation custom service, 1 site as of 2026-09-25 (was 4; confirmed live - see #27 note) plus its storage areas",
               raw_sources=("khlocation__LocationCollection.json",)),
    ObjectSpec(29, "Inventory", "Master", "UOM", True, "uom.uom", "uom_uom", "built", "REAL DATA: vmumaterial's MaterialBaseMeasureUnitCodeCollection codelist, already-imported service, no new upload needed - 23 units",
               raw_sources=("vmumaterial__MaterialBaseMeasureUnitCodeCollection.json",)),
    ObjectSpec(30, "Inventory", "Master", "Lot/Serial Numbers", False, "stock.lot", "stock_lot", "built",
               "REAL DATA. khproductionorder/ProductionLotCollection (the originally investigated source) really is a "
               "dead end - ObjectID + ID only, no product reference. Closed 2026-09-25 via khgoodsandactivityconfirmation's "
               "embedded IdentifiedStock node (only 4 fields - no dates at all, confirmed via live $metadata), joined to "
               "a product via InventoryChangeItemCollection. Rebuilt 2026-09-28 after the user asked where production/"
               "expiration dates were: IdentifiedStock is actually its own full standalone Business Object with "
               "ExpirationDateTime and ProductionDateTime, never exposed as OData until built directly via the OData "
               "Editor (service khbatch, Work Center View MMA_PHYSICALINVENTORY - same self-service path as "
               "khbomvariant/khequipmentresource). 52,101 real batch records, 51,864 (99.5%) with a real production "
               "date, 1,743 (3.3%) with a real expiration date - most materials on this tenant aren't expiry-tracked, "
               "so a blank expiration_date is real data, not a gap. MaterialUUID lives directly on this entity, so no "
               "join through InventoryChangeItemCollection is needed any more for this object. Extended again "
               "2026-09-28 after the user pasted a real screenshot of the live Identified Stock edit screen showing "
               "Supplier ID/Status/Valuation Level Type populated but absent from the CSV - khbatch had only ever "
               "selected 5 of its real Root fields. Re-opened the OData Editor and added IdentifiedStockPartyID, "
               "SupplierUUID, LifeCycleStatusCode, ProductValuationLevelTypeCode; codes decoded via khbatch's own "
               "live codelist entity sets. 1,317/52,101 rows (2.5%) carry a real supplier - most identified stock on "
               "this tenant has none, confirmed not a join failure.",
               raw_sources=("khbatch__IdentifiedStockCollection.json",)),
    ObjectSpec(31, "Inventory", "Transaction", "Inventory Adjustments", False, "stock.quant", "stock_quant_adjustment", "built",
               "REAL DATA: 'Inventory Balance' (SCMINBU03) on scm_physicalinventory_analytics.svc, grouped by material x "
               "logistics area x site - exactly Odoo's stock.quant grain. 1805 on-hand balances; location_id resolves to the "
               "16 storage areas now written into stock_location.csv (1802/1805).",
               raw_sources=("scm_physicalinventory_analytics.svc__RPSCMINBU03_Q0001QueryResults.json",)),
    ObjectSpec(32, "Inventory", "Transaction", "Stock Transfers", False, "stock.picking", "stock_picking_transfer", "built",
               "REAL DATA: khinbounddelivery - 49 inbound deliveries with 119 items. This is the receipts side; outbound "
               "deliveries were already covered as #64. Quantities live in a separate ItemQuantityCollection keyed on the "
               "item's ObjectID, not on the item row itself. picking_type_id is written as Odoo's built-in "
               "stock.picking_type_in rather than an external ID of ours.",
               raw_sources=("khinbounddelivery__InboundDeliveryCollection.json", "khinbounddelivery__ItemCollection.json",
                            "khinbounddelivery__ItemQuantityCollection.json", "khinbounddelivery__SenderPartyCollection.json")),
    ObjectSpec(33, "Inventory", "Transaction", "Stock Moves History", False, "stock.move", "stock_move_history", "built",
               "REAL DATA, rebuilt 2026-09-28 from a much larger source after team feedback flagged the previous version "
               "as incomplete. Was: khgoodsandserviceacknowledgement, 137 receipts / 238 lines. Now: "
               "khgoodsandactivityconfirmation's real inventory-movement ledger - 147,979 confirmations, 366,585 movement "
               "lines - already fully extracted in output_raw/ but never wired into a transform (the earlier note that its "
               "InventoryChangeItemCollection 500'd was true once but no longer is - re-confirmed extracting cleanly at "
               "full volume). Issue/receipt leg pairs on the same confirmation+material (105,903 of them, zero quantity "
               "mismatches) are merged into single two-sided stock.move rows with real source/destination logistics "
               "areas; every other row keeps its one known real location and uses Odoo's own built-in Suppliers/"
               "Customers/Inventory-adjustment virtual location for the side SAP genuinely didn't attach a second area "
               "to. 260,682 rows, 0 missing product_id, 0 missing quantity. lot_id/id added 2026-09-28 after the user "
               "asked why #30's lot data wasn't showing up here - real gap, not a data gap: InventoryChangeItemCollection "
               "carries IdentifiedStockUUID on 185,158/366,585 raw lines (50.5%), every one matching a real khbatch "
               "record, but the field was never read. Now resolved to stock_lot.csv's own external ID - 156,714/260,682 "
               "output rows (60.1%) carry a real lot_id/id, 0 broken references. The remaining rows have no "
               "IdentifiedStockUUID on this tenant at all (not every movement is of batch/lot-tracked stock).",
               raw_sources=("khgoodsandactivityconfirmation__GoodsAndActivityConfirmationCollection.json",
                            "khgoodsandactivityconfirmation__InventoryChangeItemCollection.json",
                            "khgoodsandactivityconfirmation__ItemChangeQuantityCollection.json")),

    # --- Maintenance ---
    ObjectSpec(34, "Maintenance", "Master", "Equipment", True, "maintenance.equipment", "maintenance_equipment", "built", "MANDATORY - closed 2026-09-28. Earlier notes (kept for context) correctly found no standalone 'Equipment' master data object in any standard or custom service - ByDesign genuinely has no Preventive Maintenance module. What resolves the sheet requirement instead: the real ByDesign standard Business Object EquipmentResource ('Supply Chain Design Master Data' -> 'Resources' in the live UI, the same discovery as khbomvariant) - 18 real records (SMT lines, wave soldering, final inspection, etc.), genuinely typed 'Equipment Resource' on this tenant. Never published as OData until built directly via the OData Editor (self-service, no .xml import): service khequipmentresource, Work Center View SCM_RESOURCES (found by searching the picker for 'Resources' - it only matches a Work Center View's own display name, not its technical ID or parent Work Center name, which is why 'Equipment'/'Design'/'Master Data' searches all returned zero results first). See build_equipment() in src/transform_odoo.py; raw_sources below.",
               raw_sources=("khequipmentresource__EquipmentResourceCollection.json",)),
    ObjectSpec(35, "Maintenance", "Master", "Maintenance Teams", False, "maintenance.team", "maintenance_team", "not_in_bydesign"),
    ObjectSpec(36, "Maintenance", "Master", "Maintenance Checklists", False, "maintenance.checklist", "maintenance_checklist", "not_in_bydesign"),
    ObjectSpec(37, "Maintenance", "Transaction", "Preventive Maintenance Schedule", False, "maintenance.request", "maintenance_request_preventive", "not_in_bydesign"),
    ObjectSpec(38, "Maintenance", "Transaction", "Maintenance Requests", False, "maintenance.request", "maintenance_request", "not_in_bydesign"),
    ObjectSpec(39, "Maintenance", "Transaction", "Maintenance History", False, "maintenance.request", "maintenance_request_history", "not_in_bydesign"),

    # --- Manufacturing ---
    ObjectSpec(40, "Manufacturing", "Master", "BOMs", True, "mrp.bom", "mrp_bom", "built",
               "REAL DATA, closed 2026-09-25. Confirmed unobtainable via the tenant's 1485 built-in + 609 custom-.xml entity "
               "sets (an exhaustive negative result, still true of THAT set) - but the data itself was real, visible live in "
               "the ByDesign UI under Enterprise Search > 'Bills of Material Variants'. Closed by building a brand new "
               "custom OData service (khbomvariant) directly via the OData Editor - self-service, no .xml import needed - "
               "exposing the underlying ProductionBillOfMaterial standard Business Object for the first time. "
               "400 BOM headers, 448 variants (447 non-obsolete -> mrp_bom.csv rows), 14,947 component lines -> "
               "mrp_bom_line.csv. product_tmpl_id/id and each line's product_id/id resolve via the same MaterialUUID -> "
               "InternalID lookup used for product_template.csv (1/447 boms and 0/14947 lines unresolved). Verified against "
               "the live UI screen for one real BOM (CS90861DOOO): all 5 components shown on screen matched exactly "
               "(products + quantities), plus 8 more from other item groups not visible in that screen's scroll position. "
               "Known limitation: components attach to the BOM header, not a specific variant, so the 48 headers with "
               "multiple variants get the same component list per variant - matches what the raw data actually supports. "
               "Full story + screenshots: sap_odata_editor_walkthrough/bom_service_setup/README.md.",
               raw_sources=("khbomvariant__ProductionBillOfMaterialCollection.json",
                             "khbomvariant__ProductionBillOfMaterialVariantCollection.json",
                             "khbomvariant__ProductionBillOfMaterialItemGroupCollection.json",
                             "khbomvariant__ProductionBillOfMaterialItemGroupItemChangeStateCollection.json")),
    ObjectSpec(41, "Manufacturing", "Master", "Routings", False, "mrp.routing.workcenter", "mrp_routing_workcenter", "pending_mapping", "Data source confirmed: 'Released Execution Production Model Operation' (SCM_REPM_OPER)"),
    ObjectSpec(42, "Manufacturing", "Master", "Work Centers", True, "mrp.workcenter", "mrp_workcenter", "built", "REAL DATA: derived from khproductionorder's OperationCollection (ResourceID/ResourceDescription), deduplicated - no dedicated Work Center master service found, but this data was already on hand (used for #44) - 17 distinct work centers as of 2026-09-25 (was 18 earlier - minor drift in the source data, negligible)",
               raw_sources=("khproductionorder__OperationCollection.json",)),
    ObjectSpec(43, "Manufacturing", "Master", "Operations", False, "mrp.routing.workcenter", "mrp_routing_workcenter_ops", "built",
               "REAL DATA, closed 2026-09-28: khproductionorder/OperationCollection was already raw-extracted (82,859 "
               "rows, one per operation per production order) but never transformed - #44's own docstring flagged "
               "this. Deduplicated by (operation ID, resource) among 'Make' rows to the real distinct operation types "
               "actually run on each work center - 22 combinations (SMT on 10100, AOI on 10200, wave soldering on "
               "10500, etc.). Cycle time deliberately not included - ProcessingNetDuration is an observed per-order "
               "duration, not a standard planned time, so there is no single correct value without fabricating one.",
               raw_sources=("khproductionorder__OperationCollection.json",)),
    ObjectSpec(44, "Manufacturing", "Transaction", "Manufacturing Orders", False, "mrp.production", "mrp_production", "built", "REAL DATA: khproductionorder custom service, 152 orders. Header only - MainProductOutputCollection's 2842 rows don't carry a ParentObjectID and don't reliably join back to a specific order, so product_id/product_qty are left blank; OperationCollection (1158 rows, joins correctly via ParentObjectID) is raw-extracted but not yet transformed into routing lines",
               raw_sources=("khproductionorder__ProductionOrderCollection.json", "khproductionorder__MainProductOutputCollection.json", "khproductionorder__OperationCollection.json")),
    ObjectSpec(45, "Manufacturing", "Transaction", "Production History", False, "mrp.production", "mrp_production_history", "built",
               "REAL DATA: the closed subset of the same khproductionorder source as #44. LifeCycleStatusCodeText is real "
               "and populated (Started 86, Released 41, Finished 17, In Preparation 7, Canceled 1); Finished and Canceled "
               "are the terminal states, giving 18 historical orders.",
               raw_sources=("khproductionorder__ProductionOrderCollection.json",)),

    # --- Purchase ---
    ObjectSpec(46, "Purchase", "Master", "Vendors", True, "res.partner", "res_partner", "built",
               "REAL DATA: khsupplier custom service (plain CRUD) - 2,147 suppliers (grew substantially since the 250 "
               "first documented here; the tenant's real supplier list, confirmed via live re-extraction 2026-09-28, not "
               "a pipeline bug), with matching address/bank/tax coverage. Same reasoning as #17: the analytics route "
               "sampled non-key fields; plain CRUD needs no chunking at all. See #17's note for the 2026-09-28 field "
               "additions (payment_terms/incoterms/is_bidder/etc.) - same res_partner.csv, same build_res_partner().",
               raw_sources=("khsupplier__SupplierCollection.json", "khsupplier__CurrentDefaultPostalAddressCollection.json", "khsupplier__TaxNumberCollection.json", "khsupplier__BankDetailsCollection.json", "khsupplier__RoleCollection.json")),
    ObjectSpec(47, "Purchase", "Master", "Vendor Pricelists", False, "product.supplierinfo", "product_supplierinfo", "built",
               "REAL DATA: vmumaterial/SupplierInformationCollection - 34 material-to-supplier links with the supplier's own "
               "part number and lead time. Already extracted but read by no transform until now. No price column exists on "
               "this entity (ByDesign keeps supplier prices in price lists this tenant does not publish), so product_code and "
               "delay are mapped and price is left for Odoo to default rather than fabricated.",
               raw_sources=("vmumaterial__SupplierInformationCollection.json", "vmumaterial__MaterialCollection.json")),
    ObjectSpec(48, "Purchase", "Transaction", "Purchase Requisitions", False, "purchase.requisition", "purchase_requisition", "pending_mapping", "No matching data source found"),
    ObjectSpec(49, "Purchase", "Transaction", "RFQs", False, "purchase.order", "purchase_order_rfq", "built",
               "REAL DATA: ByDesign has no separate RFQ object - an RFQ is a purchase order not yet ordered. "
               "LifeCycleStatusCodeText splits the orders cleanly: In Preparation + In Approval are the pre-order "
               "documents (this file, 136 rows after the 2026-10-01 live re-pull, was 139), against Sent/Follow-Up/"
               "Finished which are live orders already covered by #50. Fixed 2026-09-30 (caught in a pre-import team "
               "validation pass): these "
               "same 139 orders used to also appear in purchase_order.csv (#50) under a different external-ID prefix "
               "(sap_po_* vs this file's sap_rfq_*) with no exclusion between the two builds - importing both would "
               "have created duplicate purchase.order records in Odoo. build_purchase_orders() now excludes any order "
               "whose LifeCycleStatusCodeText is In Preparation/In Approval, via the shared _RFQ_STAGE_STATES set - "
               "confirmed 0 id overlap between the two files after the fix. "
               "Fixed 2026-10-01 (team question: are RFQ line items extracted too? - they weren't): this build "
               "function only ever wrote purchase_order_rfq.csv (headers) - the same khpurchaseorder/ItemCollection "
               "these 136 orders use was already fully extracted (340 real line items), just never written to a "
               "line file at all. purchase_order_rfq_line.csv now exists, 340 rows, 0 blank/broken order_id/id.",
               raw_sources=("khpurchaseorder__PurchaseOrderCollection.json", "khpurchaseorder__SupplierCollection.json", "khpurchaseorder__ItemCollection.json")),
    ObjectSpec(50, "Purchase", "Transaction", "Purchase Orders", True, "purchase.order", "purchase_order", "built", "REAL DATA: cust/v1/khpurchaseorder custom OData service (user-imported Cloud Applications Studio BO, from byd-api-samples-main) - 15,704 POs, 45,847 lines after the 2026-10-01 live re-pull (live tenant total minus the 136 pre-order documents excluded to #49, see #49's note; was 15,658/45,624). product_id/id on lines resolves once Products (#57) is wired up. "
               "2026-10-01 (team pre-import check, Tax + missing-product requested on PO lines): purchase_order_line.csv "
               "now carries price_tax from ItemCollection's real TaxAmount (confirmed via live $metadata: no TaxCode/"
               "rate field exists on this entity, only the computed amount, so this is informational, not a "
               "taxes_id/id relation) - 0 blank across all 45,847 lines. product_id/id now goes through the same "
               "_known_product_ids() guard already used on invoice/bill lines, for consistency (0 lines affected - "
               "every ProductID present already resolves). 18,421/45,847 raw lines have NO ProductID at all - "
               "checked directly against output_raw/: ItemTypeCode mostly 'Material' with real free-text "
               "descriptions (e.g. 'F2 LASER CUT STENCILS') and no ProductSellerID/ProductStandardID either - "
               "genuine non-catalog PO lines SAP allows without a Material Master link, confirmed not resolvable, "
               "not a pipeline gap. Also: line item descriptions containing a literal \" were replaced with the "
               "Unicode double-prime (U+2033) - see #63's note on the same fix, applied here too (0 lines with a "
               "straight quote remaining, confirmed). Also same date (live SAP UI audit against the custom OData "
               "service, team request for 'more missing fields'): incoterms/incoterms_location and "
               "buyer_responsible_name added - both were already accessible via the API (IncotermsCodeText/"
               "IncotermsLocationName sitting unused on PurchaseOrderCollection; EmployeeResponsibleCollection "
               "already extracted), just never read by this transform. buyer_responsible_name resolves via "
               "_employee_name_by_code() (EmployeeResponsibleCollection's PartyID is an EmployeeID code like "
               "'G020', joined against khemployee) - confirmed exact match against the live UI's 'Buyer "
               "Responsible' field for a real PO. incoterms/incoterms_location: 15,689/15,704 (99.9%). "
               "buyer_responsible_name: 15,704/15,704 (100%, after filtering EmployeeResponsibleCollection's "
               "candidates to real EmployeeIDs - it bundles the PO's own company code and the supplier's BP id "
               "in the same collection with no role code, same shape as SupplierCollection). Payment Terms was "
               "also requested and found VISIBLE with real data on the live UI ('30 days from the date of invoice "
               "subject to acceptance') but confirmed ABSENT from khpurchaseorder's OData metadata entirely - "
               "unlike Incoterms/Buyer Responsible, this one is not accessible via the API at all and needs a live "
               "OData Editor change to the custom service to expose it (not yet done - pending user confirmation, "
               "see SAP_IMPORT_PLAN.md or the team's 2026-10-01 field audit notes).",
               raw_sources=("khpurchaseorder__PurchaseOrderCollection.json", "khpurchaseorder__ItemCollection.json", "khpurchaseorder__SupplierCollection.json", "khpurchaseorder__EmployeeResponsibleCollection.json")),
    ObjectSpec(51, "Purchase", "Transaction", "Vendor Bills", False, "account.move", "account_move_vendor_bill", "built", "REAL DATA: khsupplierinvoice custom service - 55,449 invoices, 212,420 lines. Partner resolved via "
               "SellerPartyCollection. Fixed 2026-09-29, caught by a team cross-validation pass before Odoo import: "
               "resolve_party()'s final fallback used to return a document's most-common PartyID even when it wasn't "
               "a known partner at all (3/55,449 rows - SAP-internal group codes like 'G113' that never became a real "
               "Business Partner) - now returns None in that case instead of a broken external-id reference. 0 broken "
               "partner_id/id references confirmed after the fix.",
               raw_sources=("khsupplierinvoice__SupplierInvoiceCollection.json", "khsupplierinvoice__ItemCollection.json", "khsupplierinvoice__SellerPartyCollection.json")),
    ObjectSpec(52, "Purchase", "Transaction", "Vendor Credit Notes", False, "account.move", "account_move_vendor_credit", "built",
               "REAL DATA: ByDesign has no separate credit-memo entity - credit memos sit in the ordinary invoice table "
               "and are identified by TypeCodeText. khsupplierinvoice/SupplierInvoiceCollection holds 9 rows typed "
               "'Credit Memo', mapped to Odoo move_type in_refund. No new extraction was needed; the data had been on "
               "disk all along and nothing had looked at the type column.",
               raw_sources=("khsupplierinvoice__SupplierInvoiceCollection.json", "khsupplierinvoice__SellerPartyCollection.json")),

    # --- Quality ---
    ObjectSpec(53, "Quality", "Master", "Quality Teams", False, "quality.alert.team", "quality_alert_team", "not_in_bydesign", "QM module is not part of standard ByDesign; confirmed by cross-checking the tenant's full 573-entry Design Data Sources catalog - zero matches for 'quality'. Confirm true source system with user"),
    ObjectSpec(54, "Quality", "Master", "Quality Points", False, "quality.point", "quality_point", "not_in_bydesign"),
    ObjectSpec(55, "Quality", "Transaction", "Quality Checks", False, "quality.check", "quality_check", "not_in_bydesign"),
    ObjectSpec(56, "Quality", "Transaction", "Inspection Results", False, "quality.check", "quality_check_inspection", "not_in_bydesign"),

    # --- Sales ---
    ObjectSpec(57, "Sales", "Master", "Products", True, "product.template", "product_template", "built", "REAL DATA, FULL FIELD COVERAGE: all 15 real (non-empty) per-material entities in vmumaterial/vmumaterialvaluationdata checked (78 entity sets total exist; 172 distinct SAP fields across the 15 real ones) - MaterialCollection (base), TextCollection (detailed description, 2172/3058), PurchasingCollection/SalesCollection (UOM + purchase_ok/sale_ok), ProductCategoryCollection (category, 3058/3058 resolved), PlanningCollection (ProcurementTypeCode -> route_ids/id, real 107/93 Buy/Manufacture split), IdentificationCollection/LogisticsCollection/ValuationCollection/AvailabilityConfirmationCollection/PlanningForecastGroupCollection/QuantityConversionCollection/DeviantTaxClassificationCollection (extracted, no corresponding core Odoo product.template field - see SAP_Field_Mapping.xlsx sheet 57 for exactly which), vmumaterialvaluationdata (cost price, 994/3058). No barcode/weight/dimension data exists on this tenant (GlobalTradeItemNumberCollection and QuantityCharacteristicCollection both confirmed 0 rows live) - not fabricated, left blank. SERVICE PRODUCTS ADDED 2026-09-18: khserviceproduct is a completely separate master from vmumaterial's materials (ByDesign splits them; Odoo does not), contributing 7 more rows typed 'service' - they were absent from the output entirely before that service was imported.",
               raw_sources=("vmumaterial__MaterialCollection.json", "vmumaterial__TextCollection.json", "vmumaterial__PurchasingCollection.json", "vmumaterial__SalesCollection.json", "vmumaterial__ProductCategoryCollection.json", "vmumaterial__PlanningCollection.json", "vmumaterial__IdentificationCollection.json", "vmumaterial__LogisticsCollection.json", "vmumaterial__ValuationCollection.json", "vmumaterial__AvailabilityConfirmationCollection.json", "vmumaterial__PlanningForecastGroupCollection.json", "vmumaterial__QuantityConversionCollection.json", "vmumaterial__DeviantTaxClassificationCollection.json", "vmumaterialvaluationdata__MaterialValuationDataCollection.json", "vmumaterialvaluationdata__ValuationPriceCollection.json")),
    ObjectSpec(58, "Sales", "Master", "Product Categories", True, "product.category", "product_category", "built", "REAL DATA: vmumaterial's ProductCategoryCollection, already-imported service, no new upload needed - deduplicated from 3058 material rows to distinct categories",
               raw_sources=("vmumaterial__ProductCategoryCollection.json",)),
    ObjectSpec(59, "Sales", "Master", "Pricelists", True, "product.pricelist", "product_pricelist", "built", "REAL DATA: 226 pricelists, one per real SAP Sales Arrangement resolved from khsalesorder via "
               "_resolve_order_to_arrangement() (Customer + SalesOrg + DistributionChannel business key) - not one "
               "synthetic catch-all. partner_id/id populated as of 2026-09-28 via khcustomer/CustomerCollection's "
               "UUID->InternalID lookup (same pattern as vmumaterial for products) - 225/225 real sales arrangements "
               "resolved (the 226th row is the synthetic unmatched-orders fallback, which has no single customer by "
               "definition).",
               raw_sources=("khsalesarrangement__SalesArrangementCollection.json", "khcustomer__CustomerCollection.json")),
    ObjectSpec(60, "Sales", "Transaction", "Discount Rules", False, "product.pricelist.item", "product_pricelist_item_discount", "built",
               "THIS TENANT HAS NO DISCOUNT RULES - checked, not assumed: of the 94 price components SAP categorises as "
               "'Discount', every non-zero one is a Rounding Difference, and Item Discounts / Header Discounts are 0.00 "
               "throughout. Nothing was invented to fill the gap. What the same price-component data does carry is real "
               "LIST PRICES, and product.pricelist.item is Odoo's model for 'this product is priced at X' - so this file "
               "holds 1109 price lines attached to their real resolved #59 pricelists (61 of 226 carry lines); only the "
               "8 orders whose Sales Arrangement couldn't be resolved fall into a small 'SAP List Prices - Unmatched "
               "Sales Arrangement' fallback pricelist. Where a product was quoted at several prices the most frequent "
               "wins; the full per-order history is in output_full_csv/.",
               raw_sources=("khsalesorder__ItemPriceComponentCollection.json", "khsalesorder__ItemCollection.json",
                            "khsalesorder__ItemProductCollection.json")),
    ObjectSpec(61, "Sales", "Master", "Customer Price Lists", False, "product.pricelist", "product_pricelist", "built",
               "CLOSED 2026-09-28: same underlying file as #59 (product_pricelist.csv) - the UUID->partner mapping "
               "that blocked this is now solved (see #59's note), so every real pricelist genuinely carries its "
               "customer's partner_id/id and no separate file is needed to make this 'by customer'.",
               raw_sources=("khsalesarrangement__SalesArrangementCollection.json", "khcustomer__CustomerCollection.json")),
    ObjectSpec(62, "Sales", "Transaction", "Quotations", False, "sale.order", "sale_order_quotation", "pending_mapping", "UPDATED 2026-09-26: the authorization block described here is resolved - live curl now returns HTTP 200 with "
               "$inlinecount __count=0 (not RBAM_ERROR). khcustomerquote is imported, live, and authorized; the tenant "
               "genuinely just has zero customer quotes right now. Still pending_mapping because there's no data to "
               "confirm a transform against, not because of access - re-check if the tenant gets real quote data later."),
    ObjectSpec(63, "Sales", "Transaction", "Sales Orders", True, "sale.order", "sale_order", "built", "REAL DATA: khsalesorder custom service - 4,326 orders, 9,947 lines (re-pulled live 2026-10-01, "
               "grew from 4,308/9,921). Partner resolved via BuyerPartyCollection. Fixed 2026-09-28, caught by a "
               "team cross-validation pass before Odoo import: state used to be hardcoded 'sale' for every order "
               "even though ~48% are Canceled/Partially Canceled per CancellationStatusCode - now maps to Odoo's "
               "'cancel' state. Line quantity/UoM used to be entirely missing (every line would have imported at "
               "qty 0) - now summed from ItemScheduleLineCollection, preferring 'Confirmed' schedule lines over "
               "'Requested'; a real minority of lines still have no quantity because the item genuinely has no "
               "schedule line on this tenant - real absence, not a bug. "
               "2026-10-01 (team pre-import check, Payment Terms/Sales Team/Salesperson requested): Payment Terms "
               "confirmed absent, twice over - via $metadata, then via a live scan of every root-level node on the "
               "underlying standard SalesOrder Business Object itself in SAP's OData Editor (not just the custom "
               "service): alphabetically, PaymentControl is immediately followed by PeriodTerms, nothing between. "
               "A genuine structural gap on the standard BO (unlike Purchase Orders, where PaymentTerms turned out "
               "to already exist and just needed extracting - see #50's note), not something the OData Editor can "
               "add since it only exposes existing BO associations. The '30 days net' shown on the live Sales "
               "Order UI is computed/displayed from elsewhere (most likely the customer's own default terms), not "
               "stored on the order. No live tenant change was made (service opened read-only, closed unsaved). "
               "Sales Team/Salesperson closed the same day once SAP access was restored (password had rotated, "
               "causing tenant-wide 401s earlier) - added salesperson_name column, sourced from "
               "SalesUnitPartyCollection re-extracted with $expand=SalesUnitPartyName (a real bug in "
               "_flatten_expanded()/src/sap_client.py was found and fixed while wiring this up - it didn't handle "
               "an expanded nav property arriving as a bare list, which is the shape this one came back in). "
               "Resolution excludes whichever SalesUnitParty candidate matches the order's own resolved customer, "
               "then takes the first remaining candidate's name - resolves for 4,325/4,326 orders (99.98%). Also "
               "same date: line item descriptions containing a literal \" (inch-mark product dimensions, e.g. "
               "2.76\"L X 1.97\"W) were replaced with the Unicode double-prime (U+2033) - the CSV itself was always "
               "valid RFC 4180, but the escaped-quote-then-more-text pattern is a known rough edge for Excel's "
               "quick-open CSV parser (reported as the product description 'shifting to the next cell'). Same fix "
               "applied to purchase_order_line.csv. "
               "price_unit added same date (team question: was unit price there? - it wasn't) - sourced from "
               "ItemPriceComponentCollection's 'List Price' component, 9,943/9,947 lines (99.96%). Real finding "
               "while adding it: that component's own pricing-basis quantity does not always match the 'Confirmed' "
               "schedule-line quantity used for product_uom_qty - 4,662/9,902 lines (47%) differ. Genuine SAP "
               "behaviour, not a bug: price is locked in against the quantity on the order at pricing time, while "
               "the schedule line's Confirmed quantity reflects what was later actually committed for delivery. "
               "price_subtotal/price_unit must not be derived from product_uom_qty on this data.",
               raw_sources=("khsalesorder__SalesOrderCollection.json", "khsalesorder__ItemCollection.json", "khsalesorder__BuyerPartyCollection.json", "khsalesorder__ItemProductCollection.json", "khsalesorder__ItemScheduleLineCollection.json", "khsalesorder__SalesUnitPartyCollection.json")),
    ObjectSpec(64, "Sales", "Transaction", "Deliveries", False, "stock.picking", "stock_picking_delivery", "built", "REAL DATA: khoutbounddelivery custom service, 130 deliveries, 157 lines. Partner resolved via BuyerPartyCollection (121/130 resolved)",
               raw_sources=("khoutbounddelivery__OutboundDeliveryCollection.json", "khoutbounddelivery__ItemCollection.json", "khoutbounddelivery__BuyerPartyCollection.json")),
    ObjectSpec(65, "Sales", "Transaction", "Customer Invoices", False, "account.move", "account_move_customer_invoice", "built", "REAL DATA: khcustomerinvoice custom service - 30,081 invoices, 35,514 lines. Partner resolved via "
               "BuyerPartyCollection. Fixed 2026-09-29, caught by a team cross-validation pass before Odoo import: "
               "product_id/id on the line file used to be emitted for any real SAP ProductID even when that material "
               "no longer exists in product_template.csv (194/35,514 lines, 0.5% - old invoices referencing since-"
               "obsoleted materials, a real historical gap given this tenant's data goes back to 2011) - a broken "
               "external-id reference Odoo would reject. Now checked against _known_product_ids() and left blank in "
               "that case (description still carried in the name column); 0 broken references confirmed after the "
               "fix. Same guard applied to account_move_vendor_bill_line.csv (currently 0 affected, same risk class).",
               raw_sources=("khcustomerinvoice__CustomerInvoiceCollection.json", "khcustomerinvoice__ItemCollection.json", "khcustomerinvoice__BuyerPartyCollection.json")),
    ObjectSpec(66, "Sales", "Transaction", "Credit Notes", False, "account.move", "account_move_credit_note", "built",
               "REAL DATA, two unioned sources: (1) khcustomerinvoicerequest/CustomerInvoiceRequestCollection, the 21 "
               "rows typed 'Manual Credit Memo Request', mapped to Odoo move_type out_refund - this entity names the "
               "customer directly via BuyerPartyID, so no party-resolution guesswork needed. (2) Added 2026-09-24: "
               "khcustomerreturn/CustomerReturnCollection, 796 rows with CreditMemoStatusCodeText='Finished' (of 810 "
               "total) - a whole service coverage_gap.py found was never wired into SOURCES at all. A ByDesign "
               "'Customer Return' is physical goods coming back, but every finished one produces a real credit memo, "
               "so these are additional genuine credit notes, not returns/RMA data - resolved via BuyerPartyCollection "
               "the same way Purchase Orders/Sales Orders are.",
               raw_sources=("khcustomerinvoicerequest__CustomerInvoiceRequestCollection.json",
                            "khcustomerreturn__CustomerReturnCollection.json",
                            "khcustomerreturn__BuyerPartyCollection.json")),

    # --- Added: relational data not on the sheet but required for the above to import cleanly ---
    ObjectSpec(0, "Common", "Master", "Currency Exchange Rates", False, "res.currency.rate", "res_currency_rate", "pending_mapping", "Odoo ships currencies/countries out of the box, but NOT historical FX rates - needed for FY20-21 through FY25-26 transactional data to post at the right value"),
]


def summary_counts():
    counts = {}
    for obj in REGISTRY:
        counts[obj.status] = counts.get(obj.status, 0) + 1
    return counts
