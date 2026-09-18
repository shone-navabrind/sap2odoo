#!/usr/bin/env bash
# Curl-based smoke tests against the real SAP ByDesign tenant, used to confirm which
# OData services/entities/fields actually work BEFORE wiring them into Python extractors.
# Run this any time a new candidate service is found - it's cheap, fast feedback that
# doesn't require touching src/.
#
# Usage: ./test_sap_endpoints.sh

set -euo pipefail
cd "$(dirname "$0")"
source .env

pass=0
fail=0

check() {
  local description="$1"
  local url="$2"
  local expect_substr="$3"

  local body
  local code
  body=$(curl -s -w "\n%{http_code}" -u "${SAP_USERNAME}:${SAP_PASSWORD}" -H "Accept: application/json" "$url")
  code=$(echo "$body" | tail -1)
  body=$(echo "$body" | sed '$d')

  if [[ "$code" == "200" ]] && [[ "$body" == *"$expect_substr"* ]]; then
    echo "PASS  $description (HTTP $code)"
    pass=$((pass + 1))
  else
    echo "FAIL  $description (HTTP $code)"
    echo "      $url"
    echo "      ${body:0:300}"
    fail=$((fail + 1))
  fi
}

echo "=== bpm_businesspartnerdata_analytics.svc ==="

check "Account Details (customers) returns data" \
  "${SAP_BASE_URL}/sap/byd/odata/bpm_businesspartnerdata_analytics.svc/RPBUPCSD_Q0001QueryResults?\$top=3&\$select=CBP_UUID,TBP_UUID,CSTREET_NAME,CCITY_NAME,CSTREET_POSTAL,CCOUNTRY_CODE,CCURRENCY_CODE,CEMAIL_URI,CPHONE_NR&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Account Details total count is present" \
  "${SAP_BASE_URL}/sap/byd/odata/bpm_businesspartnerdata_analytics.svc/RPBUPCSD_Q0001QueryResults?\$top=1&\$select=CBP_UUID&\$inlinecount=allpages&sap-client=${SAP_CLIENT}" \
  '"__count"'

check "Supplier Details returns data" \
  "${SAP_BASE_URL}/sap/byd/odata/bpm_businesspartnerdata_analytics.svc/RPBUPSPP_Q0001QueryResults?\$top=3&\$select=CBP_UUID,TBP_UUID,CCITY_NAME&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Tax Numbers returns data" \
  "${SAP_BASE_URL}/sap/byd/odata/bpm_businesspartnerdata_analytics.svc/RPBUPATAXNUMBERS_Q0001QueryResults?\$top=3&\$select=CBUPA_UUID,CTAX_NUMBER&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

echo
echo "=== cust/v1/khpurchaseorder (custom OData service, Cloud Applications Studio) ==="

check "Purchase Order header data returns" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khpurchaseorder/PurchaseOrderCollection?\$top=3&\$select=ObjectID,ID,CurrencyCode,TotalNetAmount&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Purchase Order item data returns" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khpurchaseorder/ItemCollection?\$top=3&\$select=ObjectID,ParentObjectID,Description,Quantity&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Purchase Order supplier link returns" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khpurchaseorder/SupplierCollection?\$top=3&\$select=ParentObjectID,PartyID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

echo
echo "=== cust/v1/vmumaterial + vmumaterialvaluationdata (custom OData services) ==="

check "Material master data returns" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/vmumaterial/MaterialCollection?\$top=3&\$select=ObjectID,UUID,InternalID,Description&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Material valuation link returns" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/vmumaterialvaluationdata/MaterialValuationDataCollection?\$top=3&\$select=ObjectID,MaterialUUID,MateriallID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Valuation price history returns" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/vmumaterialvaluationdata/ValuationPriceCollection?\$top=3&\$select=ParentObjectID,Amount,StartDate&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Material detailed descriptions (TextCollection) return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/vmumaterial/TextCollection?\$top=3&\$select=ParentObjectID,Text,TypeCode&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Material purchasing data returns" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/vmumaterial/PurchasingCollection?\$top=3&\$select=ParentObjectID,PurchasingMeasureUnitCode&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Material sales data returns" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/vmumaterial/SalesCollection?\$top=3&\$select=ParentObjectID,SalesMeasureUnitCode&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Material planning data (procurement type) returns" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/vmumaterial/PlanningCollection?\$top=3&\$select=ParentObjectID,ProcurementTypeCode&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

echo
echo "=== 12 more cust/v1 custom services (user-imported, 2026-09-15) ==="

check "Sales Orders return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khsalesorder/SalesOrderCollection?\$top=2&\$select=ObjectID,ID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Customer Invoices return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khcustomerinvoice/CustomerInvoiceCollection?\$top=2&\$select=ObjectID,ID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Supplier Invoices return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khsupplierinvoice/SupplierInvoiceCollection?\$top=2&\$select=ObjectID,ID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Opportunities return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khopportunity/OpportunityCollection?\$top=2&\$select=ObjectID,ID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Locations return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khlocation/LocationCollection?\$top=2&\$select=ObjectID,ID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Employees return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khemployee/EmployeeCollection?\$top=2&\$select=ObjectID,EmployeeID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "House Bank Statements return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khhousebankstatement/HouseBankStatementCollection?\$top=2&\$select=ObjectID,ID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Payments return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khpayment/PaymentCollection?\$top=2&\$select=ObjectID,DocumentID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Sales Arrangements return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khsalesarrangement/SalesArrangementCollection?\$top=2&\$select=ObjectID,CustomerUUID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Outbound Deliveries return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khoutbounddelivery/OutboundDeliveryCollection?\$top=2&\$select=ObjectID,ID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Production Orders return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khproductionorder/ProductionOrderCollection?\$top=2&\$select=ObjectID,ID&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

echo
echo "=== 3 more mandatory objects found inside already-imported services (no new upload) ==="

check "Payment Terms (customer invoice side) return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khcustomerinvoice/CashDiscountTermsCollection?\$top=3&\$select=ObjectID,PaymentTermsCode&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Payment Terms (supplier invoice side) return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khsupplierinvoice/CashDiscountTermsCollection?\$top=3&\$select=ObjectID,Code&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "UOM codelist returns data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/vmumaterial/MaterialBaseMeasureUnitCodeCollection?\$top=3&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

check "Product Categories return data" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/vmumaterial/ProductCategoryCollection?\$top=3&\$select=ObjectID,ProductCategoryInternalID,Description&sap-client=${SAP_CLIENT}" \
  '"d":{"results":['

echo
echo "=== khcustomerquote: imported but blocked by SAP authorization for this user (expected to fail) ==="
curl -s -u "${SAP_USERNAME}:${SAP_PASSWORD}" \
  "${SAP_BASE_URL}/sap/byd/odata/cust/v1/khcustomerquote/CustomerQuoteCollection?\$top=1&sap-client=${SAP_CLIENT}" \
  | grep -q "RBAM_ERROR" && echo "  Confirmed still blocked (RBAM_ERROR) - needs broader authorization for SDK user" \
  || echo "  NOTE: no longer blocked - re-run this script's check-based test for it and wire it up"

check "Service catalog lists the tenant's OData services" \
  "${SAP_BASE_URL}/sap/byd/odata/" \
  "fin_generalledger_analytics.svc"

check "Chart of Accounts source returns G/L account numbers" \
  "${SAP_BASE_URL}/sap/byd/odata/fin_generalledger_analytics.svc/RPFINFXAU05_Q0001QueryResults?\$select=CGLACCT,TGLACCT&\$top=1&\$format=json" \
  "CGLACCT"

check "Inventory balance report returns data" \
  "${SAP_BASE_URL}/sap/byd/odata/scm_physicalinventory_analytics.svc/RPSCMINBU03_Q0001QueryResults?\$top=1&\$inlinecount=allpages&\$format=json" \
  "__count"

echo
echo "=== Service discovery (informational) ==="
echo "  The tenant publishes its own catalog - no name guessing needed:"
echo "    GET \${SAP_BASE_URL}/sap/byd/odata/          # every service this user may call"
echo "    GET \${SAP_BASE_URL}/sap/byd/odata/<svc>/    # that service's entity sets"
echo "  python -m src.discover_catalog rebuilds schema_snapshots/service_catalog.json from these,"
echo "  and python -m src.catalog_report writes the readable SERVICE_CATALOG.csv."

echo
echo "=== $pass passed, $fail failed ==="
[[ $fail -eq 0 ]]
