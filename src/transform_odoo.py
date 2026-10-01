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
    Load a raw JSON file by entity set name, optionally narrowed to one service.

    Entity set names are NOT unique across services - ItemCollection exists on nine of them,
    SellerPartyCollection on three, SalesCollection and SupplierCollection on two each. This
    used to return whichever file os.listdir happened to yield first, which silently binds a
    transform to the wrong service the moment a new service is imported. It now raises instead,
    so the failure is a loud error at run time rather than wrong numbers in a CSV.
    """
    matches = sorted(
        f for f in os.listdir(RAW_DIR)
        if f.endswith(f"__{entity_set}.json")
        and (not service_hint or f.startswith(f"{service_hint}__"))
    )
    if not matches:
        raise FileNotFoundError(
            f"No raw file for entity set '{entity_set}'"
            + (f" (service_hint={service_hint!r})" if service_hint else "")
            + f" in {RAW_DIR}/ - run `python -m src.extract_raw` first."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Entity set '{entity_set}' exists on {len(matches)} services "
            f"({', '.join(m.split('__')[0] for m in matches)}). "
            f"Pass service_hint=... to say which one is meant."
        )
    with open(os.path.join(RAW_DIR, matches[0]), encoding="utf-8") as f:
        return json.load(f)


def write_csv(filename, rows, fieldnames):
    path = _write_csv(ODOO_DIR, filename, rows, fieldnames)
    logger.info("Wrote %s (%d rows)", path, len(rows))
    return path


def _clean_text(value):
    """
    Replace a literal straight double-quote inside a free-text field (e.g. a product
    description like 2.76"L X 1.97"W) with the Unicode double-prime (U+2033). The CSV itself
    was always valid RFC 4180 (Python's csv module correctly doubles embedded quotes), but a
    quoted field ending in an escaped quote immediately followed by more text is a known rough
    edge for some CSV viewers (notably Excel's quick-open path), which can visually show the
    rest of the field spilling into the next cell. Swapping the character removes the ambiguity
    entirely without changing the meaning (a straight quote after a number is the inch mark, and
    U+2033 is the correct typographic symbol for it anyway). 2026-10-01.
    """
    if not value:
        return value
    return value.replace('"', "″")


def _country_ref(code):
    code = (code or "").strip().lower()
    return f"base.{code}" if code else ""


def _bool(value):
    return "False" if value in (True, "true", "True") else "True"


PARTNER_FIELDNAMES = [
    "id", "name", "street", "city", "zip", "country_id/id", "phone", "email", "website",
    "vat", "customer_rank", "supplier_rank", "active", "industry", "payment_terms",
    "incoterms", "incoterms_location", "purchase_order_currency", "order_block_reason",
    "delivery_block", "invoice_block", "is_bidder", "is_warehouse_provider",
    "is_freight_forwarder",
]

# BP role codes confirmed live via khsupplier/RoleRoleCodeCollection, 2026-09-28.
ROLE_CODE_BIDDER = "BBP001"
ROLE_CODE_WAREHOUSE_PROVIDER = "SCM002"
ROLE_CODE_FREIGHT_FORWARDER = "CRMS04"

BANK_FIELDNAMES = ["id", "name", "country_id/id"]
PARTNER_BANK_FIELDNAMES = ["id", "partner_id/id", "bank_id/id", "acc_number"]
CONTACT_FIELDNAMES = ["id", "name", "parent_id/id", "function", "type"]
ANALYTIC_PLAN_FIELDNAMES = ["id", "name"]
COST_CENTER_FIELDNAMES = ["id", "name", "code", "plan_id/id"]
# Odoo requires every analytic account to sit in a plan; cost centers (#6) and profit
# centres (#7) share this one synthetic plan.
ANALYTIC_PLAN_EXTERNAL_ID = "sap_analytic_plan_cost_centers"


def build_cost_centers():
    """Map ByDesign CostCentreCollection records to Odoo analytic accounts."""
    cost_centers = load_raw("CostCentreCollection", service_hint="costcentre")["rows"]
    plan_id = ANALYTIC_PLAN_EXTERNAL_ID

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

    Extended 2026-09-28 after the user pasted a list of Customer/Vendor screen fields missing
    from this file. industry/payment_terms/incoterms/incoterms_location/purchase_order_currency/
    order_block_reason/delivery_block/invoice_block were already sitting in already-extracted
    CustomerCollection/SupplierCollection fields, just never read by this transform.
    is_bidder/is_warehouse_provider/is_freight_forwarder are derived from khsupplier's
    RoleCollection (RoleCode BBP001/SCM002/CRMS04, confirmed live against
    RoleRoleCodeCollection) - also already extracted, just unused. payment_terms/incoterms/
    incoterms_location/purchase_order_currency needed one real fix: khsupplier/
    SupplierCollection is now pulled with $expand=PurchasingData, because
    PurchasingDataCollection's own rows carry no ParentObjectID back to the supplier (same
    "child entity you can't join, only expand" situation as khproductionorder/
    MainProductOutput) - see field_validation/02_customers_vendors/README.md.

    Checked and confirmed NOT available anywhere on this tenant, not just unmapped: Additional
    Name, Trade Name, Non-Company, Minimum Purchase Order Value, Certified According To/Valid
    To, ERS Invoice Number Prefix, Calendar Year as Suffix, Restart Doc ID Each Cal Year - all
    visible on the live Supplier screen but not present as a property anywhere in khsupplier's
    Business Object tree (checked via the OData Editor's full field list, not just $metadata).
    Same for Payment Terms/Incoterms/Incoterms Location on the CUSTOMER side specifically - that
    data lives on a separate CRM Account object this pipeline doesn't read, not on khcustomer.
    """
    customers = load_raw("CustomerCollection", service_hint="khcustomer")["rows"]
    suppliers = load_raw("SupplierCollection", service_hint="khsupplier")["rows"]

    # Role assignments (Bidder/Warehouse Provider/Freight Forwarder), added 2026-09-28 after
    # the user compared res_partner.csv against the live Customer/Vendor screens and found
    # these missing. Real fields, already sitting fully extracted - RoleCollection just needed
    # to be read by this transform. RoleCode set membership per supplier, not a single field.
    roles_by_supplier = _join_by_parent(
        load_raw("RoleCollection", service_hint="khsupplier")["rows"])

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
        role_codes = {r.get("RoleCode") for r in roles_by_supplier.get(object_id, [])}
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
            "industry": record.get("IndustrialSectorCodeText", ""),
            # Supplier-side only (khcustomer's CustomerCollection has no equivalent
            # PurchasingData/SalesData node on this tenant - checked live, 2026-09-28).
            "payment_terms": record.get("PurchasingData.PaymentTermsCodeText", ""),
            "incoterms": record.get("PurchasingData.IncotermsCodeText", ""),
            "incoterms_location": record.get("PurchasingData.IncotermsLocationName", ""),
            "purchase_order_currency": record.get("PurchasingData.PurchaseOrderCurrencyCodeText", ""),
            # Customer-side only (khsupplier's SupplierCollection has no equivalent block
            # reason fields on this tenant - checked live, 2026-09-28).
            "order_block_reason": record.get("OrderBlockingReasonCodeText", ""),
            "delivery_block": record.get("FulfilmentBlockingReasonCodeText", ""),
            "invoice_block": record.get("InvoicingBlockingReasonCodeText", ""),
            "is_bidder": "True" if ROLE_CODE_BIDDER in role_codes else "False",
            "is_warehouse_provider": "True" if ROLE_CODE_WAREHOUSE_PROVIDER in role_codes else "False",
            "is_freight_forwarder": "True" if ROLE_CODE_FREIGHT_FORWARDER in role_codes else "False",
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
            # customer-side record left blank. Booleans OR together instead - the customer-side
            # branch always sets is_bidder/etc. to "False" (roles are only ever looked up on
            # the supplier record), so a fill-blank-only merge would silently lose a real
            # supplier-side "True" behind the customer row's "False" (a real bug caught while
            # adding these fields, 2026-09-28).
            existing["supplier_rank"] = "1"
            for field in ("is_bidder", "is_warehouse_provider", "is_freight_forwarder"):
                if row.get(field) == "True":
                    existing[field] = "True"
            for field, value in row.items():
                if field in ("is_bidder", "is_warehouse_provider", "is_freight_forwarder"):
                    continue
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


PO_HEADER_FIELDNAMES = ["id", "name", "partner_id/id", "date_order", "state", "currency_id/id",
                        "amount_total", "amount_tax", "incoterms", "incoterms_location",
                        "buyer_responsible_name", "payment_terms"]
PO_LINE_FIELDNAMES = ["id", "order_id/id", "product_id/id", "name", "product_qty", "price_unit", "price_tax"]

# LifeCycleStatusCodeText values that mean "not yet a real order" - these orders are written to
# purchase_order_rfq.csv (build_rfqs(), below) instead of purchase_order.csv, so the same real
# SAP order never appears in both files under different external-ID prefixes.
_RFQ_STAGE_STATES = {"In Preparation", "In Approval"}


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

    Orders still in "In Preparation"/"In Approval" (LifeCycleStatusCodeText) are excluded here -
    build_rfqs() below already writes those same real orders to purchase_order_rfq.csv as
    draft/sent purchase.order records. Both files used to draw from every order with no
    exclusion, so the ~139 RFQ-stage orders existed twice under different external-ID prefixes
    (sap_po_* here, sap_rfq_* there) - importing both would have created duplicate purchase.order
    records in Odoo for the same real SAP order. Fixed 2026-09-30.

    Two more real gaps fixed 2026-10-01 (team pre-import check):
    1. price_tax now carries ItemCollection's real TaxAmount - present on every line (confirmed
       via live $metadata, no TaxCode/tax-rate field exists on this entity, only the computed
       monetary amount, so this is informational, not a taxes_id/id relation to account_tax.csv).
    2. product_id/id now goes through the same _known_product_ids() guard already used on
       customer-invoice/vendor-bill lines, instead of trusting any non-blank ProductID. Checked
       the remaining ~40% blank product_id/id directly against output_raw/ first: 18,367/45,981
       raw PO items have NO ProductID in SAP at all (ItemTypeCode mostly "Material", real
       descriptions like "F2 LASER CUT STENCILS" - genuine free-text/non-catalog PO lines SAP
       allows without a Material Master link, not something any field in the source can resolve
       further). 0 of the remaining ProductID-bearing lines pointed at an unknown/deleted product
       on this tenant at last check, but the guard is now applied here too for consistency.

    Two more real fields added 2026-10-01 (team pre-import check, found via the live SAP UI -
    both were already accessible via the API, just never read by this transform):
    1. incoterms/incoterms_location - PurchaseOrderCollection's own IncotermsCodeText/
       IncotermsLocationName fields (confirmed in output_raw/ all along).
    2. buyer_responsible_name - EmployeeResponsibleCollection (already extracted, ParentObjectID
       -> header ObjectID), resolved to a real name via _employee_name_by_code(). Like
       SupplierCollection, this bundles several unrelated party roles per PO with no role code
       (own-company "70000", the supplier's own BP id, AND the real employee code) - confirmed
       on PO 30016303: candidates were ["70000", "G020", "70000", "8000755", "G232"], and only
       "G020"/"G232" are real EmployeeIDs in khemployee. Picks the first candidate that's a known
       EmployeeID rather than the first row, matching the live UI's "Buyer Responsible: G020 -
       Maryann Fernandes" for that PO exactly.

    Two more closed 2026-10-01: amount_tax (header-level, from PurchaseOrderCollection's own
    TotalTaxAmount - already present, just unmapped) and payment_terms. Payment Terms turned out
    to NOT need a live OData Editor change here, unlike Sales Orders (see build_sales_orders()) -
    khpurchaseorder's own metadata.xml snapshot already had a "PaymentTerms" EntityType and
    PurchaseOrder_PaymentTerms navigation property, just never added to extract_raw.py's
    SOURCES. Pulled live 2026-10-01 (15,835 rows, one per PO) and wired in directly.
    """
    headers = load_raw("PurchaseOrderCollection")["rows"]
    items = load_raw("ItemCollection", service_hint="khpurchaseorder")["rows"]
    suppliers = load_raw("SupplierCollection", service_hint="khpurchaseorder")["rows"]
    employees_responsible = load_raw("EmployeeResponsibleCollection", service_hint="khpurchaseorder")["rows"]
    payment_terms = load_raw("PaymentTermsCollection", service_hint="khpurchaseorder")["rows"]

    # SupplierCollection bundles several party roles per PO (3529 rows for 655 POs), so taking
    # the first row picked the wrong party or an empty one 33% of the time. Type-aware
    # resolution (prefer a PartyID that is a known SUPPLIER in res_partner.csv) measured 87%
    # valid vs 67% for first-row.
    party_by_po = _group_by_parent(suppliers)
    employee_by_po = _group_by_parent(employees_responsible)
    employee_names = _employee_name_by_code()
    payment_terms_by_po = {p["ParentObjectID"]: p.get("PaymentTermsCodeText", "")
                            for p in payment_terms if p.get("ParentObjectID")}

    header_rows = []
    known_po_ids = set()
    for header in headers:
        object_id = header.get("ObjectID")
        if not object_id:
            continue
        if header.get("LifeCycleStatusCodeText") in _RFQ_STAGE_STATES:
            continue
        known_po_ids.add(object_id)
        partner_bp = resolve_party(party_by_po, object_id, prefer="supplier")
        buyer_code = next(
            (e.get("PartyID") for e in employee_by_po.get(object_id, [])
             if e.get("PartyID") in employee_names), None
        )
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
                "amount_tax": header.get("TotalTaxAmount", "") or "0",
                "incoterms": header.get("IncotermsCodeText", ""),
                "incoterms_location": header.get("IncotermsLocationName", ""),
                "buyer_responsible_name": employee_names.get(buyer_code, "") if buyer_code else "",
                "payment_terms": payment_terms_by_po.get(object_id, ""),
            }
        )

    line_rows = []
    for item in items:
        po_object_id = item.get("ParentObjectID")
        if po_object_id not in known_po_ids:
            continue
        product_id = item.get("ProductID")
        product_known = product_id and product_id in _known_product_ids()
        line_rows.append(
            {
                "id": external_id("sap_po_item", item.get("ObjectID")),
                "order_id/id": external_id("sap_po", po_object_id),
                "product_id/id": external_id("sap_prod", product_id) if product_known else "",
                "name": _clean_text(item.get("Description")) or product_id or item.get("ID", ""),
                "product_qty": item.get("Quantity", "") or "0",
                "price_unit": item.get("NetUnitPriceAmount", "") or "0",
                "price_tax": item.get("TaxAmount", "") or "0",
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
    purchasing = load_raw("PurchasingCollection", service_hint="vmumaterial")["rows"]
    sales = load_raw("SalesCollection", service_hint="vmumaterial")["rows"]
    categories = load_raw("ProductCategoryCollection", service_hint="vmumaterial")["rows"]
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
            # khserviceproductvaluationdata IS imported now, and its CostRateCollection joins
            # cleanly (7/7). Every one of those 7 rates is 0.000000, so writing them would
            # assert "this service costs nothing" where the truth is "no cost is maintained".
            # Left blank, like barcode/weight on materials.
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

_KNOWN_PRODUCT_IDS_CACHE = None


def _known_product_ids():
    """
    default_code values already written to product_template.csv (read once per run). Used to
    blank out a product_id/id reference on old transaction lines whose ProductID is a real SAP
    value but names a material that's since been deleted/obsoleted from the live product
    master - a genuine historical gap, not something to fabricate a link for. Caught on
    account_move_customer_invoice_line.csv (194/35,514 lines, 0.5%), 2026-09-29.
    """
    global _KNOWN_PRODUCT_IDS_CACHE
    if _KNOWN_PRODUCT_IDS_CACHE is None:
        ids = set()
        try:
            with open(os.path.join(ODOO_DIR, "product_template.csv"), newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    ids.add(row.get("default_code", ""))
        except FileNotFoundError:
            pass
        _KNOWN_PRODUCT_IDS_CACHE = ids
    return _KNOWN_PRODUCT_IDS_CACHE


def _is_employee_id(party_id):
    return len(party_id) == 10 and party_id.startswith("8")


_EMPLOYEE_NAME_BY_CODE_CACHE = None


def _employee_name_by_code():
    """
    EmployeeID -> FormattedName, from khemployee/EmployeeCollection. Confirmed live 2026-10-01
    (team pre-import check, via the live SAP UI): khpurchaseorder/EmployeeResponsibleCollection's
    PartyID uses this same short EmployeeID code (e.g. "G020"), not khemployee's own ObjectID/
    InternalID - a clean, confirmed join, not a guess (G020 resolves to "Maryann Fernandes",
    matching the "Buyer Responsible" field shown on the live Purchase Order screen exactly).
    """
    global _EMPLOYEE_NAME_BY_CODE_CACHE
    if _EMPLOYEE_NAME_BY_CODE_CACHE is None:
        _EMPLOYEE_NAME_BY_CODE_CACHE = {
            e["EmployeeID"]: e.get("FormattedName")
            for e in load_raw("EmployeeCollection")["rows"] if e.get("EmployeeID")
        }
    return _EMPLOYEE_NAME_BY_CODE_CACHE


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
    # No candidate on this document is a known partner (res_partner.csv) at all - real but rare
    # (3/55,261 vendor bills, caught by a team cross-validation pass, 2026-09-29): SAP's own
    # PartyID here is a generic/group code (e.g. "G113") that never became a full Business
    # Partner record. Returning it anyway used to produce a broken partner_id/id reference Odoo
    # would reject on import; returning None instead leaves the field genuinely blank.
    return None


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


SO_HEADER_FIELDNAMES = ["id", "name", "partner_id/id", "date_order", "state", "currency_id/id",
                        "amount_total", "amount_tax", "salesperson_name"]
SO_LINE_FIELDNAMES = ["id", "order_id/id", "product_id/id", "name", "price_subtotal",
                      "price_unit", "product_uom_qty", "product_uom/id", "discount"]

# CancellationStatusCode, decoded via khsalesorder's own field (checked live, 2026-09-28):
# 1=Not Canceled, 4=Canceled, 5=Partially Canceled.
_SO_CANCELED_CODES = {"4", "5"}


def build_sales_orders():
    """
    khsalesorder custom service - mandatory sheet object #63.
    SalesOrderCollection (header) + ItemCollection (lines, ParentObjectID -> header ObjectID) +
    ItemProductCollection (ProductID, ParentObjectID -> Item.ObjectID - CONFIRMED by testing) +
    BuyerPartyCollection (see resolve_party() above for why this isn't a direct field lookup).

    Two real gaps fixed 2026-09-28, caught by a team cross-validation pass before Odoo import:

    1. state was hardcoded "sale" for every order, but CancellationStatusCode shows 47.8%
       (2,060/4,308) of orders are actually Canceled (4) or Partially Canceled (5) - neither
       reached the CSV, so canceled orders would have imported as active. Now maps to Odoo's
       'cancel' state for both; a real distinction between full and partial cancellation isn't
       representable in stock stock.order's state field without a custom field, so both use the
       same core state rather than fabricating one.
    2. sale_order_line.csv had no quantity or UoM at all - every line would import at qty 0.
       Real quantity/UoM lives in ItemScheduleLineCollection (23,731 rows, ParentObjectID ->
       Item.ObjectID), which was extracted but never read by this transform. An item can carry
       several schedule lines (partial delivery dates splitting one ordered quantity across
       several confirmed dates - confirmed live: 9,875/9,920 items have more than one). Quantity
       is summed within a TypeCode ('Confirmed' preferred over 'Requested' - Confirmed is what
       SAP actually committed to deliver; only 9,920/9,921 items have any schedule line at all,
       the rest keep quantity 0 as a real, not fabricated, absence).

    Sales Team / Salesperson / Payment Terms were all requested on this object 2026-10-01.
    Payment Terms: confirmed absent, twice over. First via khsalesorder's live $metadata (no
    PaymentTerms-equivalent property anywhere - only PaymentControl, form/blocking/reference, no
    terms; and PricingTerms, currency/price-date, no terms). Then, since Purchase Orders turned
    out to have a real-but-never-extracted PaymentTerms node (see build_purchase_orders()) that
    the metadata snapshot alone wouldn't have shown was addable without checking the OData
    Editor directly, the same check was done live for Sales Orders too: opened khsalesorder in
    SAP's OData Editor (Application and User Management -> OData Services -> Custom OData
    Services) and scanned every root-level node on the underlying standard SalesOrder Business
    Object itself (not just the custom service) alphabetically - it goes straight from
    PaymentControl to PeriodTerms, nothing in between. So this is a genuine structural gap on the
    standard Business Object, not something addable by exposing more of it (unlike Purchase
    Orders) - the "30 days net" value visible on the live Sales Order UI is computed/displayed
    from elsewhere (most likely the customer's own default payment terms at display time), not
    stored as a queryable field on the Sales Order itself. No OData Editor change was made (the
    service was opened read-only and closed without saving).

    salesperson_name: real data, now wired in. SalesUnitPartyCollection bundles several party
    roles per order (34,601 rows for 4,308 orders) with no role code to tell them apart (just
    ObjectID/ParentObjectID/PartyID, confirmed via live $metadata) - same shape as
    khpurchaseorder's SupplierCollection. Its FormattedName lives on a separate child entity
    (SalesUnitPartyName) with NO foreign key back to the parent row as a plain OData property -
    only reachable via $expand (extract_raw.py now pulls SalesUnitPartyCollection with
    expand=SalesUnitPartyName; also caught and fixed a real bug in _flatten_expanded() while
    wiring this up - it only handled an expanded nav arriving as a dict or {"results": [...]},
    not a bare list, which is the shape this particular property came back in). Resolution:
    exclude whichever candidate's PartyID matches the order's own resolved customer
    (partner_id/id above - confirmed via sample data these duplicate the customer's own name,
    e.g. "Dellorto India Pvt. Ltd." showing up as a SalesUnitParty row too), then take the first
    remaining candidate's name - SAP has already resolved it server-side, whether the underlying
    party is an individual employee or a sales org unit (of 219 distinct non-customer PartyIDs,
    only 2 match a real employee's InternalID in khemployee; the rest are a different numeric
    range this pipeline has no separate master data for). Plain text, not a user_id/id relation,
    because most of these codes don't resolve to a specific res.users record - exposing the real
    name beats fabricating a link.

    Two more closed 2026-10-01 (team request, Tax + Discount confirmed missing on both SO/PO
    lines): amount_tax (header) is SalesOrderCollection's own TaxAmount field - already present,
    just unmapped (matches the "Tax: X INR" total shown under Items on the live UI). discount
    (line) is ItemPriceComponentCollection's "Product Discount (%)" component, keyed by Item
    ObjectID - already extracted, just unused. Line-level Tax was investigated too but NOT added:
    every one of the 9,947 "Tax" price components on this tenant has CalculatedAmount 0 (real
    tax lives only at the header level here); the real per-line "Discount" component was already
    found to be 0 tenant-wide for sheet object #60 (Discount Rules - see build_pricelist_items()'s
    docstring, "this tenant has no discount rules"), confirmed again here (basically 0 across all
    148,805 item price components, one single non-zero exception) - added anyway for
    completeness/transparency rather than omitted, since the field is real, just empty.

    price_unit added 2026-10-01 (team question: "is unit price there?" - it wasn't). Sourced
    from ItemPriceComponentCollection's "List Price" component (DecimalValue, already a per-1-
    unit amount - BaseDecimalValue is 1 on every row checked). Real finding while adding this:
    the List Price component's own CalculationBasisQuantity (the quantity its CalculatedAmount
    was actually priced against) does NOT always match the "Confirmed" schedule-line quantity
    this transform uses for product_uom_qty - checked directly, 4,662/9,902 lines (47%) differ.
    This is a genuine SAP data characteristic, not a transform bug: pricing is locked in against
    whatever quantity was on the order at pricing time, while the schedule line's "Confirmed"
    quantity reflects what was *later* actually committed for delivery - the two fields answer
    different questions and are expected to diverge when a quantity changes after pricing. Do
    not derive price_unit as price_subtotal/product_uom_qty; use this real field instead, which
    stays correct regardless of that divergence.
    """
    headers = load_raw("SalesOrderCollection")["rows"]
    items = load_raw("ItemCollection", service_hint="khsalesorder")["rows"]
    item_products = load_raw("ItemProductCollection")["rows"]
    parties = load_raw("BuyerPartyCollection", service_hint="khsalesorder")["rows"]
    schedule_lines = load_raw("ItemScheduleLineCollection", service_hint="khsalesorder")["rows"]
    sales_units = load_raw("SalesUnitPartyCollection", service_hint="khsalesorder")["rows"]
    item_price_components = load_raw("ItemPriceComponentCollection", service_hint="khsalesorder")["rows"]

    party_by_order = _group_by_parent(parties)
    product_by_item = {p["ParentObjectID"]: p.get("ProductID") for p in item_products if p.get("ParentObjectID")}
    sales_unit_by_order = _group_by_parent(sales_units)
    discount_by_item = {
        c["ParentObjectID"]: c.get("DecimalValue", "0")
        for c in item_price_components if c.get("TypeCodeText") == "Product Discount (%)"
    }
    unit_price_by_item = {
        c["ParentObjectID"]: c.get("DecimalValue", "0")
        for c in item_price_components if c.get("TypeCodeText") == "List Price"
    }

    def salesperson_name(order_object_id, customer_party_id):
        for candidate in sales_unit_by_order.get(order_object_id, []):
            if candidate.get("PartyID") == customer_party_id:
                continue
            name = candidate.get("SalesUnitPartyName.FormattedName")
            if name:
                return name
        return ""

    schedule_by_item = {}
    for s in schedule_lines:
        parent = s.get("ParentObjectID")
        if parent:
            schedule_by_item.setdefault(parent, []).append(s)

    def item_quantity_and_uom(item_object_id):
        lines = schedule_by_item.get(item_object_id, [])
        for preferred_type in ("Confirmed", "Requested"):
            matching = [s for s in lines if s.get("TypeCodeText") == preferred_type]
            if matching:
                total_qty = sum(float(s.get("Quantity") or 0) for s in matching)
                unit_code = matching[0].get("unitCode", "")
                return str(total_qty), unit_code
        return "0", ""

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
                "state": "cancel" if h.get("CancellationStatusCode") in _SO_CANCELED_CODES else "sale",
                "currency_id/id": (
                    f"base.{h.get('NetAmountCurrencyCode', '').strip().upper()}"
                    if h.get("NetAmountCurrencyCode") else ""
                ),
                "amount_total": h.get("NetAmount", "") or "0",
                "amount_tax": h.get("TaxAmount", "") or "0",
                "salesperson_name": salesperson_name(object_id, partner),
            }
        )

    line_rows = []
    for item in items:
        parent = item.get("ParentObjectID")
        if parent not in known_ids:
            continue
        object_id = item.get("ObjectID")
        product_id = product_by_item.get(object_id)
        qty, unit_code = item_quantity_and_uom(object_id)
        line_rows.append(
            {
                "id": external_id("sap_so_item", object_id),
                "order_id/id": external_id("sap_so", parent),
                "product_id/id": external_id("sap_prod", product_id) if product_id else "",
                "name": _clean_text(item.get("Description")) or product_id or item.get("ID", ""),
                "price_subtotal": item.get("NetAmount", "") or "0",
                "price_unit": unit_price_by_item.get(object_id, "0"),
                "product_uom_qty": qty,
                "product_uom/id": external_id("sap_uom", unit_code) if unit_code else "",
                "discount": discount_by_item.get(object_id, "0"),
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
        product_known = product_id and product_id in _known_product_ids()
        line_rows.append(
            {
                "id": external_id("sap_cinv_item", item.get("ObjectID")),
                "move_id/id": external_id("sap_cinv", parent),
                "product_id/id": external_id("sap_prod", product_id) if product_known else "",
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
    parties = load_raw("SellerPartyCollection", service_hint="khsupplierinvoice")["rows"]
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
        product_known = product_id and product_id in _known_product_ids()
        line_rows.append(
            {
                "id": external_id("sap_vinv_item", item.get("ObjectID")),
                "move_id/id": external_id("sap_vinv", parent),
                "product_id/id": external_id("sap_prod", product_id) if product_known else "",
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
    khpayment custom service (sheet objects #10/11, Customer/Vendor Payments).
    Splits by looking up BusinessPartnerID against the customer_rank/supplier_rank already
    established in res_partner.csv (built earlier in the pipeline) rather than guessing from
    this entity's own fields, which don't distinguish payment direction cleanly.

    Fixed 2026-09-28, caught by a team cross-validation pass before Odoo import: this used to
    emit `partner_id/id` for EVERY payment with a real BusinessPartnerID, even when that ID
    wasn't in res_partner.csv - a broken external-id reference Odoo would reject on import.
    563/59,493 payments (0.9%) carry a BusinessPartnerID that's a real Business Partner (found
    in khbusinesspartner/BusinessPartnerCollection) but neither a customer nor a supplier, so it
    was never built into res_partner.csv - not this transform's data to fabricate a fix for.
    5,133/59,493 (8.6%) have no BusinessPartnerID at all on SAP's side - a real absence, not a
    join failure. Both cases now leave partner_id/id blank instead of pointing at a record that
    doesn't exist, and partner_type/payment_type fall back to "customer"/"inbound" (SAP's own
    default direction) since there's no rank to check.
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
        rank_row = partner_rank.get(bp_id)
        is_supplier = bool(rank_row) and rank_row.get("supplier_rank") == "1"
        rows.append(
            {
                "id": external_id("sap_pay", object_id),
                "partner_id/id": external_id("sap_bp", bp_id) if rank_row else "",
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


PRICELIST_FIELDNAMES = ["id", "name", "currency_id/id", "partner_id/id"]


def build_pricelists():
    """
    khsalesarrangement custom service - 225 arrangements (sheet objects #59/60/61, Pricelists/
    Discount Rules/Customer Price Lists).

    partner_id/id: CustomerUUID is a hyphenated GUID that doesn't match res_partner's InternalID-
    based external IDs directly - closed 2026-09-28 via khcustomer/CustomerCollection, which
    (like vmumaterial's MaterialCollection) carries both its own dashed UUID and the numeric
    InternalID res_partner.csv's external IDs are built from. Confirmed clean: all 225/225
    arrangements resolve. This also closes #61 (Customer Price Lists) - same file, now
    genuinely "by customer" rather than anonymous, so no separate file is needed for it.
    """
    arrangements = load_raw("SalesArrangementCollection")["rows"]
    customers = load_raw("CustomerCollection", service_hint="khcustomer")["rows"]
    internal_id_by_uuid = {c["UUID"]: c.get("InternalID") for c in customers if c.get("UUID")}

    rows = []
    for arr in arrangements:
        object_id = arr.get("ObjectID")
        if not object_id:
            continue
        internal_id = internal_id_by_uuid.get(arr.get("CustomerUUID"))
        rows.append(
            {
                "id": external_id("sap_pricelist", object_id),
                "name": f"SAP Sales Arrangement {arr.get('ObjectID', '')[:12]}",
                "currency_id/id": (
                    f"base.{arr.get('CurrencyCode', '').strip().upper()}"
                    if arr.get("CurrencyCode") else ""
                ),
                "partner_id/id": external_id("sap_bp", internal_id) if internal_id else "",
            }
        )
    # Fallback pricelist for the small number of orders build_pricelist_items() can't resolve to
    # one of the real arrangements above (see _resolve_order_to_arrangement()'s docstring).
    # Written here, not there, so every product.pricelist.item's pricelist_id/id always points
    # at a row that exists in this same file.
    rows.append({
        "id": LIST_PRICE_PRICELIST_ID,
        "name": "SAP List Prices - Unmatched Sales Arrangement",
        "currency_id/id": "base.INR",
    })
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
    ("fin_generalledger_analytics.svc", "RPFINGLAU03_Q0001QueryResults"),
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

    A THIRD customer-side source was added 2026-09-24: khcustomerreturn/CustomerReturnCollection
    (found by coverage_gap.py - a whole service that had never been wired into SOURCES at all).
    A "Customer Return" in ByDesign is physical goods coming back, but its CreditMemoStatusCode
    means every finished one produces an actual credit memo - 796 of 810 rows here have
    CreditMemoStatusCodeText "Finished". These are additional real credit notes, not a
    duplicate of the khcustomerinvoicerequest set (checked: no ObjectID overlap, since they are
    a structurally different document with its own ID sequence), so they're unioned into the
    same account_move_credit_note.csv rather than kept as a separate Odoo object - a return-driven
    credit note and a manually-requested one are the same account.move concept in Odoo.

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
    return_parties = _group_by_parent(
        load_raw("BuyerPartyCollection", service_hint="khcustomerreturn")["rows"])
    for ret in load_raw("CustomerReturnCollection", service_hint="khcustomerreturn")["rows"]:
        if ret.get("CreditMemoStatusCodeText") != "Finished":
            continue
        object_id = ret.get("ObjectID")
        party = resolve_party(return_parties, object_id, prefer="customer")
        currency = ret.get("CurrencyCode") or ""
        # Unlike the other two sources, this entity's GrossAmount is signed negative (goods
        # coming back reduce revenue); Odoo's amount_total expects a positive magnitude, with
        # move_type=out_refund already conveying the direction - matches the positive
        # TotalGrossAmount convention the supplier/customer-invoice-request sources use above.
        amount = ret.get("GrossAmount") or ""
        try:
            amount = str(abs(float(amount)))
        except ValueError:
            pass
        customer_rows.append({
            "id": external_id("sap_creturn", ret.get("ID") or object_id),
            "name": ret.get("Name") or ret.get("ID") or object_id,
            "partner_id/id": external_id("sap_bp", party) if party else "",
            "invoice_date": parse_sap_date(ret.get("DateTime")) or "",
            "move_type": "out_refund",
            "state": "posted",
            "currency_id/id": f"base.{currency}" if currency else "",
            "amount_total": amount,
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


ANALYTIC_ACCOUNT_FIELDNAMES = ["id", "name", "code", "plan_id/id"]


def build_profit_centres():
    """
    Sheet object #7 (Analytic Accounts) -> account.analytic.account.

    ByDesign's second analytic dimension alongside cost centers (#6, which already writes
    account_analytic_account_cc.csv under the same synthetic analytic plan). ProfitCentreCollection
    carries only ID/ObjectID/UUID; the readable name lives in NameCollection, which is
    date-versioned - the row whose validity window is open (EndDate far in the future) is the
    current name.
    """
    centres = load_raw("ProfitCentreCollection", service_hint="khprofitcentre")["rows"]
    names = {}
    for entry in load_raw("NameCollection", service_hint="khprofitcentre")["rows"]:
        parent = (entry.get("ParentObjectID") or "")[:32]
        if parent and entry.get("Name") and parent not in names:
            names[parent] = entry["Name"]

    rows = []
    for centre in centres:
        code = centre.get("ID")
        if not code:
            continue
        rows.append({
            "id": external_id("sap_pc", code),
            "name": names.get(centre.get("ObjectID"), code),
            "code": code,
            "plan_id/id": ANALYTIC_PLAN_EXTERNAL_ID,
        })
    write_csv("account_analytic_account.csv", rows, ANALYTIC_ACCOUNT_FIELDNAMES)
    return {"account_analytic_account.csv": len(rows)}


TRANSFER_HEADER_FIELDNAMES = ["id", "name", "partner_id/id", "scheduled_date", "state", "picking_type_id"]
TRANSFER_LINE_FIELDNAMES = ["id", "picking_id/id", "product_id/id", "name", "product_uom_qty", "product_uom/id"]


def build_stock_transfers():
    """
    Sheet object #32 (Stock Transfers) -> stock.picking, the INBOUND side.

    Outbound deliveries were already covered (#64). khinbounddelivery adds what arrives:
    49 inbound deliveries with 119 items carrying ProductID, and quantities in a separate
    ItemQuantityCollection keyed on the item's ObjectID.

    picking_type_id is written as Odoo's built-in incoming-picking XML ID rather than an
    external ID of ours, because the receipt operation type ships with Odoo.

    The external ID is keyed on ObjectID, not the human-readable ID field - confirmed by
    diagnostics/validate_relations.py that SAP reuses the same ID across two different
    ObjectIDs for a handful of deliveries (e.g. "8020005269" appears on both an "Inconsistent/
    Not Released" draft and a later "Consistent/Released" version - looks like a
    correction/reprocessing pattern for Customer Returns). Keying on ID silently collapsed each
    such pair into one row, dropping the other. ID is kept as the display "name" only.
    """
    headers = load_raw("InboundDeliveryCollection", service_hint="khinbounddelivery")["rows"]
    items = load_raw("ItemCollection", service_hint="khinbounddelivery")["rows"]
    quantities = _group_by_parent(
        load_raw("ItemQuantityCollection", service_hint="khinbounddelivery")["rows"])
    senders = _group_by_parent(
        load_raw("SenderPartyCollection", service_hint="khinbounddelivery")["rows"])

    header_rows, known = [], set()
    for delivery in headers:
        object_id = delivery.get("ObjectID")
        delivery_id = delivery.get("ID")
        if not (object_id and delivery_id):
            continue
        known.add(object_id)
        party = resolve_party(senders, object_id, prefer="supplier")
        header_rows.append({
            "id": external_id("sap_inbdel", object_id),
            "name": delivery_id,
            "partner_id/id": external_id("sap_bp", party) if party else "",
            "scheduled_date": parse_sap_date(delivery.get("CreationDateTime")) or "",
            # DeliveryNoteStatusCodeText "Received" is the only terminal state on this tenant.
            "state": "done" if delivery.get("DeliveryNoteStatusCodeText") == "Received" else "assigned",
            "picking_type_id": "stock.picking_type_in",
        })

    line_rows = []
    for item in items:
        parent = item.get("ParentObjectID")
        if parent not in known:
            continue
        quantity_rows = quantities.get(item.get("ObjectID"), [])
        quantity = next((q.get("Quantity") for q in quantity_rows if q.get("Quantity")), "")
        unit = next((q.get("UnitCode") for q in quantity_rows if q.get("UnitCode")), "")
        product_id = item.get("ProductID")
        line_rows.append({
            "id": external_id("sap_inbdel_item", item.get("ObjectID")),
            "picking_id/id": external_id("sap_inbdel", parent),
            "product_id/id": external_id("sap_prod", product_id) if product_id else "",
            "name": item.get("TypeCodeText") or item.get("ID", ""),
            "product_uom_qty": quantity,
            "product_uom/id": external_id("sap_uom", unit) if unit else "",
        })

    write_csv("stock_picking_transfer.csv", header_rows, TRANSFER_HEADER_FIELDNAMES)
    write_csv("stock_picking_transfer_line.csv", line_rows, TRANSFER_LINE_FIELDNAMES)
    return {
        "stock_picking_transfer.csv": len(header_rows),
        "stock_picking_transfer_line.csv": len(line_rows),
    }


STOCK_MOVE_FIELDNAMES = [
    "id", "name", "reference", "product_id/id", "product_uom_qty", "product_uom/id",
    "date", "state", "location_id/id", "location_dest_id/id", "lot_id/id",
]

# Odoo's own built-in virtual locations (present in every install, not something this project
# invents) - used as the "outside the warehouse" end of a movement whose other leg isn't a real
# logistics area on this tenant (a pure receipt with no matching issue leg, or vice versa).
_VIRTUAL_LOCATION_SUPPLIERS = "stock.stock_location_suppliers"
_VIRTUAL_LOCATION_CUSTOMERS = "stock.stock_location_customers"
_VIRTUAL_LOCATION_INVENTORY = "stock.location_inventory"


def _logistics_area_lookup():
    """
    {stripped-uppercased ObjectID -> (SiteID, ID)} for every khlocation LogisticsArea - lets a
    khgoodsandactivityconfirmation row's dashed LogisticsAreaUUID be resolved to the exact same
    external ID build_locations() already gave that same area in stock_location.csv (confirmed
    by matching ObjectID between the two independently-imported services).
    """
    areas = load_raw("LogisticsAreaCollection", service_hint="khlocation")["rows"]
    return {a["ObjectID"]: (a.get("SiteID"), a.get("ID")) for a in areas if a.get("ObjectID")}


def _resolve_area(area_uuid, area_lookup):
    if not area_uuid:
        return ""
    site_area = area_lookup.get(area_uuid.replace("-", "").upper())
    if not site_area or not site_area[0] or not site_area[1]:
        return ""
    return logistics_area_key(*site_area)


def build_stock_moves():
    """
    Sheet object #33 (Stock Moves History) -> stock.move.

    Replaces the earlier khgoodsandserviceacknowledgement-based version (137 receipts, 238
    lines - a small subset of one document type) with khgoodsandactivityconfirmation's real
    inventory-movement ledger: 147,979 confirmations, 366,585 movement lines. The earlier
    docstring claimed InventoryChangeItemCollection/the header both 500 on this tenant - that
    was true when first checked but is no longer the case (confirmed: both extract cleanly at
    full volume now), and the service was already sitting fully extracted in output_raw/,
    just never wired into a transform.

    Each InventoryChangeItemCollection row is one movement of one material through one
    logistics area, tagged "Inventory receipt" or "Inventory issue" - NOT a two-sided transfer
    by itself. Where a confirmation moves the same material with one receipt leg and one issue
    leg (a real internal transfer, e.g. reason "Transfer"), the two legs are merged into a
    single stock.move with both a real source and a real destination location, instead of two
    separate half-transfers - confirmed clean: grouping by (confirmation, material) finds
    105,903 such pairs, all with identical quantity on both legs, zero mismatches. Every
    other row (a receipt with no matching issue, e.g. against a PO; an issue with no matching
    receipt, e.g. "Issue for Customer" or scrapping) keeps its one known real location and uses
    Odoo's own built-in virtual location for the unknown side - not a guess about where the
    goods started/ended, since SAP genuinely didn't attach a second logistics area to that row.

    lot_id/id added 2026-09-28: the user asked why stock_lot.csv's data wasn't showing up here.
    Real gap, not a data gap - InventoryChangeItemCollection carries IdentifiedStockUUID on
    185,158/366,585 raw lines (50.5%), and every one matches a real khbatch record, but this
    transform never read the field. Now resolved to stock_lot.csv's own sap_lot_<ObjectID>
    external ID. The other 49.5% of lines have no IdentifiedStockUUID on this tenant at all -
    not every movement is of identified (batch/lot-tracked) stock, so a blank lot_id/id there is
    real, not a join failure.
    """
    confirmations = {h["ObjectID"]: h for h in load_raw(
        "GoodsAndActivityConfirmationCollection",
        service_hint="khgoodsandactivityconfirmation")["rows"] if h.get("ObjectID")}
    items = load_raw("InventoryChangeItemCollection", service_hint="khgoodsandactivityconfirmation")["rows"]
    quantities = {q["ParentObjectID"]: q for q in load_raw(
        "ItemChangeQuantityCollection", service_hint="khgoodsandactivityconfirmation")["rows"]}
    materials = load_raw("MaterialCollection", service_hint="vmumaterial")["rows"]
    internal_id_by_uuid = {m["UUID"]: m.get("InternalID") for m in materials if m.get("UUID")}
    area_lookup = _logistics_area_lookup()

    def product_ref(material_uuid):
        internal_id = internal_id_by_uuid.get(material_uuid)
        return external_id("sap_prod", internal_id) if internal_id else ""

    def lot_ref(identified_stock_uuid):
        if not identified_stock_uuid:
            return ""
        return external_id("sap_lot", identified_stock_uuid.replace("-", "").upper())

    def move_row(object_id, name, date_, state, product_id_ref, qty_row, location_id, location_dest_id, lot_id_ref):
        qty = qty_row.get("Quantity") if qty_row else ""
        unit = qty_row.get("QuantityUnitCode") if qty_row else ""
        return {
            "id": external_id("sap_move", object_id),
            "name": name,
            "reference": name,
            "product_id/id": product_id_ref,
            "product_uom_qty": qty,
            "product_uom/id": external_id("sap_uom", unit) if unit else "",
            "date": date_,
            "state": state,
            "location_id/id": location_id,
            "location_dest_id/id": location_dest_id,
            "lot_id/id": lot_id_ref,
        }

    groups = {}
    for item in items:
        key = (item.get("ParentObjectID"), item.get("MaterialUUID"))
        groups.setdefault(key, []).append(item)

    rows = []
    for (confirmation_id, material_uuid), group in groups.items():
        confirmation = confirmations.get(confirmation_id)
        reason = group[0].get("InventoryChangeReasonCodeText", "")
        date_ = parse_sap_date(confirmation.get("TransactionDateTime")) if confirmation else ""
        state = "done" if not confirmation or confirmation.get("CancellationStatusCodeText") == "Not Canceled" else "cancel"
        product_id_ref = product_ref(material_uuid)

        issue_legs = [g for g in group if g.get("InventoryMovementDirectionCodeText") == "Inventory issue"]
        receipt_legs = [g for g in group if g.get("InventoryMovementDirectionCodeText") == "Inventory receipt"]

        if len(group) == 2 and len(issue_legs) == 1 and len(receipt_legs) == 1:
            issue, receipt = issue_legs[0], receipt_legs[0]
            source = _resolve_area(issue.get("LogisticsAreaUUID"), area_lookup) or _VIRTUAL_LOCATION_INVENTORY
            dest = _resolve_area(receipt.get("LogisticsAreaUUID"), area_lookup) or _VIRTUAL_LOCATION_INVENTORY
            # The two legs of a real transfer can carry different IdentifiedStockUUIDs (a
            # re-batching move) - confirmed on 9,748/116,090 pairs (8.4%). The issue leg's lot
            # is used since it names what physically left that location.
            rows.append(move_row(issue["ObjectID"], reason, date_, state, product_id_ref,
                                  quantities.get(issue["ObjectID"]), source, dest,
                                  lot_ref(issue.get("IdentifiedStockUUID"))))
            continue

        for leg in group:
            direction = leg.get("InventoryMovementDirectionCodeText")
            area = _resolve_area(leg.get("LogisticsAreaUUID"), area_lookup)
            if direction == "Inventory receipt":
                source = _VIRTUAL_LOCATION_SUPPLIERS
                dest = area or _VIRTUAL_LOCATION_INVENTORY
            else:
                source = area or _VIRTUAL_LOCATION_INVENTORY
                dest = _VIRTUAL_LOCATION_CUSTOMERS
            rows.append(move_row(leg["ObjectID"], reason, date_, state, product_id_ref,
                                  quantities.get(leg["ObjectID"]), source, dest,
                                  lot_ref(leg.get("IdentifiedStockUUID"))))

    write_csv("stock_move_history.csv", rows, STOCK_MOVE_FIELDNAMES)
    return {"stock_move_history.csv": len(rows)}


LOT_FIELDNAMES = ["id", "name", "product_id/id", "production_date", "expiration_date",
                   "supplier_id/id", "lot_status", "valuation_level_type", "stock_type"]

# IdentifiedStockLifeCycleStatusCodeCollection / IdentifiedStockProductValuationLevelTypeCodeCollection /
# IdentifiedStockIdentifiedStockTypeCodeCollection, read live via curl 2026-09-28 (khbatch's own
# codelist entity sets - not guessed, not the vmumaterial codelists which use different codes).
LOT_STATUS_TEXT = {"1": "In Preparation", "2": "Active", "3": "Blocked", "4": "Obsolete"}
LOT_VALUATION_LEVEL_TEXT = {"1": "Business Residence", "3": "Consignee"}
LOT_STOCK_TYPE_TEXT = {"01": "Batch", "02": "Lot", "03": "Optional Specified Stock",
                        "04": "Mandatory Specified Stock"}


def build_stock_lots():
    """
    Sheet object #30 (Lot/Serial Numbers) -> stock.lot.

    Previously deliberately not written: the only source then known (khproductionorder/
    ProductionLotCollection) exposes just ObjectID + ID, no product reference at all, so
    nothing built from it could pass Odoo's mandatory product_id on stock.lot.

    Closed 2026-09-25 via khgoodsandactivityconfirmation's embedded IdentifiedStock node
    (joined through InventoryChangeItemCollection for a product), then rebuilt 2026-09-28 after
    the user asked where production/expiration dates were: that embedded node only exposes 4
    fields (no dates at all - confirmed via live $metadata), because it's a thin projection of
    IdentifiedStock's real, standalone Business Object, which DOES carry ExpirationDateTime and
    ProductionDateTime - never exposed as OData until built directly via the OData Editor
    (service khbatch, Work Center View MMA_PHYSICALINVENTORY - same path as khbomvariant/
    khequipmentresource). Confirmed live: 52,101 real batch records, 51,864 (99.5%) with a real
    production date, 1,743 (3.3%) with a real expiration date - most materials on this tenant
    simply aren't expiry-tracked, so a blank expiration_date is real data, not a gap.

    MaterialUUID lives directly on this entity now, so the InventoryChangeItemCollection join
    that build_stock_moves() still needs (no MaterialUUID there) is no longer needed here.

    Extended again 2026-09-28: the user pasted a real screenshot of the live "Identified Stock"
    edit screen (Supplier ID, Status, Valuation Level Type all populated, none of them in the
    CSV) - khbatch had only ever selected 5 of its real Root fields when it was first built.
    Re-opened the service in the OData Editor and added IdentifiedStockPartyID, SupplierUUID,
    LifeCycleStatusCode and ProductValuationLevelTypeCode. Confirmed live via curl: only 1,317 of
    52,101 rows (2.5%) carry a real SupplierUUID and 3 carry an IdentifiedStockPartyID - most
    identified stock on this tenant genuinely has no supplier attached, not a join failure.
    LifeCycleStatusCode/ProductValuationLevelTypeCode/IdentifiedStockTypeCode are decoded via
    khbatch's own codelist entity sets (IdentifiedStock*CodeCollection), read live - not the
    vmumaterial codelists, which use different code values for similarly-named fields.
    """
    stocks = load_raw("IdentifiedStockCollection", service_hint="khbatch")["rows"]
    materials = load_raw("MaterialCollection", service_hint="vmumaterial")["rows"]
    internal_id_by_uuid = {m["UUID"]: m.get("InternalID") for m in materials if m.get("UUID")}

    suppliers = load_raw("SupplierCollection", service_hint="khsupplier")["rows"]
    supplier_internal_id_by_uuid = {s["UUID"]: s.get("InternalID") for s in suppliers if s.get("UUID")}

    rows = []
    for stock in stocks:
        object_id = stock.get("ObjectID")
        internal_id = internal_id_by_uuid.get(stock.get("MaterialUUID"))
        if not object_id or not internal_id:
            continue
        supplier_internal_id = supplier_internal_id_by_uuid.get(stock.get("SupplierUUID"))
        rows.append({
            "id": external_id("sap_lot", object_id),
            "name": stock.get("ID", ""),
            "product_id/id": external_id("sap_prod", internal_id),
            "production_date": parse_sap_date(stock.get("ProductionDateTime")) or "",
            "expiration_date": parse_sap_date(stock.get("ExpirationDateTime")) or "",
            "supplier_id/id": external_id("sap_bp", supplier_internal_id) if supplier_internal_id else "",
            "lot_status": LOT_STATUS_TEXT.get(stock.get("LifeCycleStatusCode"), ""),
            "valuation_level_type": LOT_VALUATION_LEVEL_TEXT.get(stock.get("ProductValuationLevelTypeCode"), ""),
            "stock_type": LOT_STOCK_TYPE_TEXT.get(stock.get("IdentifiedStockTypeCode"), ""),
        })
    write_csv("stock_lot.csv", rows, LOT_FIELDNAMES)
    return {"stock_lot.csv": len(rows)}


PRICELIST_ITEM_FIELDNAMES = [
    "id", "pricelist_id/id", "applied_on", "product_tmpl_id/id", "compute_price", "fixed_price",
]
LIST_PRICE_PRICELIST_ID = "sap_pricelist_list_prices"


def _resolve_order_to_arrangement():
    """
    khsalesorder has no direct field pointing at its khsalesarrangement (SAP does not expose
    that FK over OData on this tenant), so each order is matched to its real sales arrangement
    by the same business key ByDesign itself uses to determine one: Customer + Sales
    Organisation + Distribution Channel.

    khsalesorder's own BuyerPartyCollection is NOT one row per order - it carries every party
    role on the order (buyer, bill-to, payer, ship-to, sales unit, company, ...) all sharing the
    same ParentObjectID, so naively building a dict from it silently keeps whichever role happens
    to be iterated last (on this tenant that was role 8, the internal Sales Organisation ID, not
    the customer) - a real bug caught by checking the actual joined data rather than trusting a
    1:1 assumption the OData shape doesn't guarantee. Fixed by keeping, for each order, the
    first PartyID that is actually a known customer/supplier InternalID.

    Returns {order_object_id: arrangement_object_id}, covering ~99.8% of orders on this tenant
    (4300/4308) - the rest have no resolvable customer party and are left out, not guessed.
    """
    customers = load_raw("CustomerCollection", service_hint="khcustomer")["rows"]
    suppliers = load_raw("SupplierCollection", service_hint="khsupplier")["rows"]
    internal_to_uuid = {
        c["InternalID"]: c["UUID"].replace("-", "").upper()
        for c in customers + suppliers if c.get("InternalID") and c.get("UUID")
    }

    from collections import defaultdict
    party_roles_by_order = defaultdict(list)
    for b in load_raw("BuyerPartyCollection", service_hint="khsalesorder")["rows"]:
        party_roles_by_order[b["ParentObjectID"]].append(b.get("PartyID"))

    order_to_customer_uuid = {}
    for order_id, party_ids in party_roles_by_order.items():
        for pid in party_ids:
            if pid in internal_to_uuid:
                order_to_customer_uuid[order_id] = internal_to_uuid[pid]
                break

    arrangements = load_raw("SalesArrangementCollection", service_hint="khsalesarrangement")["rows"]
    arr_by_key = {}
    for a in arrangements:
        cust_uuid = (a.get("CustomerUUID") or "").replace("-", "").upper()
        key = (cust_uuid, a.get("SalesOrganisationID", ""), a.get("DistributionChannelCode", ""))
        arr_by_key.setdefault(key, a["ObjectID"])  # first arrangement wins on a rare duplicate key

    order_to_arrangement = {}
    for order in load_raw("SalesOrderCollection", service_hint="khsalesorder")["rows"]:
        order_id = order.get("ObjectID")
        cust_uuid = order_to_customer_uuid.get(order_id, "")
        key = (cust_uuid, order.get("SalesOrganisationID", ""), order.get("DistributionChannelCode", ""))
        if key in arr_by_key:
            order_to_arrangement[order_id] = arr_by_key[key]
    return order_to_arrangement


def build_pricelist_items():
    """
    Sheet object #60 (Discount Rules) -> product.pricelist.item.

    Stated plainly: **this tenant has no discount rules.** Of the price components SAP
    categorises as "Discount", every single non-zero one is a Rounding Difference; Item
    Discounts and Header Discounts are 0.00 throughout. Nothing was found to build discount
    rules from, and none was invented.

    What the same price-component data does carry is real LIST PRICES - product.pricelist.item
    is exactly Odoo's model for "this product is priced at X". Each priced line is attached to
    the REAL sales arrangement (pricelist) its order actually used, resolved by
    _resolve_order_to_arrangement() - not dumped under one synthetic catch-all pricelist. Orders
    that don't resolve to a specific arrangement (see that function's docstring - ~0.2% of
    orders) fall back to a small dedicated "SAP List Prices - Unmatched" pricelist so that price
    data is never silently dropped, just clearly separated from real per-customer pricing.

    Where a product appears at several prices under the same pricelist, the most frequently
    quoted price wins; the full per-order history stays in output_full_csv/.
    """
    from collections import Counter

    order_to_arrangement = _resolve_order_to_arrangement()

    items = {r["ObjectID"]: r["ParentObjectID"] for r in
             load_raw("ItemCollection", service_hint="khsalesorder")["rows"] if r.get("ObjectID")}
    product_by_item = {r.get("ParentObjectID"): r.get("ProductID") for r in
                       load_raw("ItemProductCollection", service_hint="khsalesorder")["rows"]}

    prices_by_pricelist_product = {}
    for component in load_raw("ItemPriceComponentCollection", service_hint="khsalesorder")["rows"]:
        if component.get("TypeCodeText") != "List Price":
            continue
        line_id = component.get("ParentObjectID")
        order_id = items.get(line_id)
        if not order_id:
            continue
        product_id = product_by_item.get(line_id)
        value = component.get("DecimalValue")
        if not (product_id and value):
            continue
        try:
            price = float(value)
        except ValueError:
            continue
        if price <= 0:
            continue
        pricelist_id = external_id("sap_pricelist", order_to_arrangement[order_id]) \
            if order_id in order_to_arrangement else LIST_PRICE_PRICELIST_ID
        prices_by_pricelist_product.setdefault((pricelist_id, product_id), []).append(round(price, 2))

    rows = []
    for (pricelist_id, product_id), prices in sorted(prices_by_pricelist_product.items()):
        rows.append({
            "id": external_id("sap_plitem", f"{pricelist_id}_{product_id}"),
            "pricelist_id/id": pricelist_id,
            "applied_on": "1_product",
            "product_tmpl_id/id": external_id("sap_prod", product_id),
            "compute_price": "fixed",
            "fixed_price": f"{Counter(prices).most_common(1)[0][0]:.2f}",
        })
    write_csv("product_pricelist_item_discount.csv", rows, PRICELIST_ITEM_FIELDNAMES)
    return {"product_pricelist_item_discount.csv": len(rows)}


PRICELIST_COMBINED_FIELDNAMES = [
    "id", "name", "currency_id/id", "partner_id/id",
    "item_ids/applied_on", "item_ids/product_tmpl_id/id", "item_ids/compute_price",
    "item_ids/fixed_price",
]


def build_pricelist_combined():
    """
    Deliverable convenience file: product_pricelist.csv (headers, built by build_pricelists())
    and product_pricelist_item_discount.csv (lines, built by build_pricelist_items()) merged into
    ONE file using Odoo's standard one2many CSV import convention - a pricelist header row
    carries its first line item's item_ids/* columns, and each further line for the same
    pricelist is a follow-up row with id/name/currency_id left blank so Odoo groups it under the
    same parent. Must run after both build_pricelists() and build_pricelist_items() in TRANSFORMS.

    Only "SAP List Prices" (sap_pricelist_list_prices) has real line items - the 226 sales
    arrangement headers from khsalesarrangement have no product/price data on this tenant (see
    build_pricelists()'s docstring) and are written here as header-only rows, exactly as they
    already are in product_pricelist.csv.
    """
    headers = list(csv.DictReader(open(os.path.join(ODOO_DIR, "product_pricelist.csv"), encoding="utf-8")))
    items = list(csv.DictReader(open(os.path.join(ODOO_DIR, "product_pricelist_item_discount.csv"), encoding="utf-8")))

    items_by_pricelist = {}
    for item in items:
        items_by_pricelist.setdefault(item["pricelist_id/id"], []).append(item)

    rows = []
    for header in headers:
        lines = items_by_pricelist.get(header["id"], [])
        if not lines:
            rows.append({
                "id": header["id"],
                "name": header["name"],
                "currency_id/id": header["currency_id/id"],
                "partner_id/id": header["partner_id/id"],
                "item_ids/applied_on": "",
                "item_ids/product_tmpl_id/id": "",
                "item_ids/compute_price": "",
                "item_ids/fixed_price": "",
            })
            continue
        for i, line in enumerate(lines):
            rows.append({
                "id": header["id"] if i == 0 else "",
                "name": header["name"] if i == 0 else "",
                "currency_id/id": header["currency_id/id"] if i == 0 else "",
                "partner_id/id": header["partner_id/id"] if i == 0 else "",
                "item_ids/applied_on": line["applied_on"],
                "item_ids/product_tmpl_id/id": line["product_tmpl_id/id"],
                "item_ids/compute_price": line["compute_price"],
                "item_ids/fixed_price": line["fixed_price"],
            })
    write_csv("product_pricelist_with_items.csv", rows, PRICELIST_COMBINED_FIELDNAMES)
    return {"product_pricelist_with_items.csv": len(rows)}


def build_production_history():
    """
    Sheet object #45 (Production History) -> mrp.production, the CLOSED orders.

    #44 writes every production order; #45 is the historical subset. LifeCycleStatusCodeText is
    real and populated: Started 86, Released 41, Finished 17, In Preparation 7, Canceled 1.
    "Finished" and "Canceled" are the terminal states, so those 18 are the history.
    """
    orders = load_raw("ProductionOrderCollection", service_hint="khproductionorder")["rows"]
    closed = {"Finished": "done", "Canceled": "cancel"}

    rows = []
    for order in orders:
        state = closed.get(order.get("LifeCycleStatusCodeText"))
        if not state:
            continue
        product_id = order.get("MainProductOutput.ProductID")
        rows.append({
            "id": external_id("sap_mo_hist", order.get("ID") or order.get("ObjectID")),
            "name": order.get("ID") or order.get("ObjectID", ""),
            "product_id/id": external_id("sap_prod", product_id) if product_id else "",
            "product_qty": order.get("MainProductOutput.PlannedQuantity", ""),
            "date_planned_start": parse_sap_date(order.get("RequestedStartDateTime")) or "",
            "date_planned_finished": parse_sap_date(order.get("RequestedEndDateTime")) or "",
            "state": state,
        })
    write_csv("mrp_production_history.csv", rows, PRODUCTION_ORDER_FIELDNAMES)
    return {"mrp_production_history.csv": len(rows)}


def build_rfqs():
    """
    Sheet object #49 (RFQs) -> purchase.order in draft/sent state.

    ByDesign has no separate RFQ object; an RFQ is a purchase order that has not been ordered
    yet. LifeCycleStatusCodeText gives the split on real data: In Preparation 89 and In Approval
    86 are the pre-order states, against Sent 125 / Follow-Up Document Created 266 / Finished 77
    which are live orders and already covered by #50. build_purchase_orders() excludes these same
    _RFQ_STAGE_STATES orders from purchase_order.csv so neither file duplicates the other.
    """
    orders = load_raw("PurchaseOrderCollection", service_hint="khpurchaseorder")["rows"]
    suppliers = _group_by_parent(
        load_raw("SupplierCollection", service_hint="khpurchaseorder")["rows"])
    draft_states = {"In Preparation": "draft", "In Approval": "sent"}

    rows = []
    for order in orders:
        state = draft_states.get(order.get("LifeCycleStatusCodeText"))
        if not state:
            continue
        party = resolve_party(suppliers, order.get("ObjectID"), prefer="supplier")
        currency = order.get("CurrencyCode") or ""
        rows.append({
            "id": external_id("sap_rfq", order.get("ID") or order.get("ObjectID")),
            "name": order.get("ID") or order.get("ObjectID", ""),
            "partner_id/id": external_id("sap_bp", party) if party else "",
            "date_order": parse_sap_date(order.get("CreationDateTime")) or "",
            "state": state,
            "currency_id/id": f"base.{currency}" if currency else "",
            "amount_total": order.get("TotalGrossAmount", ""),
        })
    write_csv("purchase_order_rfq.csv", rows, PO_HEADER_FIELDNAMES)
    return {"purchase_order_rfq.csv": len(rows)}


BOM_FIELDNAMES = ["id", "product_tmpl_id/id", "product_qty", "code", "type"]
BOM_LINE_FIELDNAMES = ["id", "bom_id/id", "product_id/id", "product_qty"]


def build_boms():
    """
    Sheet object #40 (BOMs, mandatory) -> mrp.bom / mrp.bom.line.

    The one mandatory object this project spent the longest searching for - a full sweep of all
    1485 entity sets across every built-in service, plus all 609 entity sets across all 47
    original custom service .xml files, found zero matches. The data was real (confirmed live in
    the ByDesign UI under Enterprise Search -> "Bills of Material Variants"), it just had never
    been published as an OData service on this tenant. Closed by building a new custom service,
    khbomvariant, directly via the OData Editor (self-service, no .xml import needed) - see
    sap_odata_editor_walkthrough/bom_service_setup/README.md for the full story and screenshots.

    Structure (confirmed live): a BOM header (ProductionBillOfMaterialCollection) has one or more
    Variants, each producing a specific product+quantity (ProductionBillOfMaterialVariantCollection,
    MaterialUUID = the variant's own output product). Each BOM header separately owns a set of
    component lines - the real product/quantity data lives on ItemGroupItemChangeState, not on
    ItemGroupItem (which is a thin pointer node with no fields of its own and a broken
    $expand back to its parent ItemGroup - confirmed via direct curl, "ParentObjectID property is
    missing in entity ProductionBillOfMaterialItemGroup"). ItemGroupItemChangeState's own nav
    property straight back to the BOM header works correctly in bulk via $expand and is what
    extract_raw.py uses (SOURCES entry has expand="ProductionBillOfMaterial"), giving each
    component row a ProductionBillOfMaterial.ObjectID column to join on directly - no intermediate
    hop needed.

    Known limitation: components are attached to the BOM header, not to a specific variant, so
    where a header has more than one variant (48 of this tenant's 400 headers - 448 variants
    total), every variant of that header gets the same component list here. This matches what the
    raw data actually supports; nothing is guessed to split components per variant.

    product_tmpl_id/id and each line's product_id/id resolve via the same MaterialUUID ->
    InternalID lookup already used for product_template.csv, so an unresolvable reference (no
    matching material on this tenant) is left blank rather than guessed, and doesn't break the
    rest of the row - matching this project's product_id/id handling everywhere else (e.g.
    purchase_order_line.csv).
    """
    headers = {r["ObjectID"]: r for r in load_raw("ProductionBillOfMaterialCollection")["rows"]
               if r.get("ObjectID")}
    variants = load_raw("ProductionBillOfMaterialVariantCollection")["rows"]
    components = load_raw("ProductionBillOfMaterialItemGroupItemChangeStateCollection")["rows"]

    materials = load_raw("MaterialCollection", service_hint="vmumaterial")["rows"]
    internal_id_by_uuid = {m["UUID"]: m.get("InternalID") for m in materials if m.get("UUID")}

    def product_ref(material_uuid):
        internal_id = internal_id_by_uuid.get(material_uuid)
        return external_id("sap_prod", internal_id) if internal_id else ""

    components_by_bom = {}
    for c in components:
        if c.get("DeletedIndicator"):
            continue
        bom_object_id = c.get("ProductionBillOfMaterial.ObjectID")
        if not bom_object_id:
            continue
        components_by_bom.setdefault(bom_object_id, []).append(c)

    bom_rows = []
    line_rows = []
    for variant in variants:
        if variant.get("ObsoleteIndicator"):
            continue
        variant_object_id = variant.get("ObjectID")
        bom_object_id = variant.get("ParentObjectID")
        header = headers.get(bom_object_id)
        if not variant_object_id or not header:
            continue
        bom_id = external_id("sap_bom", variant_object_id)
        bom_rows.append({
            "id": bom_id,
            "product_tmpl_id/id": product_ref(variant.get("MaterialUUID")),
            "product_qty": variant.get("Quantity") or "1",
            "code": header.get("ID", ""),
            "type": "normal",
        })
        for component in components_by_bom.get(bom_object_id, []):
            line_rows.append({
                "id": external_id("sap_bomline", f"{variant_object_id}_{component.get('ObjectID')}"),
                "bom_id/id": bom_id,
                "product_id/id": product_ref(component.get("MaterialUUID")),
                "product_qty": component.get("Quantity") or "1",
            })

    write_csv("mrp_bom.csv", bom_rows, BOM_FIELDNAMES)
    write_csv("mrp_bom_line.csv", line_rows, BOM_LINE_FIELDNAMES)
    return {"mrp_bom.csv": len(bom_rows), "mrp_bom_line.csv": len(line_rows)}


BOM_COMBINED_FIELDNAMES = [
    "id", "product_tmpl_id/id", "product_qty", "code", "type",
    "bom_line_ids/product_id/id", "bom_line_ids/product_qty",
]


def build_bom_combined():
    """
    Deliverable convenience file: mrp_bom.csv (headers, build_boms()) and mrp_bom_line.csv
    (lines, same function) merged into ONE file using Odoo's standard one2many CSV import
    convention - a bom header row carries its own id/product_tmpl_id/product_qty/code/type plus
    its first line's bom_line_ids/* columns; every further line for that same bom is a follow-up
    row with the header columns left blank, so Odoo groups it under the bom directly above it.
    Must run after build_boms() in TRANSFORMS. Same pattern as build_pricelist_combined().
    """
    boms = list(csv.DictReader(open(os.path.join(ODOO_DIR, "mrp_bom.csv"), encoding="utf-8")))
    lines = list(csv.DictReader(open(os.path.join(ODOO_DIR, "mrp_bom_line.csv"), encoding="utf-8")))

    lines_by_bom = {}
    for line in lines:
        lines_by_bom.setdefault(line["bom_id/id"], []).append(line)

    rows = []
    for bom in boms:
        bom_lines = lines_by_bom.get(bom["id"], [])
        if not bom_lines:
            rows.append({
                "id": bom["id"],
                "product_tmpl_id/id": bom["product_tmpl_id/id"],
                "product_qty": bom["product_qty"],
                "code": bom["code"],
                "type": bom["type"],
                "bom_line_ids/product_id/id": "",
                "bom_line_ids/product_qty": "",
            })
            continue
        for i, line in enumerate(bom_lines):
            rows.append({
                "id": bom["id"] if i == 0 else "",
                "product_tmpl_id/id": bom["product_tmpl_id/id"] if i == 0 else "",
                "product_qty": bom["product_qty"] if i == 0 else "",
                "code": bom["code"] if i == 0 else "",
                "type": bom["type"] if i == 0 else "",
                "bom_line_ids/product_id/id": line["product_id/id"],
                "bom_line_ids/product_qty": line["product_qty"],
            })
    write_csv("mrp_bom_with_lines.csv", rows, BOM_COMBINED_FIELDNAMES)
    return {"mrp_bom_with_lines.csv": len(rows)}


EQUIPMENT_FIELDNAMES = ["id", "name", "serial_no"]


def build_equipment():
    """
    Sheet object #34 (Equipment, mandatory) -> maintenance.equipment.

    Real standard ByDesign master data ("Supply Chain Design Master Data" -> "Resources" in the
    live UI, Business Object EquipmentResource) - never published as OData on this tenant until
    built directly via the OData Editor (self-service, no .xml import), the same path used for
    khbomvariant. See sap_odata_editor_walkthrough/ for the build story.

    Only `name` (the resource's own Description) and `serial_no` (its business ID, e.g. "10100")
    are mapped - the entity has no fields Odoo's maintenance.equipment has a home for beyond
    these (category, technician, location, etc. would all be guesses with no source data).
    """
    rows = load_raw("EquipmentResourceCollection")["rows"]
    out_rows = []
    for r in rows:
        object_id = r.get("ObjectID")
        if not object_id:
            continue
        out_rows.append({
            "id": external_id("sap_equip", object_id),
            "name": r.get("Description") or r.get("ID") or object_id,
            "serial_no": r.get("ID", ""),
        })
    write_csv("maintenance_equipment.csv", out_rows, EQUIPMENT_FIELDNAMES)
    return {"maintenance_equipment.csv": len(out_rows)}


JOURNAL_FIELDNAMES = [
    "id", "ref", "date", "line_ids/account_id/id", "line_ids/debit", "line_ids/credit",
]


def build_journal_entries():
    """
    Sheet object #13 (Journal Entries) -> account.move (+ line_ids).

    Previously "pending_mapping, data source confirmed" but never actually pulled - the report
    (fin_generalledger_analytics.svc/RPFINGLAU03_Q0001QueryResults, "Journal Entries") 400'd on
    every request because of a real bug in get_entity_set_all_fields() (see src/sap_client.py:
    BYD_P_* parameter fields weren't excluded from $select), now fixed. Small on this tenant -
    157 real G/L line items across a handful of journal documents, genuinely balanced (net
    debit-credit is ~0 across the whole file, confirmed). Row count moved from 137 to 157 after
    TGLACCT (the account name text) was added to the $select to also close a Chart of Accounts
    gap - a different field combination groups the same underlying OLAP data differently, the
    documented quirk of these report entities, not new or lost data.

    KCBALANCE_CURRCOMP is a signed company-currency amount - positive for a debit line
    (CDEBITCREDIT=1), negative for a credit line (CDEBITCREDIT=2) - so debit/credit for Odoo's
    account.move.line are derived directly from its sign, not guessed.
    """
    rows = load_raw("RPFINGLAU03_Q0001QueryResults", service_hint="fin_generalledger_analytics.svc")["rows"]

    lines_by_doc = {}
    for r in rows:
        doc_id = r.get("CACC_DOC_UUID")
        if not doc_id:
            continue
        lines_by_doc.setdefault(doc_id, []).append(r)

    out_rows = []
    for doc_id, lines in lines_by_doc.items():
        for i, line in enumerate(lines):
            amount = float(line.get("KCBALANCE_CURRCOMP") or 0)
            account = line.get("CGLACCT")
            out_rows.append({
                "id": external_id("sap_journal", doc_id) if i == 0 else "",
                "ref": doc_id if i == 0 else "",
                "date": parse_sap_date(line.get("CDOC_DATE")) if i == 0 else "",
                "line_ids/account_id/id": external_id("sap_account", account) if account else "",
                "line_ids/debit": amount if amount > 0 else 0,
                "line_ids/credit": -amount if amount < 0 else 0,
            })
    write_csv("account_move_journal.csv", out_rows, JOURNAL_FIELDNAMES)
    return {"account_move_journal.csv": len(out_rows)}


ASSET_FIELDNAMES = [
    "id", "name", "asset_class", "status", "acquisition_cost", "accumulated_depreciation",
    "net_book_value",
]
ASSET_DEPRECIATION_FIELDNAMES = ["id", "asset_id/id", "amount"]


def build_fixed_assets():
    """
    Sheet objects #14 (Fixed Assets) -> account.asset, #15 (Asset Depreciation) ->
    account.asset.depreciation.line.

    Same BYD_P_* bug that blocked Journal Entries blocked these too - both reports are on
    fin_fixedassets_analytics.svc, confirmed live with real assets (FACTORY LAND, LAPTOP,
    FEEDER F1-12, etc.) once the fix landed. #14 is master data (3,279 real fixed assets); #15
    is each asset's CURRENT accumulated depreciation position from the same report family, not
    a history of individual depreciation postings - no period dimension was selected (the
    report has one, but this tenant's value is a point-in-time balance, and adding a period
    would multiply row count without adding a real posting date per line), so this is one row
    per asset with its current KCPOSTED_DEPR, not per-period history. Call it what it is rather
    than implying more granularity than the source gives.

    account_asset.csv used to only write id/name even though RPFINFXAU04 carries real asset
    class/status (CASSETCLASS/TASSETCLASS, CLC_STAT/TLC_STAT) and RPFINFXAU01 carries real
    cost/depreciation/net-book-value figures (KCACQUISITION_COSTS/KCACCUMULATED_DEPR/
    KCNETBOOKVALUE_END_OF) for every asset - plain decimals, no currency-suffix formatting to
    parse (unlike the F*/K* fields on other analytics reports). Wired in 2026-09-30.
    """
    assets = load_raw("RPFINFXAU04_Q0001QueryResults", service_hint="fin_fixedassets_analytics.svc")["rows"]
    values = load_raw("RPFINFXAU01_Q0001QueryResults", service_hint="fin_fixedassets_analytics.svc")["rows"]
    values_by_asset = {v.get("CFXA_UUID"): v for v in values if v.get("CFXA_UUID")}

    asset_rows = []
    for a in assets:
        asset_id = a.get("CFXA_UUID")
        if not asset_id:
            continue
        v = values_by_asset.get(asset_id, {})
        asset_rows.append({
            "id": external_id("sap_asset", asset_id),
            "name": a.get("TFXA_UUID") or asset_id,
            "asset_class": a.get("TASSETCLASS") or a.get("CASSETCLASS") or "",
            "status": a.get("TLC_STAT") or a.get("CLC_STAT") or "",
            "acquisition_cost": v.get("KCACQUISITION_COSTS", ""),
            "accumulated_depreciation": v.get("KCACCUMULATED_DEPR", ""),
            "net_book_value": v.get("KCNETBOOKVALUE_END_OF", ""),
        })
    write_csv("account_asset.csv", asset_rows, ASSET_FIELDNAMES)

    dep_rows = []
    for v in values:
        asset_id = v.get("CFXA_UUID")
        amount = v.get("KCPOSTED_DEPR")
        if not asset_id or not amount:
            continue
        dep_rows.append({
            "id": external_id("sap_assetdep", asset_id),
            "asset_id/id": external_id("sap_asset", asset_id),
            "amount": amount,
        })
    write_csv("account_asset_depreciation_line.csv", dep_rows, ASSET_DEPRECIATION_FIELDNAMES)
    return {
        "account_asset.csv": len(asset_rows),
        "account_asset_depreciation_line.csv": len(dep_rows),
    }


ROUTING_OPS_FIELDNAMES = ["id", "name", "workcenter_id/id"]


def build_routing_operations():
    """
    Sheet object #43 (Operations) -> mrp.routing.workcenter.

    Source is khproductionorder/OperationCollection, already raw-extracted (82,859 rows) but
    never transformed - #44's own docstring flagged this as a known gap. The raw data is one
    row per operation PER PRODUCTION ORDER, not a reusable routing template, so importing it
    as-is would create tens of thousands of duplicate "routing steps". Deduplicated instead by
    (operation ID, resource) among "Make" (category=1) rows - the real distinct operation types
    actually performed on each work center - which collapses cleanly to 22 combinations (SMT on
    work center 10100, AOI on 10200, wave soldering on 10500, etc.), matching the tenant's own
    process reality: this is genuinely one production line with a fixed sequence of stations.

    Cycle time is deliberately NOT included: ProcessingNetDuration varies per order (it's an
    actual observed duration, not a standard planned time), so there is no single correct
    "time_cycle" value to put in a template without fabricating one.

    Sheet object #41 (Routings, the routing HEADER) has no OData source anywhere on this tenant
    (searched SERVICE_CATALOG.csv and the Design Data Sources catalog - "Released Execution
    Production Model Operation" / SCM_REPM_OPER exists as a report definition but was never
    published as a live OData service, same situation BOMs/Equipment were in before those were
    exposed via the OData Editor) - still pending_mapping, would need the same self-service
    service-creation work those two got.
    """
    rows = load_raw("OperationCollection", service_hint="khproductionorder")["rows"]
    seen = {}
    for r in rows:
        if r.get("TypeCodeText") != "Make":
            continue
        op_id, resource_id = r.get("ID"), r.get("ResourceID")
        if not op_id or not resource_id:
            continue
        seen[(op_id, resource_id)] = r

    out_rows = []
    for (op_id, resource_id), r in seen.items():
        out_rows.append({
            "id": external_id("sap_routingop", f"{op_id}_{resource_id}"),
            "name": op_id,
            "workcenter_id/id": external_id("sap_wc", resource_id),
        })
    write_csv("mrp_routing_workcenter_ops.csv", out_rows, ROUTING_OPS_FIELDNAMES)
    return {"mrp_routing_workcenter_ops.csv": len(out_rows)}


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
    ("account_analytic_account (profit centres)", build_profit_centres),
    ("stock_picking_transfer (inbound deliveries)", build_stock_transfers),
    ("stock_move_history (inventory change ledger)", build_stock_moves),
    ("stock_lot (identified stock / batches)", build_stock_lots),
    ("product_pricelist_item (list prices)", build_pricelist_items),
    ("product_pricelist_with_items (combined deliverable)", build_pricelist_combined),
    ("mrp_production_history (closed orders)", build_production_history),
    ("purchase_order_rfq (draft purchase orders)", build_rfqs),
    ("stock_quant_adjustment (inventory balances)", build_inventory),
    ("mrp_bom / mrp_bom_line (bills of material)", build_boms),
    ("mrp_bom_with_lines (combined deliverable)", build_bom_combined),
    ("maintenance_equipment (equipment resources)", build_equipment),
    ("account_move_journal (journal entries)", build_journal_entries),
    ("account_asset / account_asset_depreciation_line (fixed assets)", build_fixed_assets),
    ("mrp_routing_workcenter_ops (operations)", build_routing_operations),
]


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only", metavar="TEXT",
        help="Only run transforms whose label contains TEXT (case-insensitive), e.g. "
             "--only res_partner or --only product_template. Reads whatever is already in "
             "output_raw/ - no SAP calls - so this re-runs one object's mapping in seconds.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    transforms = TRANSFORMS
    if args.only:
        needle = args.only.lower()
        transforms = [(label, fn) for label, fn in TRANSFORMS if needle in label.lower()]
        if not transforms:
            logger.warning("--only %r matched no transform label", args.only)

    summary = {}
    for label, fn in transforms:
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
