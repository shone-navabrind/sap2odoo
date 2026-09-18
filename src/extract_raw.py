"""
Stage 1: pull data out of SAP and dump it AS-IS, with every field SAP gives back - no
assumptions about which columns will matter for Odoo. That decision happens in
src/transform_odoo.py, after inspecting what actually came back.

Writes one JSON file per (service, entity_set) to output_raw/, named
<service_basename>__<entity_set>.json, containing:
  {"service": ..., "entity_set": ..., "declared_field_count": N, "row_count": M,
   "fields_present": [...], "rows": [...]}

Only pulls from SOURCES below - services/entity sets confirmed to exist on this tenant via
curl (see test_sap_endpoints.sh). Add to SOURCES as more are confirmed.

A source is (service, entity_set) or (service, entity_set, options), where options may carry:
  "expand": navigation property to pull inline and flatten (CRUD entities only)
  "select": an explicit field list, instead of "every field this entity declares"

"select" exists for wide OLAP reports that are being read for one specific thing. An analytics
report GROUPs BY whatever is selected, so pulling all ~227 columns of a G/L report and merging
them back together on the account number would write one arbitrary project/document per account
into the raw dump and present it as fact. Naming the fields keeps the dump honest.
"""

import json
import logging
import os
import sys

from src.config import load_config
from src.sap_client import SAPODataClient

logger = logging.getLogger("sap2odoo.extract_raw")

# (service, entity_set) pairs confirmed to exist on this tenant. bpm_businesspartnerdata_analytics.svc
# was curl-verified (test_sap_endpoints.sh); all 7 of its entity sets are pulled for completeness -
# not just the 3 originally hand-picked ones.
SOURCES = [
    ("sap/byd/odata/v1/costcentre", "CostCentreCollection"),  # Standard OData v1 Cost Center master data
    ("sap/byd/odata/cust/v1/khpurchaseorder", "PurchaseOrderCollection"),  # Custom service, 655 POs
    ("sap/byd/odata/cust/v1/khpurchaseorder", "ItemCollection"),  # PO line items, 1861 rows
    ("sap/byd/odata/cust/v1/khpurchaseorder", "SupplierCollection"),  # Links PO -> supplier BP (PartyID)
    ("sap/byd/odata/cust/v1/vmumaterial", "MaterialCollection"),  # Product master, 3058 materials
    ("sap/byd/odata/cust/v1/vmumaterialvaluationdata", "MaterialValuationDataCollection"),  # Material -> valuation link, 3021 rows
    ("sap/byd/odata/cust/v1/vmumaterialvaluationdata", "ValuationPriceCollection"),  # Cost price history, 1238 rows
    ("sap/byd/odata/bpm_businesspartnerdata_analytics.svc", "RPBUPCSD_Q0001QueryResults"),  # Account Details (customers)
    ("sap/byd/odata/bpm_businesspartnerdata_analytics.svc", "RPBUPSPP_Q0001QueryResults"),  # Supplier Details
    ("sap/byd/odata/bpm_businesspartnerdata_analytics.svc", "RPBUPATAXNUMBERS_Q0001QueryResults"),  # Tax Numbers
    ("sap/byd/odata/bpm_businesspartnerdata_analytics.svc", "RPBPCSCARB_Q0001QueryResults"),  # Account Collaboration Data
    ("sap/byd/odata/bpm_businesspartnerdata_analytics.svc", "RPBPCSCONTB_Q0001QueryResults"),  # Account Contact Data
    ("sap/byd/odata/bpm_businesspartnerdata_analytics.svc", "RPBPCSRSPB_Q0001QueryResults"),  # Account Responsibility Data
    ("sap/byd/odata/bpm_businesspartnerdata_analytics.svc", "RPBPCSRSPEMPTERM_Q0001QueryResults"),  # Accounts w/ term. resp employee

    # 12 more custom services the user imported - see PROJECT_STATUS.csv. khcustomerquote is
    # imported and live but blocked by SAP authorization restrictions for the SDK user
    # (RBAM_ERROR on data reads) - metadata snapshot saved, not in SOURCES until access is granted.
    ("sap/byd/odata/cust/v1/khsalesorder", "SalesOrderCollection"),  # 196 sales orders (mandatory #63)
    ("sap/byd/odata/cust/v1/khsalesorder", "ItemCollection"),
    ("sap/byd/odata/cust/v1/khsalesorder", "BuyerPartyCollection"),
    ("sap/byd/odata/cust/v1/khsalesorder", "ItemProductCollection"),
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "CustomerInvoiceCollection"),  # 524 customer invoices
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "ItemCollection"),
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "BuyerPartyCollection"),
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "SupplierInvoiceCollection"),  # 458 supplier invoices
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "ItemCollection"),
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "SellerPartyCollection"),
    ("sap/byd/odata/cust/v1/khopportunity", "OpportunityCollection"),  # 15 opportunities
    ("sap/byd/odata/cust/v1/khlocation", "LocationCollection"),  # 4 locations
    ("sap/byd/odata/cust/v1/khemployee", "EmployeeCollection"),  # 99 employees
    ("sap/byd/odata/cust/v1/khemployee", "WorkplaceAddressCollection"),
    ("sap/byd/odata/cust/v1/khhousebankstatement", "HouseBankStatementCollection"),  # 87 bank statements
    ("sap/byd/odata/cust/v1/khpayment", "PaymentCollection"),  # 544 payments (customer + vendor)
    ("sap/byd/odata/cust/v1/khsalesarrangement", "SalesArrangementCollection"),  # 35 pricing arrangements
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "OutboundDeliveryCollection"),  # 130 deliveries
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "ItemCollection"),
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "BuyerPartyCollection"),
    # MainProductOutput is expanded inline because MainProductOutputCollection can't be joined
    # from its own endpoint (no ParentObjectID, and its ObjectIDs don't match the order's) -
    # $expand is the only way to know which product a production order produces.
    ("sap/byd/odata/cust/v1/khproductionorder", "ProductionOrderCollection", {"expand": "MainProductOutput"}),  # 152 production orders
    ("sap/byd/odata/cust/v1/khproductionorder", "MainProductOutputCollection"),
    ("sap/byd/odata/cust/v1/khproductionorder", "OperationCollection"),

    # Found by re-checking already-imported services for unused-but-real master data entities -
    # no new file uploads needed for these 3 mandatory objects (#4, #29, #58).
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "CashDiscountTermsCollection"),  # Payment Terms, 513 rows (dedupe by code)
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "CashDiscountTermsCollection"),  # Payment Terms, supplier side
    ("sap/byd/odata/cust/v1/vmumaterial", "MaterialBaseMeasureUnitCodeCollection"),  # UOM, 23 real units
    ("sap/byd/odata/cust/v1/vmumaterial", "ProductCategoryCollection"),  # Product Categories, 3058 rows (dedupe by ID)

    # Full Products/Materials field coverage - vmumaterial has 78 entity sets total; these 3
    # carry real per-material data beyond MaterialCollection's own 18 fields (checked live,
    # ParentObjectID confirmed to join directly to MaterialCollection.ObjectID).
    ("sap/byd/odata/cust/v1/vmumaterial", "TextCollection"),  # 2197 rows - detailed descriptions
    ("sap/byd/odata/cust/v1/vmumaterial", "PurchasingCollection"),  # 2959 rows - purchasing UOM, purchase_ok signal
    ("sap/byd/odata/cust/v1/vmumaterial", "SalesCollection"),  # 979 rows - sales UOM, sale_ok signal

    # Completing full Products/Materials coverage - remaining real (non-empty) entities in
    # vmumaterial, checked live before adding (row counts confirmed 2026-09-18).
    ("sap/byd/odata/cust/v1/vmumaterial", "PlanningCollection"),  # 3054 rows - ProcurementTypeCode -> route_ids/id
    ("sap/byd/odata/cust/v1/vmumaterial", "IdentificationCollection"),  # 3058 rows - alternate product IDs
    ("sap/byd/odata/cust/v1/vmumaterial", "LogisticsCollection"),  # 3057 rows - site/logistics info
    ("sap/byd/odata/cust/v1/vmumaterial", "ValuationCollection"),  # 3021 rows - company/valuation status
    ("sap/byd/odata/cust/v1/vmumaterial", "AvailabilityConfirmationCollection"),  # 3056 rows - supply planning area
    ("sap/byd/odata/cust/v1/vmumaterial", "PlanningForecastGroupCollection"),  # 3058 rows - forecast grouping
    ("sap/byd/odata/cust/v1/vmumaterial", "QuantityConversionCollection"),  # 5 rows - unit conversions
    ("sap/byd/odata/cust/v1/vmumaterial", "DeviantTaxClassificationCollection"),  # 4 rows - per-country tax classification

    # =====================================================================================
    # Full coverage pass (2026-09-18). Every remaining entity set that the live tenant
    # publishes AND that actually holds rows, found by parsing the service definitions in
    # byd-api-samples-main/ (src/parse_service_defs.py), probing which services are live
    # (src/probe_services.py) and counting rows before pulling (src/probe_entity_sets.py).
    # Row counts below are the tenant's own $inlinecount at the time of that probe.
    #
    # Deliberately NOT listed: 29 entity sets the tenant reports as empty, and every entity
    # set on khcustomerquote / tmserviceorder / tmservicerequest, which return RBAM_ERROR
    # (the SDK user lacks authorisation for those work centers - an SAP role change, not a
    # service import). See schema_snapshots/entity_set_counts.json for the full evidence.
    # =====================================================================================

    # --- khcustomerinvoice: 9 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "InvolvedPartyCollection"),                     # 3144 rows, 6 fields
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "BuyerPartyFormattedAddressCollection"),        # 1420 rows, 2 fields
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "InvolvedPartyFormattedAddressCollection"),     # 1420 rows, 2 fields
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "BuyerPartyNameCollection"),                    # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "InvolvedPartyDisplayNameCollection"),          # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "ItemBusinessTransactionDocumentReferenceCollection"),# 870 rows, 7 fields
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "ItemPriceAndTaxCalculationCollection"),        # 623 rows, 8 fields
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "PriceAndTaxCalculationCollection"),            # 524 rows, 4 fields
    ("sap/byd/odata/cust/v1/khcustomerinvoice", "BusinessTransactionDocumentReferenceCollection"),# 31 rows, 7 fields

    # --- khemployee: 16 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/khemployee", "OrganisationalCentreAssignmentCollection"),           # 1710 rows, 18 fields
    ("sap/byd/odata/cust/v1/khemployee", "AssignedIdentityCollection"),                         # 557 rows, 9 fields
    ("sap/byd/odata/cust/v1/khemployee", "EmployeeTypeCollection"),                             # 184 rows, 5 fields
    ("sap/byd/odata/cust/v1/khemployee", "EmployeeAssignmentCollection"),                       # 90 rows, 12 fields
    ("sap/byd/odata/cust/v1/khemployee", "JobAssignmentCollection"),                            # 90 rows, 7 fields
    ("sap/byd/odata/cust/v1/khemployee", "PositionCollection"),                                 # 90 rows, 2 fields
    ("sap/byd/odata/cust/v1/khemployee", "CurrentFunctionalUnitCollection"),                    # 16 rows, 3 fields
    ("sap/byd/odata/cust/v1/khemployee", "JobNameByValidityCollection"),                        # 12 rows, 4 fields
    ("sap/byd/odata/cust/v1/khemployee", "WorkplaceAddressInformationCollection"),              # 7 rows, 3 fields
    ("sap/byd/odata/cust/v1/khemployee", "WorkplaceAddressFormattedAddressCollection"),         # 7 rows, 3 fields
    ("sap/byd/odata/cust/v1/khemployee", "CompanyNameCollection"),                              # 5 rows, 3 fields
    ("sap/byd/odata/cust/v1/khemployee", "CurrentCompanyCollection"),                           # 4 rows, 2 fields
    ("sap/byd/odata/cust/v1/khemployee", "ReportingLineUnitNameByValidityCollection"),          # 3 rows, 5 fields
    ("sap/byd/odata/cust/v1/khemployee", "BusinessResidenceNameCollection"),                    # 3 rows, 3 fields
    ("sap/byd/odata/cust/v1/khemployee", "CurrentBusinessResidenceCollection"),                 # 2 rows, 2 fields
    ("sap/byd/odata/cust/v1/khemployee", "CurrentReportingLineUnitCollection"),                 # 2 rows, 2 fields

    # --- khlocation: 1 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/khlocation", "LogisticsAreaCollection"),                            # 16 rows, 20 fields

    # --- khopportunity: 25 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyContactFormattedAddressCollection"),  # 1420 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyFormattedAddressCollection"),         # 1420 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ExternalPartyNameCollection"),                     # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "CompetitorNameCollection"),                        # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "SalesUnitNameCollection"),                         # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "EmployeeResponsibleNameCollection"),               # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyContactNameCollection"),              # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyNameCollection"),                     # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyPostalAddressCollection"),            # 740 rows, 8 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyContactTelephoneCollection"),         # 224 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyTelephoneCollection"),                # 224 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyContactEmailCollection"),             # 208 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyEmailCollection"),                    # 208 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "CompetitorWebSiteCollection"),                     # 43 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyWebSiteCollection"),                  # 43 rows, 2 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ExternalPartyCollection"),                         # 41 rows, 7 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyCollection"),                         # 41 rows, 7 fields
    ("sap/byd/odata/cust/v1/khopportunity", "CompetitorCollection"),                            # 41 rows, 5 fields
    ("sap/byd/odata/cust/v1/khopportunity", "SalesUnitCollection"),                             # 41 rows, 4 fields
    ("sap/byd/odata/cust/v1/khopportunity", "EmployeeResponsibleCollection"),                   # 41 rows, 4 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ItemCollection"),                                  # 16 rows, 14 fields
    ("sap/byd/odata/cust/v1/khopportunity", "SalesBusinessAreaCollection"),                     # 13 rows, 5 fields
    ("sap/byd/odata/cust/v1/khopportunity", "ProspectPartyContactCollection"),                  # 10 rows, 5 fields
    ("sap/byd/odata/cust/v1/khopportunity", "DocumentReferenceCollection"),                     # 3 rows, 14 fields
    ("sap/byd/odata/cust/v1/khopportunity", "CampaignCollection"),                              # 3 rows, 11 fields

    # --- khoutbounddelivery: 12 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "ProductRecipientFormattedAddressCollection"), # 1420 rows, 2 fields
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "ProductRecipientDisplayNameCollection"),      # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "ProductRecipientPartyCollection"),            # 520 rows, 4 fields
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "SellerPartyCollection"),                      # 520 rows, 3 fields
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "FreightForwarderPartyCollection"),            # 520 rows, 3 fields
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "ItemSalesOrderReferenceCollection"),          # 473 rows, 4 fields
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "ItemLogisticsRequestResponsiblePartyCollection"),# 471 rows, 3 fields
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "ShipFromLocationCollection"),                 # 260 rows, 3 fields
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "ArrivalPeriodCollection"),                    # 238 rows, 6 fields
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "ShippingPeriodCollection"),                   # 238 rows, 6 fields
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "PickupPeriodCollection"),                     # 238 rows, 4 fields
    ("sap/byd/odata/cust/v1/khoutbounddelivery", "ItemDeliveryQuantityCollection"),             # 157 rows, 4 fields

    # --- khpayment: 1 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/khpayment", "CompanyNameCollection"),                               # 5 rows, 2 fields

    # --- khproductionorder: 1 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/khproductionorder", "ProductionLotCollection"),                     # 145 rows, 2 fields

    # --- khpurchaseorder: 24 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/khpurchaseorder", "ItemShipToLocationCollection"),                  # 5571 rows, 5 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "ItemEndBuyerPartyCollection"),                   # 5533 rows, 3 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "ApproverPartyCollection"),                       # 3529 rows, 4 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "BuyerPartyCollection"),                          # 3529 rows, 4 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "BillToPartyCollection"),                         # 3529 rows, 4 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "EmployeeResponsibleCollection"),                 # 3529 rows, 4 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "PurchasingUnitCollection"),                      # 3529 rows, 4 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "ItemBusinessTransactionDocumentReferenceCollection"),# 2400 rows, 7 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "DeliveryAddressCollection"),                     # 1477 rows, 1 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "SupplierFormattedAddressCollection"),            # 1420 rows, 2 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "ApproverPartyNameCollection"),                   # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "BuyerPartyNameCollection"),                      # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "BillToPartyNameCollection"),                     # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "EmployeeResponsibleNameCollection"),             # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "PurchasingUnitNameCollection"),                  # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "SupplierNameCollection"),                        # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "DeliveryAddressNameCollection"),                 # 1367 rows, 6 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "BusinessTransactionDocumentReferenceCollection"),# 1351 rows, 7 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "DeliveryPostalAddressCollection"),               # 740 rows, 13 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "SupplierPostalAddressCollection"),               # 740 rows, 11 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "PaymentTermsCollection"),                        # 605 rows, 3 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "ItemAccountAssignmentDetailsCollection"),        # 284 rows, 31 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "ItemAccountAssignmentCollection"),               # 281 rows, 2 fields
    ("sap/byd/odata/cust/v1/khpurchaseorder", "NotesCollection"),                               # 112 rows, 11 fields

    # --- khsalesarrangement: 1 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/khsalesarrangement", "SalesOrganisationNameByValidityCollection"),  # 17 rows, 4 fields

    # --- khsalesorder: 23 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/khsalesorder", "ItemPriceComponentCollection"),                     # 3704 rows, 16 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "PriceComponentCollection"),                         # 2748 rows, 20 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ItemVendorPartyCollection"),                        # 2333 rows, 4 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ItemProductRecipientPartyCollection"),              # 2333 rows, 3 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ProductRecipientPartyCollection"),                  # 1557 rows, 6 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "SalesUnitPartyCollection"),                         # 1557 rows, 4 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ProductRecipientPartyDetailsCollection"),           # 1477 rows, 17 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ProductRecipientPartyFormattedAddressCollection"),  # 1420 rows, 3 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ItemVendorFormattedAddressCollection"),             # 1420 rows, 2 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "BuyerPartyNameCollection"),                         # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "SalesUnitPartyNameCollection"),                     # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ItemVendorNameCollection"),                         # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ProductRecipientPartyNameCollection"),              # 1367 rows, 6 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ProductRecipientPartyPostalAddressCollection"),     # 740 rows, 12 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ItemScheduleLineCollection"),                       # 516 rows, 7 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ItemShipFromLocationCollection"),                   # 515 rows, 3 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "RequestedFulfillmentPeriodCollection"),             # 361 rows, 6 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ItemPriceAndTaxCalculationCollection"),             # 260 rows, 10 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "ItemDocumentReferenceCollection"),                  # 201 rows, 9 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "PaymentControlCollection"),                         # 196 rows, 7 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "PricingTermsCollection"),                           # 196 rows, 6 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "PriceAndTaxCalculationCollection"),                 # 194 rows, 3 fields
    ("sap/byd/odata/cust/v1/khsalesorder", "DocumentReferenceCollection"),                      # 169 rows, 18 fields

    # --- khsupplierinvoice: 12 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "ItemConfirmedInboundDeliveryReferenceCollection"),# 4082 rows, 4 fields
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "ItemPurchaseOrderReferenceCollection"),        # 4082 rows, 4 fields
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "BuyerPartyCollection"),                        # 3256 rows, 3 fields
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "BillToPartyCollection"),                       # 3256 rows, 3 fields
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "PurchaseOrderReferenceCollection"),            # 1908 rows, 6 fields
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "ConfirmedInboundDeliveryReferenceCollection"), # 1908 rows, 6 fields
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "ExternalReferenceCollection"),                 # 1908 rows, 5 fields
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "SupplierInvoiceExceptionReferenceCollection"), # 1908 rows, 3 fields
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "SellerPartyAddressCollection"),                # 1420 rows, 2 fields
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "SellerPartyNameCollection"),                   # 1410 rows, 2 fields
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "ItemTaxCalculationCollection"),                # 777 rows, 7 fields
    ("sap/byd/odata/cust/v1/khsupplierinvoice", "SupplierInvoiceExceptionCollection"),          # 110 rows, 4 fields

    # --- vmumaterial: 4 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/vmumaterial", "SupplierInformationCollection"),                     # 34 rows, 8 fields
    ("sap/byd/odata/cust/v1/vmumaterial", "SalesOrganisationNameByValidityCollection"),         # 17 rows, 4 fields
    ("sap/byd/odata/cust/v1/vmumaterial", "CompanyCurrentNameCollection"),                      # 5 rows, 2 fields
    ("sap/byd/odata/cust/v1/vmumaterial", "BusinessResidenceCurrentNameCollection"),            # 3 rows, 2 fields

    # --- vmumaterialvaluationdata: 3 additional entity sets with real data ---
    ("sap/byd/odata/cust/v1/vmumaterialvaluationdata", "ValuationLevelCollection"),             # 3021 rows, 13 fields
    ("sap/byd/odata/cust/v1/vmumaterialvaluationdata", "AccountDeterminationSpecificationCollection"),# 3021 rows, 2 fields
    ("sap/byd/odata/cust/v1/vmumaterialvaluationdata", "InventoryValuationSpecificationCollection"),# 3021 rows, 2 fields


    # =====================================================================================
    # Analytics reports (2026-09-18). Found via the tenant's own live service catalog at
    # GET {SAP_BASE_URL}/sap/byd/odata/ - see src/discover_catalog.py. That catalog lists 48
    # services and 1485 entity sets; SERVICE_CATALOG.csv is the readable version. Two earlier
    # rounds of guessing service names scored 0/100 on the assumption that no catalog existed.
    #
    # Field lists come from schema_snapshots/<service>.metadata.xml, captured by
    # src/snapshot_metadata.py. $metadata collapses to ID + TotaledProperties on the catch-all
    # ana_businessanalytics_analytics.svc, but resolves properly on the per-module services -
    # so every report below is addressed through its module service, not the catch-all.
    # =====================================================================================

    # --- Chart of Accounts (#1, MANDATORY) ------------------------------------------------
    # The dedicated "G/L Account Master Data" report (RPFINGLAU17) has exactly the right
    # fields but declares a mandatory Chart of Accounts variable with no default, so it
    # returns 0 rows and rejects a filter unless given a chart-of-accounts key this tenant
    # does not expose. These 6 reports each return the accounts actually used in their own
    # area, and together cover all 122 accounts in use - see src/probe_gl_accounts.py, which
    # tested all 60 reports that declare CGLACCT/TGLACCT and computed this minimal cover.
    ("sap/byd/odata/fin_costandrevenue_analytics.svc", "RPFINCACU04_Q0002QueryResults", {"select": ["CGLACCT", "TGLACCT"]}),   # 49 accounts
    ("sap/byd/odata/fin_audit_analytics.svc", "RPFINGLAU02_Q0002QueryResults", {"select": ["CGLACCT", "TGLACCT"]}),            # 35 accounts
    ("sap/byd/odata/fin_generalledger_analytics.svc", "RPFINFXAU05_Q0001QueryResults", {"select": ["CGLACCT", "TGLACCT"]}),    # 19 accounts
    ("sap/byd/odata/fin_generalledger_analytics.svc", "RPFINFCDU02_Q0001QueryResults", {"select": ["CGLACCT", "TGLACCT"]}),    # 14 accounts
    ("sap/byd/odata/fin_audit_analytics.svc", "RPFININVU03_Q0001QueryResults", {"select": ["CGLACCT", "TGLACCT"]}),            # 4 accounts
    ("sap/byd/odata/fin_audit_analytics.svc", "RPFINGLAU02_Q0003QueryResults", {"select": ["CGLACCT", "TGLACCT"]}),            # 1 account

    # --- Taxes (#2, MANDATORY) ------------------------------------------------------------
    # "Taxes - Product Tax Details" carries the tax rate percentage itself, which no custom
    # service exposes - khcustomerinvoice/khsupplierinvoice only give the tax CODE per line.
    # Explicit select for two reasons: the report has 68 dimensions (well past the drill-down
    # limit), and its first UUID-bearing field is a CUSTOMER UUID, so an automatic chunk-merge
    # would collapse every tax line onto one row per customer. Selecting just the tax-defining
    # dimensions makes the report's GROUP BY do the right thing - one row per distinct tax.
    ("sap/byd/odata/fin_taxmanagement_analytics.svc", "RPGLOTAXB01_Q0001QueryResults", {"select": [
        "CRESULT_TAX_TYPE", "CRESULT_TAX_EVENT", "CPRODTAX_RATE_PERCENT", "CTRP_TAX_TYPE",
        "CCIV_LOCATION_REGION", "CTAX_DEDUCTIBLE", "CPRODUCT_TYPE",
    ]}),

    # --- Inventory balances (#31) ---------------------------------------------------------
    # Same reasoning as the tax report: 69 fields, and the first UUID-bearing field is a
    # product-category UUID, which would collapse 1978 stock lines onto a handful of rows.
    # Grouping by material x logistics area x site is exactly Odoo's stock.quant grain, and
    # the T* fields carry the human-readable name of each C* key.
    ("sap/byd/odata/scm_physicalinventory_analytics.svc", "RPSCMINBU03_Q0001QueryResults", {"select": [
        "CMATERIAL_UUID", "TMATERIAL_UUID", "CLOG_AREA_UUID", "TLOG_AREA_UUID",
        "CSITE_UUID", "TSITE_UUID", "CINV_UNIT", "FCENDING_QUANTITY",
    ]}),

    # --- Product / service master cross-check (#57) ---------------------------------------
    # RPSERVICE is the SERVICE product master - a separate master from vmumaterial's
    # materials, and missing from product_template.csv entirely until now.
    ("sap/byd/odata/pmm_productdata_analytics.svc", "RPSERVICE_Q0001QueryResults"),


    # --- Open Customer Invoices (#8, MANDATORY) -------------------------------------------
    # The "Trade Receivables Payables Register" is ByDesign's open-items report: one row per
    # still-unsettled invoice, with the invoice number, the partner and the outstanding amount.
    # This is what the posted-invoice service could never answer - khcustomerinvoice has no
    # payment-status field at all, and matching payments to invoices by ID only ever hit 7%.
    # Explicit select: the report has 131 columns, mostly ageing buckets, and >11 dimensions at
    # once trips TOO_MANY_DRILL_DOWN_OBJECTS.
    ("sap/byd/odata/fin_receivablesar_analytics.svc", "RPFINDUEU04_Q0007QueryResults", {"select": [
        "CIM_BP_UUID", "CIM_B_BTD_ID", "CIM_B_BTD_DATE", "CIM_TRANSCURR", "CCOMP_UUID",
        "FCOUTSTANDING_AMNT", "FCOVEDUE_AMNT",
    ]}),
    # Payables side of the same register - open vendor bills (#9 is already built from posted
    # supplier invoices; this adds the outstanding balance the posted data cannot give).
    ("sap/byd/odata/fin_payablesap_analytics.svc", "RPFINDUEU04_Q0004QueryResults", {"select": [
        "CIM_BP_UUID", "CIM_B_BTD_ID", "CIM_B_BTD_DATE", "CIM_TRANSCURR", "CCOMP_UUID",
        "FCOUTSTANDING_AMNT", "FCOVEDUE_AMNT",
    ]}),

]


def raw_filename(service, entity_set):
    basename = service.rsplit("/", 1)[-1]
    return f"{basename}__{entity_set}.json"


def extract_all(client, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    summary = []

    for source in SOURCES:
        # A source is (service, entity_set) or (service, entity_set, options) - see SOURCES.
        service, entity_set = source[0], source[1]
        options = source[2] if len(source) > 2 else {}
        expand, select = options.get("expand"), options.get("select")
        detail = f" (expand={expand})" if expand else f" (select={len(select)} fields)" if select else ""
        logger.info("Raw extracting %s/%s%s ...", service, entity_set, detail)
        try:
            if select:
                rows, declared_fields = client.get_entity_set(service, entity_set, select=select), select
            else:
                rows, declared_fields = client.get_entity_set_all_fields(service, entity_set, expand=expand)
        except Exception:
            logger.exception("FAILED raw extraction: %s/%s", service, entity_set)
            summary.append((service, entity_set, "FAILED", 0))
            continue

        fields_present = sorted({k for row in rows for k in row if k != "__metadata"})
        payload = {
            "service": service,
            "entity_set": entity_set,
            "declared_field_count": len(declared_fields),
            "row_count": len(rows),
            "fields_present": fields_present,
            "rows": [{k: v for k, v in row.items() if k != "__metadata"} for row in rows],
        }

        path = os.path.join(output_dir, raw_filename(service, entity_set))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)

        logger.info("Wrote %s (%d rows, %d columns)", path, len(rows), len(fields_present))
        summary.append((service, entity_set, "OK", len(rows)))

    return summary


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    client = SAPODataClient(config)
    summary = extract_all(client, "output_raw")

    logger.info("=== Raw extraction summary ===")
    for service, entity_set, status, count in summary:
        logger.info("%-70s %-8s %d rows", f"{service.rsplit('/', 1)[-1]}/{entity_set}", status, count)


if __name__ == "__main__":
    sys.exit(main())
