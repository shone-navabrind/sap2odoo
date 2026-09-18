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
    ("sap/byd/odata/cust/v1/khproductionorder", "ProductionOrderCollection", "MainProductOutput"),  # 152 production orders
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
]


def raw_filename(service, entity_set):
    basename = service.rsplit("/", 1)[-1]
    return f"{basename}__{entity_set}.json"


def extract_all(client, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    summary = []

    for source in SOURCES:
        # A source is (service, entity_set) or (service, entity_set, expand) - see SOURCES.
        service, entity_set = source[0], source[1]
        expand = source[2] if len(source) > 2 else None
        logger.info("Raw extracting %s/%s%s ...", service, entity_set, f" (expand={expand})" if expand else "")
        try:
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
