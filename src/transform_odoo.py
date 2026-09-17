"""
Stage 2: read Stage 1's raw dumps (output_raw/*.json) and produce Odoo-ready CSVs
(output_odoo/*.csv). Runs entirely offline against files already on disk - no SAP calls here.

Field mapping decisions below reference actual column names confirmed present in output_raw/
(see each raw file's "fields_present" list), not a pre-guessed schema.
"""

import csv
import json
import logging
import os
import sys

from src.csv_writer import external_id, write_csv as _write_csv
from src.sap_client import parse_sap_date

logger = logging.getLogger("sap2odoo.transform_odoo")

RAW_DIR = "output_raw"
ODOO_DIR = "output_odoo"


def load_raw(entity_set, service_hint=None):
    """
    Load a raw JSON file by entity set name. Several services share entity set names
    (ItemCollection, BuyerPartyCollection, etc.) - pass service_hint (the service's basename,
    e.g. "khsalesorder") to disambiguate. Without a hint, the first filename match wins, which
    is only safe when the entity set name is unique across all of SOURCES.
    """
    for filename in os.listdir(RAW_DIR):
        if not filename.endswith(f"__{entity_set}.json"):
            continue
        if service_hint and not filename.startswith(f"{service_hint}__"):
            continue
        with open(os.path.join(RAW_DIR, filename), encoding="utf-8") as f:
            return json.load(f)
    raise FileNotFoundError(
        f"No raw file for entity set '{entity_set}'"
        + (f" (service_hint={service_hint!r})" if service_hint else "")
        + f" in {RAW_DIR}/ - run `python -m src.extract_raw` first."
    )


def write_csv(filename, rows, fieldnames):
    path = _write_csv(ODOO_DIR, filename, rows, fieldnames)
    logger.info("Wrote %s (%d rows)", path, len(rows))
    return path


def _country_ref(code):
    code = (code or "").strip().lower()
    return f"base.{code}" if code else ""


def _bool(value):
    return "False" if value in (True, "true", "True") else "True"


PARTNER_FIELDNAMES = [
    "id", "name", "street", "city", "zip", "country_id/id", "phone", "email", "website",
    "vat", "customer_rank", "supplier_rank", "active",
]

BANK_FIELDNAMES = ["id", "name", "country_id/id"]
PARTNER_BANK_FIELDNAMES = ["id", "partner_id/id", "bank_id/id", "acc_number"]
ANALYTIC_PLAN_FIELDNAMES = ["id", "name"]
COST_CENTER_FIELDNAMES = ["id", "name", "code", "plan_id/id"]


def build_cost_centers():
    """Map ByDesign CostCentreCollection records to Odoo analytic accounts."""
    cost_centers = load_raw("CostCentreCollection")["rows"]
    plan_id = "sap_analytic_plan_cost_centers"

    plan_rows = [{"id": plan_id, "name": "SAP Cost Centers"}]
    rows = []
    for cost_center in cost_centers:
        key = cost_center.get("UUID") or cost_center.get("ObjectID") or cost_center.get("ID")
        if not key:
            continue
        code = cost_center.get("ID", "")
        rows.append(
            {
                "id": external_id("sap_cost_center", key),
                "name": cost_center.get("MostRecentDefaultName") or code or str(key),
                "code": code,
                "plan_id/id": plan_id,
            }
        )

    write_csv("account_analytic_plan.csv", plan_rows, ANALYTIC_PLAN_FIELDNAMES)
    write_csv("account_analytic_account_cc.csv", rows, COST_CENTER_FIELDNAMES)
    return {
        "account_analytic_plan.csv": len(plan_rows),
        "account_analytic_account_cc.csv": len(rows),
    }


def build_res_partner():
    """
    Sources (confirmed columns, see output_raw/*__RPBUPCSD*.json / *__RPBUPSPP*.json /
    *__RPBUPATAXNUMBERS*.json for the full field list each carries):
      RPBUPCSD_Q0001QueryResults          - customer-side accounts (44 on this tenant)
      RPBUPSPP_Q0001QueryResults          - supplier-side accounts (229)
      RPBUPATAXNUMBERS_Q0001QueryResults  - tax numbers keyed by CBUPA_UUID/CBP_UUID (66)
    """
    accounts = load_raw("RPBUPCSD_Q0001QueryResults")["rows"]
    suppliers = load_raw("RPBUPSPP_Q0001QueryResults")["rows"]
    tax_numbers = load_raw("RPBUPATAXNUMBERS_Q0001QueryResults")["rows"]

    vat_by_bp = {}
    for t in tax_numbers:
        bp_id = t.get("CBUPA_UUID")
        if bp_id and bp_id not in vat_by_bp:
            vat_by_bp[bp_id] = t.get("CTAX_NUMBER", "")

    rows_by_bp = {}
    bank_rows = []
    seen_banks = {}

    def add_bank(bp_id, bank_name, account_id, iban, country_code):
        if not (bank_name or account_id or iban):
            return
        bank_key = (bank_name or "").strip() or "UNKNOWN_BANK"
        bank_ext_id = seen_banks.get(bank_key)
        if not bank_ext_id:
            bank_ext_id = external_id("sap_bank", bank_key)
            seen_banks[bank_key] = bank_ext_id
        bank_rows.append(
            {
                "bank": {
                    "id": bank_ext_id,
                    "name": bank_name or "Unknown Bank",
                    "country_id/id": _country_ref(country_code),
                },
                "partner_bank": {
                    "id": external_id("sap_pbank", f"{bp_id}_{account_id or iban}"),
                    "partner_id/id": external_id("sap_bp", bp_id),
                    "bank_id/id": bank_ext_id,
                    "acc_number": iban or account_id or "",
                },
            }
        )

    for acc in accounts:
        bp_id = acc.get("CBP_UUID")
        if not bp_id:
            continue
        rows_by_bp[bp_id] = {
            "id": external_id("sap_bp", bp_id),
            "name": acc.get("TBP_UUID") or bp_id,
            "street": acc.get("CSTREET_NAME", ""),
            "city": acc.get("CCITY_NAME", ""),
            "zip": acc.get("CSTREET_POSTAL", ""),
            "country_id/id": _country_ref(acc.get("CCOUNTRY_CODE")),
            "phone": acc.get("CPHONE_NR", ""),
            "email": acc.get("CEMAIL_URI", ""),
            "website": acc.get("CWEB_URI", ""),
            "vat": vat_by_bp.get(bp_id, ""),
            "customer_rank": "1",
            "supplier_rank": "0",
            "active": _bool(acc.get("CDELIVERY_BLOCK")) if acc.get("CDELIVERY_BLOCK") else "True",
        }
        add_bank(bp_id, acc.get("CBANK_NAME"), acc.get("CBANK_ACCOUNT_ID"), acc.get("CBANK_IBAN"), acc.get("CBANK_NAT_COUNTRY"))

    for sup in suppliers:
        bp_id = sup.get("CBP_UUID")
        if not bp_id:
            continue
        if bp_id in rows_by_bp:
            rows_by_bp[bp_id]["supplier_rank"] = "1"
            if not rows_by_bp[bp_id]["phone"]:
                rows_by_bp[bp_id]["phone"] = sup.get("CPHONE_NR", "")
            if not rows_by_bp[bp_id]["website"]:
                rows_by_bp[bp_id]["website"] = sup.get("CWEB_URI", "")
        else:
            rows_by_bp[bp_id] = {
                "id": external_id("sap_bp", bp_id),
                "name": sup.get("TBP_UUID") or bp_id,
                "street": sup.get("CSTREET_NAME", ""),
                "city": sup.get("CCITY_NAME", ""),
                "zip": sup.get("CSTREET_POSTAL", ""),
                "country_id/id": _country_ref(sup.get("CCOUNTRY_CODE")),
                "phone": sup.get("CPHONE_NR", ""),
                "email": sup.get("CEMAIL_URI", ""),
                "website": sup.get("CWEB_URI", ""),
                "vat": vat_by_bp.get(bp_id, ""),
                "customer_rank": "0",
                "supplier_rank": "1",
                "active": "True",
            }
        add_bank(bp_id, sup.get("CBANK_NAME"), sup.get("CBANK_ACCOUNT_ID"), sup.get("CBANK_IBAN"), sup.get("CBANK_NAT_COUNTRY"))

    write_csv("res_partner.csv", list(rows_by_bp.values()), PARTNER_FIELDNAMES)

    unique_banks = {}
    unique_partner_banks = []
    for entry in bank_rows:
        unique_banks[entry["bank"]["id"]] = entry["bank"]
        unique_partner_banks.append(entry["partner_bank"])
    write_csv("res_bank.csv", list(unique_banks.values()), BANK_FIELDNAMES)
    write_csv("res_partner_bank.csv", unique_partner_banks, PARTNER_BANK_FIELDNAMES)

    return {
        "res_partner.csv": len(rows_by_bp),
        "res_bank.csv": len(unique_banks),
        "res_partner_bank.csv": len(unique_partner_banks),
    }


PO_HEADER_FIELDNAMES = ["id", "name", "partner_id/id", "date_order", "state", "currency_id/id", "amount_total"]
PO_LINE_FIELDNAMES = ["id", "order_id/id", "product_id/id", "name", "product_qty", "price_unit"]


def build_purchase_orders():
    """
    Source: khpurchaseorder custom OData service (Cloud Applications Studio custom BO,
    imported by the user from byd-api-samples-main/Custom OData Services/khpurchaseorder.xml).
    Real CRUD OData, not the analytics/BI kind - see output_raw/khpurchaseorder__*.json for the
    full confirmed field lists.

    PurchaseOrderCollection - 655 header rows, keyed by ObjectID, human PO number in "ID"
    ItemCollection          - 1861 line rows, ParentObjectID -> header ObjectID
    SupplierCollection      - links header ObjectID -> supplier business partner "PartyID"
                               (matches CBP_UUID in res_partner.csv's external IDs)
    """
    headers = load_raw("PurchaseOrderCollection")["rows"]
    items = load_raw("ItemCollection", service_hint="khpurchaseorder")["rows"]
    suppliers = load_raw("SupplierCollection")["rows"]

    supplier_by_po = {}
    for sup in suppliers:
        po_object_id = sup.get("ParentObjectID")
        if po_object_id and po_object_id not in supplier_by_po:
            supplier_by_po[po_object_id] = sup.get("PartyID")

    header_rows = []
    known_po_ids = set()
    for header in headers:
        object_id = header.get("ObjectID")
        if not object_id:
            continue
        known_po_ids.add(object_id)
        partner_bp = supplier_by_po.get(object_id)
        header_rows.append(
            {
                "id": external_id("sap_po", object_id),
                "name": header.get("ID", object_id),
                "partner_id/id": external_id("sap_bp", partner_bp) if partner_bp else "",
                "date_order": parse_sap_date(header.get("CreationDateTime")),
                # LifeCycleStatusCode values aren't a documented open/closed mapping for this
                # tenant - these are real historical SAP orders, so treated as confirmed.
                "state": "purchase",
                "currency_id/id": (
                    f"base.{header.get('CurrencyCode', '').strip().upper()}"
                    if header.get("CurrencyCode") else ""
                ),
                "amount_total": header.get("TotalNetAmount", "") or "0",
            }
        )

    line_rows = []
    for item in items:
        po_object_id = item.get("ParentObjectID")
        if po_object_id not in known_po_ids:
            continue
        product_id = item.get("ProductID")
        line_rows.append(
            {
                "id": external_id("sap_po_item", item.get("ObjectID")),
                "order_id/id": external_id("sap_po", po_object_id),
                # Products (#57 in the registry) aren't wired up yet - this reference will only
                # resolve once product_template.csv exists with matching sap_prod_* external IDs.
                "product_id/id": external_id("sap_prod", product_id) if product_id else "",
                "name": item.get("Description") or product_id or item.get("ID", ""),
                "product_qty": item.get("Quantity", "") or "0",
                "price_unit": item.get("NetUnitPriceAmount", "") or "0",
            }
        )

    write_csv("purchase_order.csv", header_rows, PO_HEADER_FIELDNAMES)
    write_csv("purchase_order_line.csv", line_rows, PO_LINE_FIELDNAMES)
    return {
        "purchase_order.csv": len(header_rows),
        "purchase_order_line.csv": len(line_rows),
    }


PRODUCT_FIELDNAMES = [
    "id", "name", "default_code", "description", "type", "categ_id/id", "uom_id/id",
    "uom_po_id/id", "standard_price", "purchase_ok", "sale_ok", "active",
]


def build_products():
    """
    Full field coverage for Products/Materials (#57, mandatory), from ALL of vmumaterial's
    per-material entities confirmed to carry real data on this tenant (checked all 78 entity
    sets in vmumaterial's metadata; most are empty codelists or unrelated org data - these are
    the ones with real rows, each confirmed to join via ParentObjectID -> Material.ObjectID):

      MaterialCollection                    - 3058 materials, the base 18 fields
      TextCollection (TypeCode 10006)        - 2197 rows, "Detailed Description" text
      PurchasingCollection                   - 2959 rows, purchasing UOM -> purchase_ok signal
      SalesCollection                        - 979 rows, sales UOM -> sale_ok signal
      ProductCategoryCollection              - 3058 rows, one category per material
      vmumaterialvaluationdata/MaterialValuationDataCollection + ValuationPriceCollection
                                             - links Material.UUID -> latest cost price

    uom_id/id, uom_po_id/id, categ_id/id reference this same pipeline's own uom_uom.csv /
    product_category.csv external IDs (built from the same SAP codes, so they resolve).
    default_code uses InternalID, matching ProductID values already referenced in
    purchase_order_line.csv (e.g. sap_prod_1) - wiring this up resolves those product_id/id links.
    standard_price picks each material's ValuationPrice row with the latest StartDate (most
    recent = current cost), not filtered by currency/type - single-currency tenant assumption,
    documented as a known limitation if that's wrong.

    NOT available on this tenant (checked live, zero rows): GlobalTradeItemNumberCollection
    (barcode/GTIN), SalesTextCollection/PurchasingTextCollection (channel-specific descriptions),
    QuantityCharacteristicCollection (would have carried weight/dimensions if populated),
    CustomerInformationCollection. No weight/barcode fields are fabricated - left blank.
    """
    materials = load_raw("MaterialCollection")["rows"]
    valuation_data = load_raw("MaterialValuationDataCollection")["rows"]
    prices = load_raw("ValuationPriceCollection")["rows"]
    texts = load_raw("TextCollection", service_hint="vmumaterial")["rows"]
    purchasing = load_raw("PurchasingCollection")["rows"]
    sales = load_raw("SalesCollection")["rows"]
    categories = load_raw("ProductCategoryCollection")["rows"]

    prices_by_valuation_id = {}
    for price in prices:
        parent = price.get("ParentObjectID")
        if not parent:
            continue
        prices_by_valuation_id.setdefault(parent, []).append(price)

    price_by_material_uuid = {}
    for vd in valuation_data:
        material_uuid = vd.get("MaterialUUID")
        valuation_object_id = vd.get("ObjectID")
        if not material_uuid or not valuation_object_id:
            continue
        candidates = prices_by_valuation_id.get(valuation_object_id, [])
        if not candidates:
            continue
        latest = max(candidates, key=lambda p: p.get("StartDate") or "")
        price_by_material_uuid[material_uuid] = latest.get("Amount", "")

    description_by_material = {}
    for t in texts:
        if t.get("TypeCode") == "10006" and t.get("ParentObjectID") not in description_by_material:
            description_by_material[t["ParentObjectID"]] = t.get("Text", "")

    purchase_uom_by_material = {}
    for p in purchasing:
        parent = p.get("ParentObjectID")
        if parent and parent not in purchase_uom_by_material:
            purchase_uom_by_material[parent] = p.get("PurchasingMeasureUnitCode", "")

    sale_uom_by_material = {}
    for s in sales:
        parent = s.get("ParentObjectID")
        if parent and parent not in sale_uom_by_material:
            sale_uom_by_material[parent] = s.get("SalesMeasureUnitCode", "")

    category_by_material = {}
    for c in categories:
        parent = c.get("ParentObjectID")
        if parent:
            category_by_material[parent] = c.get("ProductCategoryInternalID", "")

    rows = []
    for material in materials:
        object_id = material.get("ObjectID")
        if not object_id:
            continue
        material_uuid = material.get("UUID")
        base_uom = material.get("BaseMeasureUnitCode", "")
        purchase_uom = purchase_uom_by_material.get(object_id)
        category_code = category_by_material.get(object_id)
        rows.append(
            {
                "id": external_id("sap_prod", material.get("InternalID") or object_id),
                "name": material.get("Description") or material.get("InternalID") or object_id,
                "default_code": material.get("InternalID", ""),
                "description": description_by_material.get(object_id, ""),
                "type": "consu",
                "categ_id/id": external_id("sap_prodcat", category_code) if category_code else "",
                "uom_id/id": external_id("sap_uom", base_uom) if base_uom else "",
                "uom_po_id/id": external_id("sap_uom", purchase_uom) if purchase_uom else (
                    external_id("sap_uom", base_uom) if base_uom else ""
                ),
                "standard_price": price_by_material_uuid.get(material_uuid, ""),
                "purchase_ok": "True" if object_id in purchase_uom_by_material else "False",
                "sale_ok": "True" if object_id in sale_uom_by_material else "False",
                "active": "True",
            }
        )

    write_csv("product_template.csv", rows, PRODUCT_FIELDNAMES)
    return {"product_template.csv": len(rows)}


# These custom services bundle multiple party roles (sold-to, ship-to, bill-to, employee
# responsible...) under one generically-named PartyID collection, keyed only by ParentObjectID -
# there's no role code field to pick the "real" customer/supplier directly. CONFIRMED BY TESTING
# across 4 of these collections (192/196, 513/524, 442/458, 121/130 resolved): '70000' (Danlaw's
# own company) and '71000' (its permanent establishment) appear on almost every document, and
# 10-digit IDs starting with '8' are employee-responsible IDs - excluding those and taking the
# most-frequent remaining PartyID reliably picks the actual customer/supplier.
_NON_PARTY_IDS = {"70000", "71000"}


def _is_employee_id(party_id):
    return len(party_id) == 10 and party_id.startswith("8")


def resolve_party(party_rows_by_parent, parent_object_id):
    from collections import Counter
    candidates = [
        r.get("PartyID") for r in party_rows_by_parent.get(parent_object_id, [])
        if r.get("PartyID") and r["PartyID"] not in _NON_PARTY_IDS and not _is_employee_id(r["PartyID"])
    ]
    if not candidates:
        return None
    return Counter(candidates).most_common(1)[0][0]


def _group_by_parent(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row.get("ParentObjectID"), []).append(row)
    return grouped


SO_HEADER_FIELDNAMES = ["id", "name", "partner_id/id", "date_order", "state", "currency_id/id", "amount_total"]
SO_LINE_FIELDNAMES = ["id", "order_id/id", "product_id/id", "name", "price_subtotal"]


def build_sales_orders():
    """
    khsalesorder custom service - 196 sales orders (mandatory sheet object #63).
    SalesOrderCollection (header) + ItemCollection (lines, ParentObjectID -> header ObjectID) +
    ItemProductCollection (ProductID, ParentObjectID -> Item.ObjectID - CONFIRMED by testing) +
    BuyerPartyCollection (see resolve_party() above for why this isn't a direct field lookup).
    """
    headers = load_raw("SalesOrderCollection")["rows"]
    items = load_raw("ItemCollection", service_hint="khsalesorder")["rows"]
    item_products = load_raw("ItemProductCollection")["rows"]
    parties = load_raw("BuyerPartyCollection", service_hint="khsalesorder")["rows"]

    party_by_order = _group_by_parent(parties)
    product_by_item = {p["ParentObjectID"]: p.get("ProductID") for p in item_products if p.get("ParentObjectID")}

    header_rows = []
    known_ids = set()
    for h in headers:
        object_id = h.get("ObjectID")
        if not object_id:
            continue
        known_ids.add(object_id)
        partner = resolve_party(party_by_order, object_id)
        header_rows.append(
            {
                "id": external_id("sap_so", object_id),
                "name": h.get("ID", object_id),
                "partner_id/id": external_id("sap_bp", partner) if partner else "",
                "date_order": parse_sap_date(h.get("PostingDateTime")),
                "state": "sale",
                "currency_id/id": (
                    f"base.{h.get('NetAmountCurrencyCode', '').strip().upper()}"
                    if h.get("NetAmountCurrencyCode") else ""
                ),
                "amount_total": h.get("NetAmount", "") or "0",
            }
        )

    line_rows = []
    for item in items:
        parent = item.get("ParentObjectID")
        if parent not in known_ids:
            continue
        object_id = item.get("ObjectID")
        product_id = product_by_item.get(object_id)
        line_rows.append(
            {
                "id": external_id("sap_so_item", object_id),
                "order_id/id": external_id("sap_so", parent),
                "product_id/id": external_id("sap_prod", product_id) if product_id else "",
                "name": item.get("Description") or product_id or item.get("ID", ""),
                "price_subtotal": item.get("NetAmount", "") or "0",
            }
        )

    write_csv("sale_order.csv", header_rows, SO_HEADER_FIELDNAMES)
    write_csv("sale_order_line.csv", line_rows, SO_LINE_FIELDNAMES)
    return {"sale_order.csv": len(header_rows), "sale_order_line.csv": len(line_rows)}


INVOICE_HEADER_FIELDNAMES = ["id", "partner_id/id", "invoice_date", "move_type", "state", "currency_id/id", "amount_total"]
INVOICE_LINE_FIELDNAMES = ["id", "move_id/id", "product_id/id", "name", "quantity", "price_unit"]


def build_customer_invoices():
    """khcustomerinvoice custom service - 524 customer invoices (sheet object #65)."""
    headers = load_raw("CustomerInvoiceCollection")["rows"]
    items = load_raw("ItemCollection", service_hint="khcustomerinvoice")["rows"]
    parties = load_raw("BuyerPartyCollection", service_hint="khcustomerinvoice")["rows"]
    party_by_doc = _group_by_parent(parties)

    header_rows = []
    known_ids = set()
    for h in headers:
        object_id = h.get("ObjectID")
        if not object_id:
            continue
        known_ids.add(object_id)
        partner = resolve_party(party_by_doc, object_id)
        header_rows.append(
            {
                "id": external_id("sap_cinv", object_id),
                "partner_id/id": external_id("sap_bp", partner) if partner else "",
                "invoice_date": parse_sap_date(h.get("Date")),
                "move_type": "out_invoice",
                "state": "posted",
                "currency_id/id": (
                    f"base.{h.get('TotalNetAmountCurrencyCode', '').strip().upper()}"
                    if h.get("TotalNetAmountCurrencyCode") else ""
                ),
                "amount_total": h.get("TotalNetAmount", "") or "0",
            }
        )

    line_rows = []
    for item in items:
        parent = item.get("ParentObjectID")
        if parent not in known_ids:
            continue
        product_id = item.get("ProductID")
        line_rows.append(
            {
                "id": external_id("sap_cinv_item", item.get("ObjectID")),
                "move_id/id": external_id("sap_cinv", parent),
                "product_id/id": external_id("sap_prod", product_id) if product_id else "",
                "name": item.get("Description") or product_id or item.get("ID", ""),
                "quantity": item.get("Quantity", "") or "1",
                "price_unit": item.get("NetAmount", "") or "0",
            }
        )

    write_csv("account_move_customer_invoice.csv", header_rows, INVOICE_HEADER_FIELDNAMES)
    write_csv("account_move_customer_invoice_line.csv", line_rows, INVOICE_LINE_FIELDNAMES)
    return {
        "account_move_customer_invoice.csv": len(header_rows),
        "account_move_customer_invoice_line.csv": len(line_rows),
    }


def build_supplier_invoices():
    """
    khsupplierinvoice custom service - 458 supplier invoices (sheet object #51, Vendor Bills).
    LifeCycleStatusCode confirmed real and populated (Paid=12, Partially Paid=11, Posted=8,
    Ready for Posting=3, In Process=1, Exception=2, Canceled=9, Voided=7). #9 Open Vendor Bills
    = anything not Paid/Canceled/Voided.
    """
    CLOSED_STATUS_CODES = {"12", "9", "7"}  # Paid, Canceled, Voided

    headers = load_raw("SupplierInvoiceCollection")["rows"]
    items = load_raw("ItemCollection", service_hint="khsupplierinvoice")["rows"]
    parties = load_raw("SellerPartyCollection")["rows"]
    party_by_doc = _group_by_parent(parties)

    header_rows = []
    open_header_rows = []
    known_ids = set()
    for h in headers:
        object_id = h.get("ObjectID")
        if not object_id:
            continue
        known_ids.add(object_id)
        partner = resolve_party(party_by_doc, object_id)
        row = {
            "id": external_id("sap_vinv", object_id),
            "partner_id/id": external_id("sap_bp", partner) if partner else "",
            "invoice_date": parse_sap_date(h.get("InvoiceDate")),
            "move_type": "in_invoice",
            "state": "posted",
            "currency_id/id": (
                f"base.{h.get('TotalNetAmountCurrencyCode', '').strip().upper()}"
                if h.get("TotalNetAmountCurrencyCode") else ""
            ),
            "amount_total": h.get("TotalNetAmount", "") or "0",
        }
        header_rows.append(row)
        if h.get("LifeCycleStatusCode") not in CLOSED_STATUS_CODES:
            open_header_rows.append(row)

    line_rows = []
    for item in items:
        parent = item.get("ParentObjectID")
        if parent not in known_ids:
            continue
        product_id = item.get("ProductID")
        line_rows.append(
            {
                "id": external_id("sap_vinv_item", item.get("ObjectID")),
                "move_id/id": external_id("sap_vinv", parent),
                "product_id/id": external_id("sap_prod", product_id) if product_id else "",
                "name": item.get("Description") or product_id or item.get("ID", ""),
                "quantity": item.get("Quantity", "") or "1",
                "price_unit": item.get("NetUnitPriceAmount", "") or "0",
            }
        )

    write_csv("account_move_vendor_bill.csv", header_rows, INVOICE_HEADER_FIELDNAMES)
    write_csv("account_move_vendor_bill_line.csv", line_rows, INVOICE_LINE_FIELDNAMES)
    write_csv("account_move_open_vendor.csv", open_header_rows, INVOICE_HEADER_FIELDNAMES)
    return {
        "account_move_vendor_bill.csv": len(header_rows),
        "account_move_vendor_bill_line.csv": len(line_rows),
        "account_move_open_vendor.csv": len(open_header_rows),
    }


OPPORTUNITY_FIELDNAMES = ["id", "name", "expected_revenue", "probability", "type", "stage_id/id"]


def build_opportunities():
    """
    khopportunity custom service - 15 opportunities (sheet objects #20/21/22).
    LifeCycleStatusCode confirmed real and populated: 1=Open, 2=In Process, 4=Won, 5=Lost.
    #21 Open Opportunities = not yet Won/Lost (codes 1,2). #22 Closed = Won or Lost (codes 4,5).
    """
    opportunities = load_raw("OpportunityCollection")["rows"]
    WON_LOST_CODES = {"4", "5"}

    all_rows = []
    open_rows = []
    closed_rows = []
    for opp in opportunities:
        object_id = opp.get("ObjectID")
        if not object_id:
            continue
        row = {
            "id": external_id("sap_opp", object_id),
            "name": opp.get("Description") or opp.get("ID", object_id),
            "expected_revenue": opp.get("ExpectedRevenueAmount", "") or "0",
            "probability": opp.get("ChanceOfSuccessPercent", "") or "0",
            "type": "opportunity",
            "stage_id/id": "",  # Odoo stages aren't ByDesign sales-phase codes - map manually post-import
        }
        all_rows.append(row)
        if opp.get("LifeCycleStatusCode") in WON_LOST_CODES:
            closed_rows.append(row)
        else:
            open_rows.append(row)

    write_csv("crm_lead.csv", all_rows, OPPORTUNITY_FIELDNAMES)
    write_csv("crm_lead_open.csv", open_rows, OPPORTUNITY_FIELDNAMES)
    write_csv("crm_lead_closed.csv", closed_rows, OPPORTUNITY_FIELDNAMES)
    return {
        "crm_lead.csv": len(all_rows),
        "crm_lead_open.csv": len(open_rows),
        "crm_lead_closed.csv": len(closed_rows),
    }


LOCATION_FIELDNAMES = ["id", "name"]


WAREHOUSE_FIELDNAMES = ["id", "name", "code"]


def build_locations():
    """
    khlocation custom service - 4 locations (sheet objects #27/#28).
    #27 Warehouses: ByDesign's own semantics for "this location tracks inventory" is the
    InventoryManagedLocationIndicator flag - filtering on it gives real warehouse data with no
    new SAP call needed (1/4 locations on this tenant is inventory-managed).
    """
    locations = load_raw("LocationCollection")["rows"]
    rows = []
    warehouse_rows = []
    for loc in locations:
        object_id = loc.get("ObjectID")
        if not object_id:
            continue
        name = loc.get("Name") or loc.get("ID", object_id)
        rows.append({"id": external_id("sap_loc", object_id), "name": name})
        if loc.get("InventoryManagedLocationIndicator"):
            warehouse_rows.append(
                {"id": external_id("sap_wh", object_id), "name": name, "code": loc.get("ID", "")}
            )
    write_csv("stock_location.csv", rows, LOCATION_FIELDNAMES)
    write_csv("stock_warehouse.csv", warehouse_rows, WAREHOUSE_FIELDNAMES)
    return {"stock_location.csv": len(rows), "stock_warehouse.csv": len(warehouse_rows)}


EMPLOYEE_FIELDNAMES = ["id", "name", "login", "email", "phone"]


def build_employees():
    """khemployee custom service - 99 employees (sheet object #18, Salespersons)."""
    employees = load_raw("EmployeeCollection")["rows"]
    addresses = load_raw("WorkplaceAddressCollection")["rows"]
    address_by_employee = {a["ParentObjectID"]: a for a in addresses if a.get("ParentObjectID")}

    rows = []
    for emp in employees:
        object_id = emp.get("ObjectID")
        if not object_id:
            continue
        addr = address_by_employee.get(object_id, {})
        email = addr.get("Email", "")
        rows.append(
            {
                "id": external_id("sap_emp", object_id),
                "name": emp.get("FormattedName") or object_id,
                "login": email or external_id("sap_emp", object_id),
                "email": email,
                "phone": addr.get("Phone", ""),
            }
        )
    write_csv("res_users.csv", rows, EMPLOYEE_FIELDNAMES)
    return {"res_users.csv": len(rows)}


BANK_STATEMENT_FIELDNAMES = ["id", "name", "date", "balance_start", "balance_end_real"]


def build_bank_statements():
    """khhousebankstatement custom service - 87 statements (sheet object #12)."""
    statements = load_raw("HouseBankStatementCollection")["rows"]
    rows = []
    for stmt in statements:
        object_id = stmt.get("ObjectID")
        if not object_id:
            continue
        rows.append(
            {
                "id": external_id("sap_bstmt", object_id),
                "name": stmt.get("ID", object_id),
                "date": parse_sap_date(stmt.get("Date")),
                "balance_start": stmt.get("OpeningBalanceAmount", "") or "0",
                "balance_end_real": stmt.get("ClosingBalanceAmount", "") or "0",
            }
        )
    write_csv("account_bank_statement.csv", rows, BANK_STATEMENT_FIELDNAMES)
    return {"account_bank_statement.csv": len(rows)}


PAYMENT_FIELDNAMES = ["id", "partner_id/id", "amount", "payment_type", "partner_type", "date", "currency_id/id"]


def build_payments():
    """
    khpayment custom service - 544 payments (sheet objects #10/11, Customer/Vendor Payments).
    Splits by looking up BusinessPartnerID against the customer_rank/supplier_rank already
    established in res_partner.csv (built earlier in the pipeline) rather than guessing from
    this entity's own fields, which don't distinguish payment direction cleanly.
    """
    payments = load_raw("PaymentCollection")["rows"]

    partner_rank = {}
    try:
        with open(os.path.join(ODOO_DIR, "res_partner.csv"), newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                partner_rank[row["id"].replace("sap_bp_", "")] = row
    except FileNotFoundError:
        pass

    rows = []
    for pmt in payments:
        object_id = pmt.get("ObjectID")
        if not object_id:
            continue
        bp_id = pmt.get("BusinessPartnerID", "")
        rank_row = partner_rank.get(bp_id, {})
        is_supplier = rank_row.get("supplier_rank") == "1"
        rows.append(
            {
                "id": external_id("sap_pay", object_id),
                "partner_id/id": external_id("sap_bp", bp_id) if bp_id else "",
                "amount": pmt.get("TransactionCurrencyAmount", "") or "0",
                "payment_type": "outbound" if is_supplier else "inbound",
                "partner_type": "supplier" if is_supplier else "customer",
                "date": parse_sap_date(pmt.get("AccountingTransactionDate")),
                "currency_id/id": (
                    f"base.{pmt.get('TransactionCurrencyCode', '').strip().upper()}"
                    if pmt.get("TransactionCurrencyCode") else ""
                ),
            }
        )
    write_csv("account_payment.csv", rows, PAYMENT_FIELDNAMES)
    return {"account_payment.csv": len(rows)}


PRICELIST_FIELDNAMES = ["id", "name", "currency_id/id"]


def build_pricelists():
    """
    khsalesarrangement custom service - 35 arrangements (sheet objects #59/60/61, Pricelists/
    Discount Rules/Customer Price Lists). CustomerUUID here is a hyphenated GUID that doesn't
    match res_partner's numeric CBP_UUID-based external IDs, so partner_id/id isn't populated -
    known limitation, needs a UUID->numeric-ID lookup this tenant hasn't exposed yet.
    """
    arrangements = load_raw("SalesArrangementCollection")["rows"]
    rows = []
    for arr in arrangements:
        object_id = arr.get("ObjectID")
        if not object_id:
            continue
        rows.append(
            {
                "id": external_id("sap_pricelist", object_id),
                "name": f"SAP Sales Arrangement {arr.get('ObjectID', '')[:12]}",
                "currency_id/id": (
                    f"base.{arr.get('CurrencyCode', '').strip().upper()}"
                    if arr.get("CurrencyCode") else ""
                ),
            }
        )
    write_csv("product_pricelist.csv", rows, PRICELIST_FIELDNAMES)
    return {"product_pricelist.csv": len(rows)}


DELIVERY_HEADER_FIELDNAMES = ["id", "name", "partner_id/id", "scheduled_date", "state"]
DELIVERY_LINE_FIELDNAMES = ["id", "picking_id/id", "product_id/id", "name"]


def build_deliveries():
    """khoutbounddelivery custom service - 130 deliveries (sheet object #64)."""
    headers = load_raw("OutboundDeliveryCollection")["rows"]
    items = load_raw("ItemCollection", service_hint="khoutbounddelivery")["rows"]
    parties = load_raw("BuyerPartyCollection", service_hint="khoutbounddelivery")["rows"]
    party_by_doc = _group_by_parent(parties)

    header_rows = []
    known_ids = set()
    for h in headers:
        object_id = h.get("ObjectID")
        if not object_id:
            continue
        known_ids.add(object_id)
        partner = resolve_party(party_by_doc, object_id)
        header_rows.append(
            {
                "id": external_id("sap_delivery", object_id),
                "name": h.get("ID", object_id),
                "partner_id/id": external_id("sap_bp", partner) if partner else "",
                "scheduled_date": parse_sap_date(h.get("CreationDateTime")),
                "state": "done",
            }
        )

    line_rows = []
    for item in items:
        parent = item.get("ParentObjectID")
        if parent not in known_ids:
            continue
        product_id = item.get("ProductID")
        line_rows.append(
            {
                "id": external_id("sap_delivery_item", item.get("ObjectID")),
                "picking_id/id": external_id("sap_delivery", parent),
                "product_id/id": external_id("sap_prod", product_id) if product_id else "",
                "name": product_id or item.get("ID", ""),
            }
        )

    write_csv("stock_picking_delivery.csv", header_rows, DELIVERY_HEADER_FIELDNAMES)
    write_csv("stock_picking_delivery_line.csv", line_rows, DELIVERY_LINE_FIELDNAMES)
    return {
        "stock_picking_delivery.csv": len(header_rows),
        "stock_picking_delivery_line.csv": len(line_rows),
    }


PRODUCTION_ORDER_FIELDNAMES = ["id", "name", "product_id/id", "product_qty", "date_planned_start", "date_planned_finished", "state"]


def build_production_orders():
    """
    khproductionorder custom service - 152 orders (sheet object #44, Manufacturing Orders).
    MainProductOutputCollection's row count (2842) doesn't correspond 1:1 with the 152 orders
    and carries no ParentObjectID field - its linkage to a specific order isn't reliably
    determinable from this entity shape, so line-level output isn't built; header only, with
    BillOfMaterialID kept as a text reference (BOMs, #40, isn't wired up yet either).
    """
    orders = load_raw("ProductionOrderCollection")["rows"]
    rows = []
    for order in orders:
        object_id = order.get("ObjectID")
        if not object_id:
            continue
        rows.append(
            {
                "id": external_id("sap_mo", object_id),
                "name": order.get("ID", object_id),
                "product_id/id": "",  # see docstring - output linkage not reliable from this entity
                "product_qty": "0",
                "date_planned_start": parse_sap_date(order.get("RequestedStartDateTime")),
                "date_planned_finished": parse_sap_date(order.get("RequestedEndDateTime")),
                "state": "done",
            }
        )
    write_csv("mrp_production.csv", rows, PRODUCTION_ORDER_FIELDNAMES)
    return {"mrp_production.csv": len(rows)}


WORKCENTER_FIELDNAMES = ["id", "name", "code"]


def build_work_centers():
    """
    Sheet object #42 (Work Centers, mandatory). No dedicated Work Center master service was
    found, but khproductionorder's OperationCollection (already imported for #44) carries
    ResourceID/ResourceDescription on every operation - deduplicating gives a real work center
    list (18 distinct "Equipment Resource" entries on this tenant) with no new SAP call needed.
    """
    operations = load_raw("OperationCollection")["rows"]
    seen = {}
    for op in operations:
        resource_id = op.get("ResourceID")
        if not resource_id or resource_id in seen:
            continue
        seen[resource_id] = {
            "id": external_id("sap_wc", resource_id),
            "name": op.get("ResourceDescription") or resource_id,
            "code": resource_id,
        }
    rows = list(seen.values())
    write_csv("mrp_workcenter.csv", rows, WORKCENTER_FIELDNAMES)
    return {"mrp_workcenter.csv": len(rows)}


PAYMENT_TERM_FIELDNAMES = ["id", "name"]


def build_payment_terms():
    """
    Sheet object #4 (Payment Terms, mandatory). No dedicated master service found, but
    CashDiscountTermsCollection - already present in khcustomerinvoice and khsupplierinvoice,
    both already imported for #51/#65 - carries a real PaymentTermsCode/Text (customer side)
    or Code/CodeText (supplier side) on every invoice. Deduplicating both gives a real payment
    terms master with no new SAP call needed.
    """
    customer_terms = load_raw("CashDiscountTermsCollection", service_hint="khcustomerinvoice")["rows"]
    supplier_terms = load_raw("CashDiscountTermsCollection", service_hint="khsupplierinvoice")["rows"]

    seen = {}
    for t in customer_terms:
        code, text = t.get("PaymentTermsCode"), t.get("PaymentTermsCodeText")
        if code and code not in seen:
            seen[code] = text or code
    for t in supplier_terms:
        code, text = t.get("Code"), t.get("CodeText")
        if code and code not in seen:
            seen[code] = text or code

    rows = [{"id": external_id("sap_payterm", code), "name": name} for code, name in seen.items()]
    write_csv("account_payment_term.csv", rows, PAYMENT_TERM_FIELDNAMES)
    return {"account_payment_term.csv": len(rows)}


UOM_FIELDNAMES = ["id", "name"]


def build_uom():
    """Sheet object #29 (UOM, mandatory). vmumaterial's MaterialBaseMeasureUnitCodeCollection
    codelist - 23 real units of measure, already-imported service, no new SAP call needed."""
    units = load_raw("MaterialBaseMeasureUnitCodeCollection")["rows"]
    rows = []
    for u in units:
        code = u.get("Code")
        if not code:
            continue
        rows.append({"id": external_id("sap_uom", code), "name": u.get("Description") or code})
    write_csv("uom_uom.csv", rows, UOM_FIELDNAMES)
    return {"uom_uom.csv": len(rows)}


PRODUCT_CATEGORY_FIELDNAMES = ["id", "name"]


def build_product_categories():
    """Sheet object #58 (Product Categories, mandatory). vmumaterial's ProductCategoryCollection
    is one row per material (3058), not per distinct category - deduplicated by
    ProductCategoryInternalID to get the real category master. Already-imported service, no new
    SAP call needed."""
    categories = load_raw("ProductCategoryCollection")["rows"]
    seen = {}
    for c in categories:
        code = c.get("ProductCategoryInternalID")
        if code and code not in seen:
            seen[code] = c.get("Description") or code
    rows = [{"id": external_id("sap_prodcat", code), "name": name} for code, name in seen.items()]
    write_csv("product_category.csv", rows, PRODUCT_CATEGORY_FIELDNAMES)
    return {"product_category.csv": len(rows)}


TRANSFORMS = [
    ("account_analytic_plan / account_analytic_account (cost centers)", build_cost_centers),
    ("res_partner / res_bank / res_partner_bank", build_res_partner),
    ("purchase_order / purchase_order_line", build_purchase_orders),
    ("product_template", build_products),
    ("sale_order / sale_order_line", build_sales_orders),
    ("account_move_customer_invoice(_line)", build_customer_invoices),
    ("account_move_vendor_bill(_line)", build_supplier_invoices),
    ("crm_lead (opportunities)", build_opportunities),
    ("stock_location", build_locations),
    ("res_users (employees/salespersons)", build_employees),
    ("account_bank_statement", build_bank_statements),
    ("account_payment", build_payments),
    ("product_pricelist (sales arrangements)", build_pricelists),
    ("stock_picking_delivery(_line)", build_deliveries),
    ("mrp_production", build_production_orders),
    ("mrp_workcenter", build_work_centers),
    ("account_payment_term", build_payment_terms),
    ("uom_uom", build_uom),
    ("product_category", build_product_categories),
]


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    summary = {}
    for label, fn in TRANSFORMS:
        logger.info("Transforming %s...", label)
        try:
            summary.update(fn())
        except Exception:
            logger.exception("FAILED transform: %s", label)

    logger.info("=== Transform summary ===")
    for filename, count in summary.items():
        logger.info("%-30s %d rows", filename, count)


if __name__ == "__main__":
    sys.exit(main())
