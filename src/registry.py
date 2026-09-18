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
               "band confirmed against the real account names (see ACCOUNT_TYPE_BANDS in src/transform_odoo.py).",
               raw_sources=("fin_costandrevenue_analytics.svc__RPFINCACU04_Q0002QueryResults.json",
                            "fin_audit_analytics.svc__RPFINGLAU02_Q0002QueryResults.json",
                            "fin_generalledger_analytics.svc__RPFINFXAU05_Q0001QueryResults.json",
                            "fin_generalledger_analytics.svc__RPFINFCDU02_Q0001QueryResults.json",
                            "fin_audit_analytics.svc__RPFININVU03_Q0001QueryResults.json",
                            "fin_audit_analytics.svc__RPFINGLAU02_Q0003QueryResults.json")),
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
    ObjectSpec(7, "Accounts", "Master", "Analytic Accounts", False, "account.analytic.account", "account_analytic_account", "pending_mapping"),
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
    ObjectSpec(10, "Accounts", "Transaction", "Customer Payments", False, "account.payment", "account_payment_customer", "built", "REAL DATA: khpayment custom service, 544 payments total - customer vs vendor split via partner_type column (looked up against res_partner's customer_rank/supplier_rank), combined with #11 in one account_payment.csv",
               raw_sources=("khpayment__PaymentCollection.json",)),
    ObjectSpec(11, "Accounts", "Transaction", "Vendor Payments", False, "account.payment", "account_payment_vendor", "built", "REAL DATA: same khpayment source and account_payment.csv as #10, split by partner_type column",
               raw_sources=("khpayment__PaymentCollection.json",)),
    ObjectSpec(12, "Accounts", "Transaction", "Bank Statements", False, "account.bank.statement", "account_bank_statement", "built", "REAL DATA: khhousebankstatement custom service, 87 statements",
               raw_sources=("khhousebankstatement__HouseBankStatementCollection.json",)),
    ObjectSpec(13, "Accounts", "Transaction", "Journal Entries", False, "account.move", "account_move_journal", "pending_mapping", "Data source confirmed: 'Journal Entry Item' (FINACCDIB), 'Journal Entry Voucher' (FINACCENB)"),
    ObjectSpec(14, "Accounts", "Transaction", "Fixed Assets", False, "account.asset", "account_asset", "pending_mapping", "Data source confirmed: 'Fixed Asset' (FINFXASSB), 'Fixed Assets Master Data' (FINFXAU04)"),
    ObjectSpec(15, "Accounts", "Transaction", "Asset Depreciation", False, "account.asset.depreciation.line", "account_asset_depreciation_line", "pending_mapping", "Data source confirmed: 'Fixed Assets Values' (FINFXAU01)"),

    # --- Common ---
    ObjectSpec(16, "Common", "Master", "Attachments/Documents", False, "ir.attachment", "ir_attachment", "pending_mapping", "SAP attachments are typically binary content on GOS/DMS - needs a per-record download pass, not a flat OData select"),

    # --- CRM ---
    ObjectSpec(17, "CRM", "Master", "Customers", True, "res.partner", "res_partner", "built",
               "REAL DATA: khcustomer custom service (plain CRUD) - 44 customers with full address, tax number, bank "
               "detail and contact-person data. Replaced the bpm_businesspartnerdata_analytics.svc report, which had to "
               "be fetched in field-chunks and merged on a key SAP does not guarantee unique (44 keys vs 50 rows), so "
               "some fields were an arbitrary sample. Verified before switching: the CRUD source returns a strict "
               "superset - all 271 previous external IDs plus 17 more - so no document reference broke. Contact persons "
               "(36) now populate res_partner_contact.csv from RelationshipCollection; they were empty before.",
               raw_sources=("khcustomer__CustomerCollection.json", "khcustomer__PostalAddressCollection.json", "khcustomer__TaxNumberCollection.json", "khcustomer__RelationshipCollection.json")),
    ObjectSpec(18, "CRM", "Master", "Salespersons", True, "res.users", "res_users_salesperson", "built", "REAL DATA: khemployee custom service, 99 employees (output file is res_users.csv - see FILENAME_OVERRIDES)",
               raw_sources=("khemployee__EmployeeCollection.json", "khemployee__WorkplaceAddressCollection.json")),
    ObjectSpec(19, "CRM", "Master", "Activities", False, "mail.activity", "mail_activity", "pending_mapping"),
    ObjectSpec(20, "CRM", "Transaction", "Opportunities", False, "crm.lead", "crm_lead", "built", "REAL DATA: khopportunity custom service, 15 opportunities (all, unfiltered)",
               raw_sources=("khopportunity__OpportunityCollection.json",)),
    ObjectSpec(21, "CRM", "Transaction", "Open Opportunities", True, "crm.lead", "crm_lead_open", "built", "REAL DATA: same khopportunity source as #20, filtered on LifeCycleStatusCode (Open=1, In Process=2) - 8/15 open",
               raw_sources=("khopportunity__OpportunityCollection.json",)),
    ObjectSpec(22, "CRM", "Transaction", "Closed Opportunities", False, "crm.lead", "crm_lead_closed", "built", "REAL DATA: same khopportunity source as #20, filtered on LifeCycleStatusCode (Won=4, Lost=5) - 7/15 closed",
               raw_sources=("khopportunity__OpportunityCollection.json",)),

    # --- Engineering ---
    ObjectSpec(23, "Engineering", "Master", "Engineering Items", False, "product.template", "product_template_engineering", "not_in_bydesign", "PLM engineering items are not part of standard ByDesign; confirm source system with user"),
    ObjectSpec(24, "Engineering", "Master", "Document Revisions", False, "product.document", "product_document_revision", "not_in_bydesign"),
    ObjectSpec(25, "Engineering", "Master", "ECO", False, "mrp.eco", "mrp_eco", "not_in_bydesign", "Engineering Change Orders require the PLM module - not standard in ByDesign"),
    ObjectSpec(26, "Engineering", "Master", "Drawings", False, "ir.attachment", "ir_attachment_drawings", "not_in_bydesign"),

    # --- Inventory ---
    ObjectSpec(27, "Inventory", "Master", "Warehouses", True, "stock.warehouse", "stock_warehouse", "built", "REAL DATA: same khlocation source as #28, filtered on InventoryManagedLocationIndicator (ByDesign's own signal for 'this location tracks inventory') - 1/4 locations qualify",
               raw_sources=("khlocation__LocationCollection.json",)),
    ObjectSpec(28, "Inventory", "Master", "Locations", True, "stock.location", "stock_location", "built", "REAL DATA: khlocation custom service, 4 locations",
               raw_sources=("khlocation__LocationCollection.json",)),
    ObjectSpec(29, "Inventory", "Master", "UOM", True, "uom.uom", "uom_uom", "built", "REAL DATA: vmumaterial's MaterialBaseMeasureUnitCodeCollection codelist, already-imported service, no new upload needed - 23 units",
               raw_sources=("vmumaterial__MaterialBaseMeasureUnitCodeCollection.json",)),
    ObjectSpec(30, "Inventory", "Master", "Lot/Serial Numbers", False, "stock.lot", "stock_lot", "pending_mapping",
               "BLOCKED, deliberately not written: khproductionorder/ProductionLotCollection has 145 rows but exposes only "
               "ObjectID and ID - no ParentObjectID and no product reference of any kind - so lots cannot be attached to a "
               "product, and Odoo's stock.lot requires product_id. A file built from this would be 100% unimportable. "
               "Fix is an import, not code: khgoodsandactivityconfirmation.xml carries SerialNumber and IdentifiedStock with "
               "their material links (SAP_IMPORT_PLAN.md, priority 1)."),
    ObjectSpec(31, "Inventory", "Transaction", "Inventory Adjustments", False, "stock.quant", "stock_quant_adjustment", "built",
               "REAL DATA: 'Inventory Balance' (SCMINBU03) on scm_physicalinventory_analytics.svc, grouped by material x "
               "logistics area x site - exactly Odoo's stock.quant grain. 1805 on-hand balances; location_id resolves to the "
               "16 storage areas now written into stock_location.csv (1802/1805).",
               raw_sources=("scm_physicalinventory_analytics.svc__RPSCMINBU03_Q0001QueryResults.json",)),
    ObjectSpec(32, "Inventory", "Transaction", "Stock Transfers", False, "stock.picking", "stock_picking_transfer", "pending_mapping"),
    ObjectSpec(33, "Inventory", "Transaction", "Stock Moves History", False, "stock.move", "stock_move_history", "pending_mapping"),

    # --- Maintenance ---
    ObjectSpec(34, "Maintenance", "Master", "Equipment", True, "maintenance.equipment", "maintenance_equipment", "not_in_bydesign", "MANDATORY - investigated twice. (1) Zero matches for 'equipment' across the tenant's 573-entry Design Data Sources catalog. (2) User imported+tested tmserviceorder/tmserviceconfirmation/tmservicerequest (Service Order/Confirmation/Request custom services) as a possible alternate path: their metadata confirms NO standalone Equipment entity exists in any of them - ReferenceObjectCollection only carries ProductID(+SerialID), i.e. ByDesign models 'equipment' as a serialized Product instance, not separate master data. tmserviceorder/tmservicerequest data access is additionally blocked by SAP authorization restrictions for the SDK user (metadata visible, data reads return RBAM_ERROR); tmserviceconfirmation is accessible but has 0 rows on this tenant. CONCLUSION: standard ByDesign has no Equipment master data object - if this data is needed, it must come from a different source system or be confirmed out of scope with Danlaw/the client."),
    ObjectSpec(35, "Maintenance", "Master", "Maintenance Teams", False, "maintenance.team", "maintenance_team", "not_in_bydesign"),
    ObjectSpec(36, "Maintenance", "Master", "Maintenance Checklists", False, "maintenance.checklist", "maintenance_checklist", "not_in_bydesign"),
    ObjectSpec(37, "Maintenance", "Transaction", "Preventive Maintenance Schedule", False, "maintenance.request", "maintenance_request_preventive", "not_in_bydesign"),
    ObjectSpec(38, "Maintenance", "Transaction", "Maintenance Requests", False, "maintenance.request", "maintenance_request", "not_in_bydesign"),
    ObjectSpec(39, "Maintenance", "Transaction", "Maintenance History", False, "maintenance.request", "maintenance_request_history", "not_in_bydesign"),

    # --- Manufacturing ---
    ObjectSpec(40, "Manufacturing", "Master", "BOMs", True, "mrp.bom", "mrp_bom", "pending_mapping",
               "NOT OBTAINABLE OVER ODATA FROM THIS TENANT - the strongest negative result in the project, and the one "
               "mandatory object that genuinely cannot be closed from here. Searched for bom / 'bill of material' / "
               "'production model' / recipe / component / routing / explosion across BOTH (a) all 1485 entity sets of all 48 "
               "services the tenant publishes to this user (schema_snapshots/service_catalog.json, SERVICE_CATALOG.csv) and "
               "(b) all 609 entity sets defined across all 47 custom service .xml files - zero matches in either. Importing "
               "more custom services cannot produce it: no BOM service definition exists in the sample set. "
               "The design-time catalog does list PBOM data sources (SCMPBOMU02 etc), but they are NOT published as OData on "
               "this tenant - that catalog tracks report definitions, not published services. "
               "khproductionorder REFERENCES a BOM via ProductionModelID/ProductionModelVersionID but never exposes its "
               "components. Options for the user: expose the PBOM data sources via Business Configuration > Analytics, or "
               "extract BOMs by a non-OData route (UI export / file download) and hand over a CSV."),
    ObjectSpec(41, "Manufacturing", "Master", "Routings", False, "mrp.routing.workcenter", "mrp_routing_workcenter", "pending_mapping", "Data source confirmed: 'Released Execution Production Model Operation' (SCM_REPM_OPER)"),
    ObjectSpec(42, "Manufacturing", "Master", "Work Centers", True, "mrp.workcenter", "mrp_workcenter", "built", "REAL DATA: derived from khproductionorder's OperationCollection (ResourceID/ResourceDescription), deduplicated - no dedicated Work Center master service found, but this data was already on hand (used for #44) - 18 distinct work centers",
               raw_sources=("khproductionorder__OperationCollection.json",)),
    ObjectSpec(43, "Manufacturing", "Master", "Operations", False, "mrp.routing.workcenter", "mrp_routing_workcenter_ops", "pending_mapping"),
    ObjectSpec(44, "Manufacturing", "Transaction", "Manufacturing Orders", False, "mrp.production", "mrp_production", "built", "REAL DATA: khproductionorder custom service, 152 orders. Header only - MainProductOutputCollection's 2842 rows don't carry a ParentObjectID and don't reliably join back to a specific order, so product_id/product_qty are left blank; OperationCollection (1158 rows, joins correctly via ParentObjectID) is raw-extracted but not yet transformed into routing lines",
               raw_sources=("khproductionorder__ProductionOrderCollection.json", "khproductionorder__MainProductOutputCollection.json", "khproductionorder__OperationCollection.json")),
    ObjectSpec(45, "Manufacturing", "Transaction", "Production History", False, "mrp.production", "mrp_production_history", "pending_mapping"),

    # --- Purchase ---
    ObjectSpec(46, "Purchase", "Master", "Vendors", True, "res.partner", "res_partner", "built",
               "REAL DATA: khsupplier custom service (plain CRUD) - 250 suppliers, up from the 229 the analytics report "
               "returned, with 248 addresses, 82 bank accounts and 43 tax numbers. Same reasoning as #17: the analytics "
               "route sampled non-key fields (229 keys vs 237 rows); plain CRUD needs no chunking at all.",
               raw_sources=("khsupplier__SupplierCollection.json", "khsupplier__CurrentDefaultPostalAddressCollection.json", "khsupplier__TaxNumberCollection.json", "khsupplier__BankDetailsCollection.json")),
    ObjectSpec(47, "Purchase", "Master", "Vendor Pricelists", False, "product.supplierinfo", "product_supplierinfo", "built",
               "REAL DATA: vmumaterial/SupplierInformationCollection - 34 material-to-supplier links with the supplier's own "
               "part number and lead time. Already extracted but read by no transform until now. No price column exists on "
               "this entity (ByDesign keeps supplier prices in price lists this tenant does not publish), so product_code and "
               "delay are mapped and price is left for Odoo to default rather than fabricated.",
               raw_sources=("vmumaterial__SupplierInformationCollection.json", "vmumaterial__MaterialCollection.json")),
    ObjectSpec(48, "Purchase", "Transaction", "Purchase Requisitions", False, "purchase.requisition", "purchase_requisition", "pending_mapping", "No matching data source found"),
    ObjectSpec(49, "Purchase", "Transaction", "RFQs", False, "purchase.order", "purchase_order_rfq", "pending_mapping", "Filter: purchase.order in draft/sent state"),
    ObjectSpec(50, "Purchase", "Transaction", "Purchase Orders", True, "purchase.order", "purchase_order", "built", "REAL DATA: cust/v1/khpurchaseorder custom OData service (user-imported Cloud Applications Studio BO, from byd-api-samples-main) - 655 POs, 1861 line items confirmed. product_id/id on lines resolves now that Products (#57) is also wired up (1586/1595)",
               raw_sources=("khpurchaseorder__PurchaseOrderCollection.json", "khpurchaseorder__ItemCollection.json", "khpurchaseorder__SupplierCollection.json")),
    ObjectSpec(51, "Purchase", "Transaction", "Vendor Bills", False, "account.move", "account_move_vendor_bill", "built", "REAL DATA: khsupplierinvoice custom service, 458 invoices, 1581 lines. Partner resolved via SellerPartyCollection (442/458 resolved)",
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
    ObjectSpec(59, "Sales", "Master", "Pricelists", True, "product.pricelist", "product_pricelist", "built", "REAL DATA: khsalesarrangement custom service, 35 arrangements. partner_id/id NOT populated - CustomerUUID here is a hyphenated GUID that doesn't match res_partner's numeric-ID-based external IDs, and this tenant hasn't exposed a UUID->numeric-ID lookup",
               raw_sources=("khsalesarrangement__SalesArrangementCollection.json",)),
    ObjectSpec(60, "Sales", "Master", "Discount Rules", False, "product.pricelist.item", "product_pricelist_item_discount", "pending_mapping", "khsalesarrangement (#59's source) is header-only - no discount-rule line items found in it; would need a different/deeper entity not yet investigated"),
    ObjectSpec(61, "Sales", "Master", "Customer Price Lists", False, "product.pricelist", "product_pricelist_customer", "pending_mapping", "Same source as #59 but without a working partner link (see #59's note), so this isn't meaningfully 'by customer' yet - left pending until the UUID->partner mapping is solved"),
    ObjectSpec(62, "Sales", "Transaction", "Quotations", False, "sale.order", "sale_order_quotation", "pending_mapping", "khcustomerquote custom service is imported and live (metadata confirmed, CustomerQuoteCollection has real fields) but data reads are blocked by SAP authorization restrictions for the SDK user (RBAM_ERROR) - needs broader permissions granted on this tenant before it can be extracted"),
    ObjectSpec(63, "Sales", "Transaction", "Sales Orders", True, "sale.order", "sale_order", "built", "REAL DATA: khsalesorder custom service, 196 orders, 263 lines. Partner resolved via BuyerPartyCollection (195/196 resolved)",
               raw_sources=("khsalesorder__SalesOrderCollection.json", "khsalesorder__ItemCollection.json", "khsalesorder__BuyerPartyCollection.json", "khsalesorder__ItemProductCollection.json")),
    ObjectSpec(64, "Sales", "Transaction", "Deliveries", False, "stock.picking", "stock_picking_delivery", "built", "REAL DATA: khoutbounddelivery custom service, 130 deliveries, 157 lines. Partner resolved via BuyerPartyCollection (121/130 resolved)",
               raw_sources=("khoutbounddelivery__OutboundDeliveryCollection.json", "khoutbounddelivery__ItemCollection.json", "khoutbounddelivery__BuyerPartyCollection.json")),
    ObjectSpec(65, "Sales", "Transaction", "Customer Invoices", False, "account.move", "account_move_customer_invoice", "built", "REAL DATA: khcustomerinvoice custom service, 524 invoices, 623 lines. Partner resolved via BuyerPartyCollection (513/524 resolved)",
               raw_sources=("khcustomerinvoice__CustomerInvoiceCollection.json", "khcustomerinvoice__ItemCollection.json", "khcustomerinvoice__BuyerPartyCollection.json")),
    ObjectSpec(66, "Sales", "Transaction", "Credit Notes", False, "account.move", "account_move_credit_note", "built",
               "REAL DATA: khcustomerinvoicerequest/CustomerInvoiceRequestCollection, the 21 rows typed 'Manual Credit "
               "Memo Request', mapped to Odoo move_type out_refund. This entity names the customer directly via "
               "BuyerPartyID, so none of the party-resolution guesswork other document types need applies here.",
               raw_sources=("khcustomerinvoicerequest__CustomerInvoiceRequestCollection.json",)),

    # --- Added: relational data not on the sheet but required for the above to import cleanly ---
    ObjectSpec(0, "Common", "Master", "Currency Exchange Rates", False, "res.currency.rate", "res_currency_rate", "pending_mapping", "Odoo ships currencies/countries out of the box, but NOT historical FX rates - needed for FY20-21 through FY25-26 transactional data to post at the right value"),
]


def summary_counts():
    counts = {}
    for obj in REGISTRY:
        counts[obj.status] = counts.get(obj.status, 0) + 1
    return counts
