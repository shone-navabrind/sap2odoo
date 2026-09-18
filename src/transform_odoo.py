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
CONTACT_FIELDNAMES = ["id", "name", "parent_id/id", "function", "type"]
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


def _join_by_parent(rows, key="ParentObjectID"):
    """
    Index child rows by their parent's 32-character ObjectID.

    ByDesign returns ParentObjectID on these partner child entities as a 64-character value:
    two 32-character ObjectIDs concatenated. Only the first half is the parent business
    object's ObjectID. Verified on real data - joining on the full string matches 0 of 44
    addresses, joining on the first 32 characters matches all 44. (Where SAP happens to return
    a plain 32-character parent, as on BankDetails/TaxNumber/Role, the slice is a no-op.)
    """
    out = {}
    for row in rows:
        parent = (row.get(key) or "")[:32]
        if parent:
            out.setdefault(parent, []).append(row)
    return out


def _first(index, object_id, field):
    """First non-empty `field` among the child rows belonging to `object_id`."""
    for row in index.get(object_id, []):
        value = (row.get(field) or "").strip()
        if value:
            return value
    return ""


def build_res_partner():
    """
    Sheet objects #17 Customers and #46 Vendors (both mandatory), plus #5 Banks.

    Built from the khcustomer / khsupplier custom services - plain CRUD, so every field comes
    back in one request. This replaces the previous analytics-report source
    (RPBUPCSD/RPBUPSPP), which had to be fetched in field-chunks and merged on CBP_UUID, a key
    SAP does not guarantee unique: get_entity_set_all_fields logged 44 distinct keys against 50
    returned rows for customers and 229 against 237 for suppliers, meaning some partners'
    non-key fields were an arbitrary sample rather than a join.

    Switching source is safe and strictly additive - checked before the change, not assumed:
    the CRUD services return all 271 external IDs the analytics route produced, plus 17 more
    (288 total), so no existing `sap_bp_*` reference from any document file breaks. The
    analytics rows are still read afterwards, purely to fill fields the CRUD services leave
    blank; nothing that was populated before can be lost.

    Also produces, from the same services:
      res_partner_contact.csv - contact persons, via khcustomer's RelationshipCollection
      res_bank.csv            - the real bank directory from khhousebankaccount
      res_partner_bank.csv    - partner bank accounts from BankDetailsCollection
    """
    customers = load_raw("CustomerCollection", service_hint="khcustomer")["rows"]
    suppliers = load_raw("SupplierCollection", service_hint="khsupplier")["rows"]

    addresses = _join_by_parent(
        load_raw("PostalAddressCollection", service_hint="khcustomer")["rows"]
        + load_raw("CurrentDefaultPostalAddressCollection", service_hint="khsupplier")["rows"])
    phones = _join_by_parent(
        load_raw("ConventionalPhoneCollection", service_hint="khcustomer")["rows"]
        + load_raw("MobilePhoneCollection", service_hint="khcustomer")["rows"]
        + load_raw("CurrentDefaultConventionalPhoneCollection", service_hint="khsupplier")["rows"]
        + load_raw("CurrentDefaultMobilePhoneCollection", service_hint="khsupplier")["rows"])
    emails = _join_by_parent(
        load_raw("CurrentDefaultEMailCollection", service_hint="khsupplier")["rows"])
    websites = _join_by_parent(
        load_raw("WebSiteCollection", service_hint="khcustomer")["rows"]
        + load_raw("CurrentDefaultWebSiteCollection", service_hint="khsupplier")["rows"])
    # The two services name the tax number column differently (TaxNumberID vs PartyTaxID).
    tax_numbers = _join_by_parent(
        load_raw("TaxNumberCollection", service_hint="khcustomer")["rows"]
        + load_raw("TaxNumberCollection", service_hint="khsupplier")["rows"])
    banks_by_partner = _join_by_parent(
        load_raw("BankDetailsCollection", service_hint="khcustomer")["rows"]
        + load_raw("BankDetailsCollection", service_hint="khsupplier")["rows"])

    def partner_row(record, is_customer):
        object_id = record.get("ObjectID", "")
        internal_id = record.get("InternalID")
        return {
            "id": external_id("sap_bp", internal_id),
            "name": (record.get("BusinessPartnerFormattedName")
                     or record.get("SortingFormattedName") or internal_id),
            "street": " ".join(p for p in (_first(addresses, object_id, "HouseID"),
                                           _first(addresses, object_id, "StreetName")) if p),
            "city": (_first(addresses, object_id, "CityName")
                     or _first(addresses, object_id, "DifferentCityName")),
            "zip": (_first(addresses, object_id, "StreetPostalCode")
                    or _first(addresses, object_id, "CompanyPostalCode")),
            "country_id/id": _country_ref(_first(addresses, object_id, "CountryCode")),
            "phone": _first(phones, object_id, "FormattedNumberDescription"),
            "email": _first(emails, object_id, "URI"),
            "website": _first(websites, object_id, "URI"),
            "vat": (_first(tax_numbers, object_id, "TaxNumberID")
                    or _first(tax_numbers, object_id, "PartyTaxID")),
            "customer_rank": "1" if is_customer else "0",
            "supplier_rank": "0" if is_customer else "1",
            # LifeCycleStatusCode 2 = Active; 1 = In Preparation, 3 = Blocked, 4 = Obsolete.
            "active": "True" if record.get("LifeCycleStatusCode") == "2" else "False",
        }

    rows_by_id = {}
    object_ids_by_partner = {}
    for record in customers:
        if not record.get("InternalID"):
            continue
        row = partner_row(record, is_customer=True)
        rows_by_id[row["id"]] = row
        object_ids_by_partner.setdefault(row["id"], []).append(record.get("ObjectID", ""))

    for record in suppliers:
        if not record.get("InternalID"):
            continue
        row = partner_row(record, is_customer=False)
        existing = rows_by_id.get(row["id"])
        object_ids_by_partner.setdefault(row["id"], []).append(record.get("ObjectID", ""))
        if existing:
            # Same business partner in both roles: keep both ranks, and take any field the
            # customer-side record left blank.
            existing["supplier_rank"] = "1"
            for field, value in row.items():
                if value and not existing.get(field):
                    existing[field] = value
        else:
            rows_by_id[row["id"]] = row

    # Backfill from the old analytics reports. They are far sparser, but where the CRUD
    # services return nothing at all for a field this is the only value available, so reading
    # them costs nothing and guarantees the switch cannot lose data.
    analytics_fields = {"CSTREET_NAME": "street", "CCITY_NAME": "city", "CSTREET_POSTAL": "zip",
                        "CPHONE_NR": "phone", "CEMAIL_URI": "email", "CWEB_URI": "website"}
    for entity in ("RPBUPCSD_Q0001QueryResults", "RPBUPSPP_Q0001QueryResults"):
        for record in load_raw(entity)["rows"]:
            row = rows_by_id.get(external_id("sap_bp", record.get("CBP_UUID") or ""))
            if not row:
                continue
            for source_field, odoo_field in analytics_fields.items():
                value = (record.get(source_field) or "").strip()
                if value and not row.get(odoo_field):
                    row[odoo_field] = value
            if not row.get("country_id/id"):
                row["country_id/id"] = _country_ref(record.get("CCOUNTRY_CODE"))

    write_csv("res_partner.csv", list(rows_by_id.values()), PARTNER_FIELDNAMES)

    # --- Contacts -------------------------------------------------------------------------
    # RelationshipCollection carries no ParentObjectID, but it does name both sides by their
    # InternalID, which is exactly what the partner external IDs are built from.
    contact_rows, seen_contacts = [], set()
    for link in load_raw("RelationshipCollection", service_hint="khcustomer")["rows"]:
        parent_id, contact_id = link.get("InternalID1"), link.get("InternalID2")
        name = link.get("BusinessPartnerFormattedName2")
        if not (contact_id and name) or contact_id in seen_contacts:
            continue
        parent_ext = external_id("sap_bp", parent_id) if parent_id else ""
        if parent_ext not in rows_by_id:
            continue
        seen_contacts.add(contact_id)
        contact_rows.append({
            "id": external_id("sap_contact", contact_id),
            "name": name,
            "parent_id/id": parent_ext,
            "function": link.get("FunctionalTitleName") or link.get("BusinessPartnerFunctionTypeCodeText") or "",
            "type": "contact",
        })
    write_csv("res_partner_contact.csv", contact_rows, CONTACT_FIELDNAMES)

    # --- Banks ----------------------------------------------------------------------------
    # The bank master proper, rather than bank names scraped out of partner records.
    bank_rows = {}
    for entry in load_raw("BankDirectoryEntryCollection", service_hint="khhousebankaccount")["rows"]:
        name = (entry.get("OrganisationFormattedName") or "").strip()
        if not name or entry.get("DeletedIndicator"):
            continue
        bank_rows[external_id("sap_bank", name)] = {
            "id": external_id("sap_bank", name),
            "name": name,
            "country_id/id": _country_ref(entry.get("CountryCode")),
        }

    partner_bank_rows, seen_accounts = [], set()
    for partner_ext, object_ids in object_ids_by_partner.items():
        for object_id in object_ids:
            for detail in banks_by_partner.get(object_id, []):
                account = (detail.get("BankAccountID") or detail.get("BankAccountStandardID") or "").strip()
                bank_name = (detail.get("BankFormattedName") or "").strip()
                if not account:
                    continue
                key = (partner_ext, account)
                if key in seen_accounts:
                    continue
                seen_accounts.add(key)
                bank_ext = external_id("sap_bank", bank_name) if bank_name else ""
                # A bank referenced by an account but absent from the directory still has to
                # exist in res_bank.csv, or the partner bank row will not import.
                if bank_ext and bank_ext not in bank_rows:
                    bank_rows[bank_ext] = {
                        "id": bank_ext,
                        "name": bank_name,
                        "country_id/id": _country_ref(detail.get("BankCountryCode")),
                    }
                partner_bank_rows.append({
                    "id": external_id("sap_pbank", f"{partner_ext}_{account}"),
                    "partner_id/id": partner_ext,
                    "bank_id/id": bank_ext,
                    "acc_number": account,
                })

    write_csv("res_bank.csv", list(bank_rows.values()), BANK_FIELDNAMES)
    write_csv("res_partner_bank.csv", partner_bank_rows, PARTNER_BANK_FIELDNAMES)

    return {
        "res_partner.csv": len(rows_by_id),
        "res_partner_contact.csv": len(contact_rows),
        "res_bank.csv": len(bank_rows),
        "res_partner_bank.csv": len(partner_bank_rows),
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

    # SupplierCollection bundles several party roles per PO (3529 rows for 655 POs), so taking
    # the first row picked the wrong party or an empty one 33% of the time. Type-aware
    # resolution (prefer a PartyID that is a known SUPPLIER in res_partner.csv) measured 87%
    # valid vs 67% for first-row.
    party_by_po = _group_by_parent(suppliers)

    header_rows = []
    known_po_ids = set()
    for header in headers:
        object_id = header.get("ObjectID")
        if not object_id:
            continue
        known_po_ids.add(object_id)
        partner_bp = resolve_party(party_by_po, object_id, prefer="supplier")
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
    "uom_po_id/id", "standard_price", "purchase_ok", "sale_ok", "tracking", "route_ids/id",
    "active",
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

    Also mapped (added after checking every remaining real entity in vmumaterial, 2026-09-18):
      IdentifiedStockTypeCode (MaterialCollection)  -> tracking ('01'=Batch -> 'lot', else 'none';
                                                          SerialNumberProfileCode is 100% "No
                                                          Serial Number Assignment" on this
                                                          tenant - zero variation, no signal, so
                                                          not used for tracking)
      PlanningCollection.ProcurementTypeCode        -> route_ids/id (real 107/93 split between
                                                          External Procurement and In-house
                                                          Production in a 200-row sample; maps to
                                                          Odoo's standard purchase_stock/mrp route
                                                          external IDs - ASSUMES those modules are
                                                          installed in the target Odoo)

    Checked and extracted but with NO corresponding core Odoo product.template field, so NOT
    mapped (real SAP data, just nothing sensible to map it to without extra modules/masters):
    IdentificationCollection (alternate IDs, redundant with default_code), LogisticsCollection
    (site/logistics, not a product attribute), ValuationCollection (company/valuation status),
    AvailabilityConfirmationCollection (supply planning area config), PlanningForecastGroupCollection
    (a second, forecast-specific category grouping distinct from ProductCategoryCollection),
    QuantityConversionCollection (unit conversion factors, only 5 rows), DeviantTaxClassificationCollection
    (per-country tax override, only 4 rows - needs Taxes master #2, still pending).

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
    planning = load_raw("PlanningCollection")["rows"]

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

    procurement_type_by_material = {}
    for p in planning:
        parent = p.get("ParentObjectID")
        if parent and parent not in procurement_type_by_material:
            procurement_type_by_material[parent] = p.get("ProcurementTypeCode", "")

    ROUTE_BY_PROCUREMENT_TYPE = {
        "2": "purchase_stock.route_warehouse0_buy",  # External Procurement
        "1": "mrp.route_warehouse0_manufacture",  # In-house Production
    }

    rows = []
    for material in materials:
        object_id = material.get("ObjectID")
        if not object_id:
            continue
        material_uuid = material.get("UUID")
        base_uom = material.get("BaseMeasureUnitCode", "")
        purchase_uom = purchase_uom_by_material.get(object_id)
        category_code = category_by_material.get(object_id)
        procurement_type = procurement_type_by_material.get(object_id)
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
                "tracking": "lot" if material.get("IdentifiedStockTypeCode") == "01" else "none",
                "route_ids/id": ROUTE_BY_PROCUREMENT_TYPE.get(procurement_type, ""),
                "active": "True",
            }
        )

    rows.extend(_service_product_rows())
    write_csv("product_template.csv", rows, PRODUCT_FIELDNAMES)
    return {"product_template.csv": len(rows)}


def _service_product_rows():
    """
    SERVICE products, appended to the same product_template.csv as the materials above.

    ByDesign keeps services in a completely separate master (khserviceproduct) from materials
    (vmumaterial); Odoo has one product.template for both. Until khserviceproduct was imported
    these 7 were missing from the output entirely - not filtered out, simply never fetched.

    Mapped as type "service" rather than "consu", which is what makes Odoo skip stock handling
    for them.
    """
    try:
        services = load_raw("ServiceProductCollection", service_hint="khserviceproduct")["rows"]
    except FileNotFoundError:
        # khserviceproduct not imported on this tenant - materials-only output is still valid.
        return []

    sales = {r.get("ParentObjectID"): r for r in
             load_raw("SalesCollection", service_hint="khserviceproduct")["rows"]}
    purchasing = {r.get("ParentObjectID"): r for r in
                  load_raw("PurchasingCollection", service_hint="khserviceproduct")["rows"]}
    categories = {r.get("ParentObjectID"): r for r in
                  load_raw("ProductCategoryCollection", service_hint="khserviceproduct")["rows"]}

    rows = []
    for service in services:
        object_id = service.get("ObjectID")
        internal_id = service.get("InternalID")
        if not internal_id:
            continue
        base_uom = service.get("BaseMeasureUnitCode", "")
        sale = sales.get(object_id, {})
        purchase = purchasing.get(object_id, {})
        category = (categories.get(object_id) or {}).get("ProductCategoryInternalID", "")
        rows.append({
            "id": external_id("sap_prod", internal_id),
            "name": service.get("Description") or internal_id,
            "default_code": internal_id,
            "description": sale.get("ItemGroupCodeText", ""),
            "type": "service",
            "categ_id/id": external_id("sap_prodcat", category) if category else "",
            "uom_id/id": external_id("sap_uom", base_uom) if base_uom else "",
            "uom_po_id/id": external_id(
                "sap_uom", purchase.get("PurchasingMeasureUnitCode") or base_uom)
                if (purchase.get("PurchasingMeasureUnitCode") or base_uom) else "",
            # No valuation data for services on this tenant (khserviceproductvaluationdata is
            # not imported), so no cost price is invented.
            "standard_price": "",
            "purchase_ok": "True" if purchase else "False",
            "sale_ok": "True" if sale else "False",
            "tracking": "none",
            "route_ids/id": "",
            "active": "True" if service.get("LifeCycleStatusCode", "2") == "2" else "True",
        })
    return rows


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


def _load_partner_ranks():
    """
    Reads res_partner.csv (written earlier in the same pipeline run) to know which business
    partner IDs are real, and which are customers vs suppliers. Used to pick the right party
    off a document instead of guessing by frequency alone.
    """
    known, customers, suppliers = set(), set(), set()
    try:
        with open(os.path.join(ODOO_DIR, "res_partner.csv"), newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                bp_id = row["id"].replace("sap_bp_", "")
                known.add(bp_id)
                if row.get("customer_rank") == "1":
                    customers.add(bp_id)
                if row.get("supplier_rank") == "1":
                    suppliers.add(bp_id)
    except FileNotFoundError:
        pass
    return known, customers, suppliers


def resolve_party(party_rows_by_parent, parent_object_id, prefer=None):
    """
    Pick the real customer/supplier off a document's bundled party collection.

    `prefer` is "customer" or "supplier" - when given, a PartyID that is a KNOWN partner of
    that type wins over one that merely appears most often. MEASURED on real data: for Purchase
    Orders this lifts partner resolution from 67% to 87% valid; for Sales Orders, Customer
    Invoices, Vendor Bills and Deliveries it produces identical results (98/98/97/93%), so it's
    a strict improvement, not a trade-off.
    """
    from collections import Counter
    candidates = [
        r.get("PartyID") for r in party_rows_by_parent.get(parent_object_id, [])
        if r.get("PartyID") and r["PartyID"] not in _NON_PARTY_IDS and not _is_employee_id(r["PartyID"])
    ]
    if not candidates:
        return None

    known, customers, suppliers = _partner_ranks()
    prefer_set = customers if prefer == "customer" else suppliers if prefer == "supplier" else set()

    typed = [c for c in candidates if c in prefer_set]
    if typed:
        return Counter(typed).most_common(1)[0][0]
    known_candidates = [c for c in candidates if c in known]
    if known_candidates:
        return Counter(known_candidates).most_common(1)[0][0]
    return Counter(candidates).most_common(1)[0][0]


_PARTNER_RANKS_CACHE = None


def _partner_ranks():
    """res_partner.csv is re-read once per run, not per document (655+ documents each)."""
    global _PARTNER_RANKS_CACHE
    if _PARTNER_RANKS_CACHE is None:
        _PARTNER_RANKS_CACHE = _load_partner_ranks()
    return _PARTNER_RANKS_CACHE


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
        partner = resolve_party(party_by_order, object_id, prefer="customer")
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
        partner = resolve_party(party_by_doc, object_id, prefer="customer")
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
        partner = resolve_party(party_by_doc, object_id, prefer="supplier")
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


OPPORTUNITY_FIELDNAMES = ["id", "name", "expected_revenue", "probability", "type"]


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
            # stage_id deliberately omitted: Odoo's CRM stages are per-database configuration
            # and ByDesign's sales-phase codes don't map onto them, so a 100%-empty column would
            # just be noise in the import file. Set stages in Odoo after import.
            "type": "opportunity",
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


LOCATION_FIELDNAMES = ["id", "name", "location_id/id", "usage"]


WAREHOUSE_FIELDNAMES = ["id", "name", "code"]


def logistics_area_key(site_id, area_id):
    """
    The external-ID key for a storage area, shared by stock_location.csv and
    stock_quant_adjustment.csv.

    The inventory report identifies a storage area as "<site>/<area>" ("71000/71000-3") while
    khlocation gives the two parts separately (SiteID + ID). Deriving both sides from one
    function is what makes the quants' location_id/id actually resolve.
    """
    return external_id("sap_loc", f"{site_id}/{area_id}")


def build_locations():
    """
    khlocation custom service - sheet objects #27 (Warehouses) and #28 (Locations).

    Two levels: 4 top-level Locations (sites), plus the 16 LogisticsAreas inside them - the
    actual storage bins ("RM Stores - Good", "FG Main Stores", "Quality Area") that inventory
    balances are reported against. The areas were extracted but not mapped until now, which
    left every stock quant pointing at a location that did not exist in the output.

    #27 Warehouses: ByDesign's own semantics for "this location tracks inventory" is the
    InventoryManagedLocationIndicator flag - filtering on it gives real warehouse data (1/4
    locations on this tenant is inventory-managed).
    """
    locations = load_raw("LocationCollection", service_hint="khlocation")["rows"]
    areas = load_raw("LogisticsAreaCollection", service_hint="khlocation")["rows"]

    rows, warehouse_rows = [], []
    site_key_by_id = {}
    for loc in locations:
        object_id = loc.get("ObjectID")
        if not object_id:
            continue
        name = loc.get("Name") or loc.get("ID", object_id)
        site_key = external_id("sap_loc", object_id)
        site_key_by_id[loc.get("ID")] = site_key
        rows.append({"id": site_key, "name": name, "location_id/id": "", "usage": "view"})
        if loc.get("InventoryManagedLocationIndicator"):
            warehouse_rows.append(
                {"id": external_id("sap_wh", object_id), "name": name, "code": loc.get("ID", "")}
            )

    for area in areas:
        area_id, site_id = area.get("ID"), area.get("SiteID")
        if not area_id or not site_id:
            continue
        rows.append({
            "id": logistics_area_key(site_id, area_id),
            "name": area.get("Description") or area_id,
            "location_id/id": site_key_by_id.get(site_id, ""),
            # Only inventory-managed areas hold stock; the rest are pass-through views.
            "usage": "internal" if area.get("InventoryManagedIndicator") else "view",
        })

    write_csv("stock_location.csv", rows, LOCATION_FIELDNAMES)
    write_csv("stock_warehouse.csv", warehouse_rows, WAREHOUSE_FIELDNAMES)
    return {"stock_location.csv": len(rows), "stock_warehouse.csv": len(warehouse_rows)}


EMPLOYEE_FIELDNAMES = ["id", "name", "login", "email", "phone"]


def build_employees():
    """
    khemployee custom service - 99 employees (sheet object #18, Salespersons).

    WorkplaceAddressCollection's ParentObjectID is a 64-char CONCATENATION of two 32-char
    ObjectIDs - the employee's ObjectID followed by the address node's own parent. Joining on
    the full string matches nothing (confirmed: 0/7); the first 32 chars match 7/7.
    """
    employees = load_raw("EmployeeCollection")["rows"]
    addresses = load_raw("WorkplaceAddressCollection")["rows"]
    address_by_employee = {
        a["ParentObjectID"][:32]: a for a in addresses if a.get("ParentObjectID")
    }

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
        partner = resolve_party(party_by_doc, object_id, prefer="customer")
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

    The product being produced comes from MainProductOutput, pulled via $expand in
    extract_raw.py: MainProductOutputCollection's own endpoint returns 2842 rows with no
    ParentObjectID and ObjectIDs that don't match the orders', so it can't be joined from
    there - $expand is the only reliable link. Flattened into MainProductOutput.* keys.

    Odoo's mrp.production requires product_id and product_qty, so without this the file could
    not be imported at all.
    """
    orders = load_raw("ProductionOrderCollection")["rows"]
    rows = []
    for order in orders:
        object_id = order.get("ObjectID")
        if not object_id:
            continue
        product_id = order.get("MainProductOutput.ProductID")
        planned_qty = order.get("MainProductOutput.PlannedQuantity")
        rows.append(
            {
                "id": external_id("sap_mo", object_id),
                "name": order.get("ID", object_id),
                "product_id/id": external_id("sap_prod", product_id) if product_id else "",
                "product_qty": planned_qty or "0",
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
    categories = load_raw("ProductCategoryCollection", service_hint="vmumaterial")["rows"]
    try:
        # Service products sit in their own categories (SER, FREIGHT) that no material uses,
        # so reading only vmumaterial left product_template.csv pointing at categories that
        # did not exist in this file.
        categories += load_raw("ProductCategoryCollection", service_hint="khserviceproduct")["rows"]
    except FileNotFoundError:
        pass
    seen = {}
    for c in categories:
        code = c.get("ProductCategoryInternalID")
        if code and code not in seen:
            seen[code] = c.get("Description") or code
    rows = [{"id": external_id("sap_prodcat", code), "name": name} for code, name in seen.items()]
    write_csv("product_category.csv", rows, PRODUCT_CATEGORY_FIELDNAMES)
    return {"product_category.csv": len(rows)}


ACCOUNT_FIELDNAMES = ["id", "code", "name", "account_type", "reconcile"]

# The six reports that between them expose every G/L account in use on this tenant, as the
# minimal cover computed by src/probe_gl_accounts.py over all 60 reports declaring CGLACCT.
GL_ACCOUNT_SOURCES = [
    ("fin_costandrevenue_analytics.svc", "RPFINCACU04_Q0002QueryResults"),
    ("fin_audit_analytics.svc", "RPFINGLAU02_Q0002QueryResults"),
    ("fin_generalledger_analytics.svc", "RPFINFXAU05_Q0001QueryResults"),
    ("fin_generalledger_analytics.svc", "RPFINFCDU02_Q0001QueryResults"),
    ("fin_audit_analytics.svc", "RPFININVU03_Q0001QueryResults"),
    ("fin_audit_analytics.svc", "RPFINGLAU02_Q0003QueryResults"),
]

# Odoo requires an account_type on every account.account row, and this tenant publishes none:
# the only two reports carrying a G/L account type characteristic (RPFINPRFU24) return 0 rows,
# and the G/L Account Master report is blocked by a mandatory variable (see extract_raw.SOURCES).
#
# So the type is derived from the account number range. That is safe here because ByDesign's
# numbering is strictly banded and every band is confirmed by the account NAMES actually present
# in this tenant's data - e.g. 150000 "Accounts Payable-Domestic", 242000 "Accounts
# Receivable-Domestic", 241000 "Inventory - Raw Material", 300001 "Domestic Sales",
# 500010 "Salary", 700044 "GR/IR clearing". Each band below lists the account that confirms it.
ACCOUNT_TYPE_BANDS = [
    ("150", "liability_payable",    "150000 Accounts Payable-Domestic"),
    ("151", "liability_payable",    "151000 Accounts Payable-International"),
    ("16",  "liability_current",    "163455 IGST-Payable-Goa-RCM"),
    ("20",  "asset_fixed",          "202001 Buildings - Administration"),
    ("21",  "asset_fixed",          "212110 Acc Dep Buildings (accumulated depreciation)"),
    ("22",  "asset_fixed",          "227130 CWIP_Building (capital work in progress)"),
    ("241", "asset_current",        "241000 Inventory - Raw Material"),
    ("242", "asset_receivable",     "242000 Accounts Receivable-Domestic"),
    ("244", "asset_cash",           "244607 STATE BANK OF INDIA - VERNA"),
    ("245", "asset_cash",           "245002 Current Account Corpn Bank"),
    ("246", "asset_current",        "246000 Bills Receivable"),
    ("25",  "asset_current",        "252500 Advances to Suppliers"),
    ("3",   "income",               "300001 Domestic Sales"),
    ("4",   "expense_direct_cost",  "490001 Raw Material (cost of goods)"),
    ("5",   "expense",              "500010 Salary"),
    ("7",   "liability_current",    "700044 GR/IR clearing-Unbills Payable"),
]


def _account_type(code):
    """-> (account_type, the account name that confirms the band). Longest prefix wins."""
    for prefix, account_type, evidence in sorted(ACCOUNT_TYPE_BANDS, key=lambda b: -len(b[0])):
        if code.startswith(prefix):
            return account_type, evidence
    return "asset_current", ""


def build_chart_of_accounts():
    """
    Sheet object #1 (Chart of Accounts, MANDATORY).

    ByDesign's own "G/L Account Master Data" report declares a mandatory Chart of Accounts
    variable with no default and no readable value list, so it returns nothing. Instead this
    reads the six analytics reports that expose CGLACCT/TGLACCT without a blocking variable
    and unions them - between them they cover every G/L account in use on this tenant.
    See src/probe_gl_accounts.py for how those six were identified out of 60 candidates.

    Consequence worth stating plainly: this is the set of accounts that actually carry
    postings, not the full configured chart. An account defined in ByDesign but never posted
    to would not appear here.
    """
    accounts = {}
    for service, entity_set in GL_ACCOUNT_SOURCES:
        payload = load_raw(entity_set, service_hint=service)
        for row in payload["rows"]:
            code = (row.get("CGLACCT") or "").strip()
            if not code:
                continue
            name = (row.get("TGLACCT") or "").strip()
            if code not in accounts or (name and not accounts[code]):
                accounts[code] = name

    rows = []
    for code in sorted(accounts):
        account_type, _ = _account_type(code)
        rows.append({
            "id": external_id("sap_account", code),
            "code": code,
            "name": accounts[code] or code,
            "account_type": account_type,
            # Odoo reconciles payables and receivables; nothing else by default.
            "reconcile": "True" if account_type in ("asset_receivable", "liability_payable") else "False",
        })
    write_csv("account_account.csv", rows, ACCOUNT_FIELDNAMES)
    return {"account_account.csv": len(rows)}


SUPPLIERINFO_FIELDNAMES = [
    "id", "partner_id/id", "product_tmpl_id/id", "product_code", "product_name", "delay",
]


def build_vendor_pricelists():
    """
    Sheet object #47 (Vendor Pricelists) -> product.supplierinfo.

    vmumaterial's SupplierInformationCollection links a material to the supplier that provides
    it, with that supplier's own part number and lead time. It was being extracted but never
    read by any transform.

    No price column exists on this entity - ByDesign keeps supplier prices in price lists this
    tenant does not publish - so product_code/delay are mapped and price is left for Odoo to
    default. That is the honest subset, not a fabricated price.
    """
    links = load_raw("SupplierInformationCollection", service_hint="vmumaterial")["rows"]
    # product_template.csv keys products on InternalID (see build_products), not ObjectID.
    product_by_object = {
        m["ObjectID"]: m.get("InternalID")
        for m in load_raw("MaterialCollection", service_hint="vmumaterial")["rows"]
        if m.get("ObjectID")
    }

    rows = []
    for link in links:
        supplier_id = (link.get("SupplierID") or "").strip()
        product_id = product_by_object.get(link.get("ParentObjectID"))
        if not supplier_id or not product_id:
            continue
        # SupplierLeadTimeDuration arrives as an ISO-8601 duration ("P7D"); Odoo wants days.
        duration = (link.get("SupplierLeadTimeDuration") or "").strip()
        delay = duration[1:-1] if duration.startswith("P") and duration.endswith("D") else ""
        rows.append({
            "id": external_id("sap_supinfo", f"{supplier_id}_{product_id}"),
            "partner_id/id": external_id("sap_bp", supplier_id),
            "product_tmpl_id/id": external_id("sap_prod", product_id),
            "product_code": link.get("SupplierPartNumber") or "",
            "product_name": link.get("BusinessPartnerFormattedName") or "",
            "delay": delay,
        })
    write_csv("product_supplierinfo.csv", rows, SUPPLIERINFO_FIELDNAMES)
    return {"product_supplierinfo.csv": len(rows)}


OPEN_INVOICE_FIELDNAMES = [
    "id", "name", "partner_id/id", "invoice_date", "move_type", "state",
    "currency_id/id", "amount_total",
]


def _parse_sap_measure(value):
    """
    "1.770,00 USD" -> "1770.00";  "2.813 NOS" -> "2813.00";  "160 PCS" -> "160.00".

    ByDesign's analytics layer returns KEY FIGURES (the F*/K* fields) pre-formatted for display
    in the report's locale, with the unit or currency appended - they are not numbers. This
    tenant's locale is European: "." groups thousands, "," is the decimal separator. Confirmed
    on real data from two different reports: amounts arrive as "1.770,00 USD" and quantities as
    "20.000 NOS" / "160 PCS".

    This does NOT apply to the C* dimension fields, which come back as raw values - the tax rate
    dimension is literally "18.000000" and must be read as 18, not as 18 million. Use float()
    directly for those.
    """
    text = (value or "").strip()
    if not text:
        return ""
    number = text.split(" ")[0].replace(".", "").replace(",", ".")
    try:
        return f"{float(number):.2f}"
    except ValueError:
        return ""


def _sap_amount_currency(value):
    """The currency code trailing a formatted measure, e.g. "1.770,00 USD" -> "USD"."""
    parts = (value or "").strip().split(" ")
    return parts[1] if len(parts) > 1 else ""


def _tax_rate(value):
    """"18.000000" -> "18.0". A C* dimension, so it is a raw decimal, not a formatted measure."""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return ""


def build_open_customer_invoices():
    """
    Sheet object #8 (Open Customer Invoices, MANDATORY).

    ByDesign's "Trade Receivables Payables Register" is its open-items report: one row per
    invoice that is still unsettled, with the invoice number, the customer and the amount
    outstanding. This is the object that could not be built before - khcustomerinvoice exposes
    no payment-status field whatsoever, and matching payments back to invoices by document ID
    only ever reached 7%, which was coincidence rather than a join.

    amount_total here is the OUTSTANDING balance, not the original invoice total, because that
    is what the register reports and what an opening-balance import needs.
    """
    rows_in = load_raw("RPFINDUEU04_Q0007QueryResults",
                       service_hint="fin_receivablesar_analytics.svc")["rows"]
    known, _, _ = _partner_ranks()

    rows = []
    for item in rows_in:
        invoice_id = (item.get("CIM_B_BTD_ID") or "").strip()
        if not invoice_id:
            continue
        partner = (item.get("CIM_BP_UUID") or "").strip()
        outstanding = item.get("FCOUTSTANDING_AMNT")
        rows.append({
            "id": external_id("sap_openinv", invoice_id),
            "name": invoice_id,
            "partner_id/id": external_id("sap_bp", partner) if partner in known else "",
            "invoice_date": parse_sap_date(item.get("CIM_B_BTD_DATE")) or "",
            "move_type": "out_invoice",
            "state": "posted",
            # The register formats amounts in their own currency, which can differ from the
            # document currency in CIM_TRANSCURR - trust the amount's own suffix.
            "currency_id/id": f"base.{_sap_amount_currency(outstanding)}"
                              if _sap_amount_currency(outstanding) else "",
            "amount_total": _parse_sap_measure(outstanding),
        })
    write_csv("account_move_open_customer.csv", rows, OPEN_INVOICE_FIELDNAMES)
    return {"account_move_open_customer.csv": len(rows)}


CREDIT_NOTE_FIELDNAMES = [
    "id", "name", "partner_id/id", "invoice_date", "move_type", "state",
    "currency_id/id", "amount_total",
]


def build_credit_notes():
    """
    Sheet objects #52 (Vendor Credit Notes) and #66 (Credit Notes, customer side).

    Both were pending only because nothing had looked at the document TYPE columns. Credit
    memos are not a separate entity in ByDesign - they live in the ordinary invoice tables and
    are distinguished by TypeCodeText:
      khsupplierinvoice/SupplierInvoiceCollection      "Credit Memo"                (9)
      khcustomerinvoicerequest/...RequestCollection    "Manual Credit Memo Request" (21)

    Odoo's move_type is what makes a credit note a credit note on import: out_refund reduces a
    customer balance, in_refund reduces a vendor balance.
    """
    vendor_rows = []
    for invoice in load_raw("SupplierInvoiceCollection", service_hint="khsupplierinvoice")["rows"]:
        if invoice.get("TypeCodeText") != "Credit Memo":
            continue
        object_id = invoice.get("ObjectID")
        party = resolve_party(
            _group_by_parent(load_raw("SellerPartyCollection", service_hint="khsupplierinvoice")["rows"]),
            object_id, prefer="supplier")
        currency = invoice.get("TotalGrossAmountCurrencyCode") or ""
        vendor_rows.append({
            "id": external_id("sap_vcredit", invoice.get("ID") or object_id),
            "name": invoice.get("ID") or object_id,
            "partner_id/id": external_id("sap_bp", party) if party else "",
            "invoice_date": parse_sap_date(invoice.get("InvoiceDate")) or "",
            "move_type": "in_refund",
            "state": "posted",
            "currency_id/id": f"base.{currency}" if currency else "",
            "amount_total": invoice.get("TotalGrossAmount") or "",
        })
    write_csv("account_move_vendor_credit.csv", vendor_rows, CREDIT_NOTE_FIELDNAMES)

    customer_rows = []
    for request in load_raw("CustomerInvoiceRequestCollection",
                            service_hint="khcustomerinvoicerequest")["rows"]:
        if "Credit Memo" not in (request.get("TypeCodeText") or ""):
            continue
        # This entity names the customer directly, so no party-resolution heuristic is needed.
        party = (request.get("BuyerPartyID") or request.get("BillToPartyID") or "").strip()
        currency = request.get("TotalGrossAmountCurrencyCode") or request.get("CurrencyCode") or ""
        object_id = request.get("ObjectID")
        customer_rows.append({
            "id": external_id("sap_ccredit", request.get("BaseBusinessTransactionDocumentID") or object_id),
            "name": request.get("Name") or request.get("BaseBusinessTransactionDocumentID") or object_id,
            "partner_id/id": external_id("sap_bp", party) if party else "",
            "invoice_date": parse_sap_date(request.get("ProposedInvoiceDate")) or "",
            "move_type": "out_refund",
            "state": "posted",
            "currency_id/id": f"base.{currency}" if currency else "",
            "amount_total": request.get("TotalGrossAmount") or "",
        })
    write_csv("account_move_credit_note.csv", customer_rows, CREDIT_NOTE_FIELDNAMES)

    return {
        "account_move_vendor_credit.csv": len(vendor_rows),
        "account_move_credit_note.csv": len(customer_rows),
    }


TAX_FIELDNAMES = ["id", "name", "amount", "amount_type", "type_tax_use", "description"]


def build_taxes():
    """
    Sheet object #2 (Taxes, MANDATORY).

    "Taxes - Product Tax Details" is the only source on this tenant that carries a tax RATE.
    Every custom service exposes tax CODES on documents (khsupplierinvoice/ItemTaxCalculation,
    khcustomerinvoice/ItemPriceAndTaxCalculation) but never the percentage behind them.

    Rows are one per distinct tax combination the report groups by, so this is the set of taxes
    actually applied in this tenant - not the full configured tax table, which ByDesign does not
    publish over OData.
    """
    rows_in = load_raw("RPGLOTAXB01_Q0001QueryResults",
                       service_hint="fin_taxmanagement_analytics.svc")["rows"]

    taxes = {}
    for item in rows_in:
        rate = (item.get("CPRODTAX_RATE_PERCENT") or "").strip()
        tax_type = (item.get("CRESULT_TAX_TYPE") or "").strip()
        if not rate or not tax_type:
            continue
        region = (item.get("CCIV_LOCATION_REGION") or "").strip()
        event = (item.get("CRESULT_TAX_EVENT") or "").strip()
        key = (tax_type, rate, region)
        if key in taxes:
            continue
        label = " ".join(p for p in (tax_type, f"{rate}%", region) if p)
        taxes[key] = {
            "id": external_id("sap_tax", "_".join(p for p in (tax_type, rate, region) if p)),
            "name": label,
            "amount": _tax_rate(rate),
            "amount_type": "percent",
            # ByDesign does not label a tax as sales-side or purchase-side on this report;
            # "sale" is Odoo's default and the safer of the two to review after import.
            "type_tax_use": "sale",
            "description": event,
        }
    rows = sorted(taxes.values(), key=lambda t: t["name"])
    write_csv("account_tax.csv", rows, TAX_FIELDNAMES)
    return {"account_tax.csv": len(rows)}


QUANT_FIELDNAMES = ["id", "product_id/id", "location_id/id", "inventory_quantity", "product_uom_id/id"]


def build_inventory():
    """
    Sheet object #31 (Inventory Adjustments) -> stock.quant opening balances.

    "Inventory Balance" grouped by material x logistics area x site, which is exactly Odoo's
    stock.quant grain. TMATERIAL_UUID/TLOG_AREA_UUID carry the readable product and location
    names behind the UUID keys.
    """
    rows_in = load_raw("RPSCMINBU03_Q0001QueryResults",
                       service_hint="scm_physicalinventory_analytics.svc")["rows"]

    rows = []
    for item in rows_in:
        product = (item.get("CMATERIAL_UUID") or "").strip()
        quantity = _parse_sap_measure(item.get("FCENDING_QUANTITY"))
        if not product or not quantity or float(quantity) == 0:
            continue
        # CLOG_AREA_UUID is "<site>/<area>", the same pair build_locations keys its storage
        # areas on - external_id() normalises both to the identical external ID.
        area = (item.get("CLOG_AREA_UUID") or item.get("CSITE_UUID") or "").strip()
        unit = (item.get("CINV_UNIT") or "").strip()
        rows.append({
            "id": external_id("sap_quant", f"{product}_{area}"),
            "product_id/id": external_id("sap_prod", product),
            "location_id/id": external_id("sap_loc", area) if area else "",
            "inventory_quantity": quantity,
            "product_uom_id/id": external_id("sap_uom", unit) if unit else "",
        })
    write_csv("stock_quant_adjustment.csv", rows, QUANT_FIELDNAMES)
    return {"stock_quant_adjustment.csv": len(rows)}


# Sheet object #30 (Lot/Serial Numbers) has no usable source on this tenant and is deliberately
# NOT written. khproductionorder/ProductionLotCollection holds 145 rows but exposes exactly two
# fields - ObjectID and ID - with no ParentObjectID and no product reference of any kind, so the
# lots cannot be attached to a product. Odoo's stock.lot requires product_id, so a file built
# from this would be 100% unimportable while appearing in the status reports as "done".
# The fix is an import, not code: khgoodsandactivityconfirmation.xml carries SerialNumber and
# IdentifiedStock with their material links - see SAP_IMPORT_PLAN.md, priority 1.


TRANSFORMS = [
    ("account_account (chart of accounts)", build_chart_of_accounts),
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
    ("product_supplierinfo (vendor pricelists)", build_vendor_pricelists),
    ("account_move_open_customer (open customer invoices)", build_open_customer_invoices),
    ("account_tax (taxes)", build_taxes),
    ("account_move credit notes (customer + vendor)", build_credit_notes),
    ("stock_quant_adjustment (inventory balances)", build_inventory),
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
