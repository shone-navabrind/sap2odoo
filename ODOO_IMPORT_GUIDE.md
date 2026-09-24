# Odoo Master Data Import Guide

Step-by-step import order for all 16 built master-data files, including every extra SAP
column available as a custom field - not just the standard Odoo fields. Generated from the
2026-09-24 full extraction (`output_odoo/` + `output_full_csv/odoo_models/`); regenerate the
field tables any time data changes by re-running the generator described at the bottom of this
file.

## Overview

**17 master-data objects are built** (Customers and Vendors share one file, `res_partner.csv`,
so that's 16 physical files). Of the 16 mandatory master objects, 14 are done - only Equipment
and BOMs are missing, and neither is fixable by importing more data (see `STATUS.md` for why).

**Total custom fields available across all master data: 688** (after excluding fields that don't
add anything - see "A note on ObjectID/UUID fields" below). Two-phase pattern for every object:

1. **Create the custom fields first** (table given per object below) - Odoo needs the field to
   exist before a column can be mapped onto it during import.
2. **Import the base file** (`output_odoo/<file>.csv`) - creates the actual records with
   standard Odoo fields.
3. **Import the full-column file** (`output_full_csv/odoo_models/<file>.csv`) - same records,
   matched by External ID, populating the custom fields created in step 1.

### A note on `ObjectID`/`ParentObjectID`/`UUID` fields

These appear throughout the tables below because they're real columns on the source SAP data,
and the instruction was "import all the custom fields, not a curated subset" - so they're
included for completeness. In practice they're SAP's internal linkage keys (already used behind
the scenes to build the External IDs and relations in `output_odoo/`) and have no direct
business meaning to a user - they're mainly useful for *traceability* ("which exact SAP record
did this come from") rather than reporting or day-to-day use. Skip them if you only want
business-meaningful custom fields; keep them if you want full audit lineage.

### Why some fields repeat with a `_2`, `_3` suffix

A few objects pull from more than one source entity that happens to declare a same-named field
(e.g. `account_payment_term.csv` combines `khcustomerinvoice`'s and `khsupplierinvoice`'s
`CashDiscountTermsCollection`, both of which have a `PaymentBaselineDate` field). The suggested
technical names are de-duplicated with a numeric suffix so both can exist as separate Odoo
fields - check the **Source entity** column to tell them apart.

## Import order (respects all cross-object dependencies)

| Order | File | Odoo Model | Depends on |
|---|---|---|---|
| 1 | `account_analytic_plan.csv` | account.analytic.plan | none |
| 2 | `uom_uom.csv` | uom.uom | none |
| 3 | `res_bank.csv` | res.bank | none |
| 4 | `product_category.csv` | product.category | none |
| 5 | `account_account.csv` | account.account | none |
| 6 | `account_tax.csv` | account.tax | none |
| 7 | `account_payment_term.csv` | account.payment.term | none |
| 8 | `account_analytic_account_cc.csv` | account.analytic.account | #1 (`plan_id/id`) |
| 9 | `account_analytic_account.csv` | account.analytic.account | #1 (`plan_id/id`) |
| 10 | `res_partner.csv` | res.partner | none (uses Odoo's built-in `base.<iso2>` country data) |
| 11 | `res_users.csv` | res.users | none |
| 12 | `stock_warehouse.csv` | stock.warehouse | none |
| 13 | `stock_location.csv` | stock.location | #12 (location hierarchy) |
| 14 | `mrp_workcenter.csv` | mrp.workcenter | none |
| 15 | `product_template.csv` | product.template | #4 (`categ_id/id`), #2 (`uom_id/id`, `uom_po_id/id`) |
| 16 | `product_supplierinfo.csv` | product.supplierinfo | #10 (`partner_id/id`), #15 (`product_tmpl_id/id`) |
| 17 | `product_pricelist.csv` | product.pricelist | none (uses Odoo's built-in currency data) |

Within each numbered step: create custom fields (if any) &rarr; import base file &rarr; import
full-column file. Move to the next step only after that object's records exist, since later
objects reference earlier ones by External ID.

## Per-object detail

### account_analytic_plan.csv &rarr; `account.analytic.plan`

**Rows:** 1  
**Depends on:** none  
**Extra SAP columns available:** 0

**Step 2 - import the base file:** `output_odoo/account_analytic_plan.csv` (1 rows, 2 standard columns) - creates the records.


### uom_uom.csv &rarr; `uom.uom`

**Rows:** 38  
**Depends on:** none  
**Extra SAP columns available:** 2

**Step 1 - create these custom fields on `uom.uom` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `vmumaterial`/`MaterialBaseMeasureUnitCodeCollection` | `Code` | `x_sap_code` | Char |
| `vmumaterial`/`MaterialBaseMeasureUnitCodeCollection` | `Description` | `x_sap_description` | Char |

**Step 2 - import the base file:** `output_odoo/uom_uom.csv` (38 rows, 2 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/uom_uom.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### res_bank.csv &rarr; `res.bank`

**Rows:** 287  
**Depends on:** none  
**Extra SAP columns available:** 15

**Step 1 - create these custom fields on `res.bank` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `BankGroupCode` | `x_sap_bank_group_code` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `BankGroupCodeText` | `x_sap_bank_group_code_text` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `BankStandardID` | `x_sap_bank_standard_i_d` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `CityName` | `x_sap_city_name` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `CommonBankStandardIDMainIndicator` | `x_sap_common_bank_standard_i_d_main_indicator` | Boolean |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `CountryCode` | `x_sap_country_code` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `CountryCodeText` | `x_sap_country_code_text` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `DeletedIndicator` | `x_sap_deleted_indicator` | Boolean |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `OrganisationFormattedName` | `x_sap_organisation_formatted_name` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `RegionCode` | `x_sap_region_code` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `RegionCodeText` | `x_sap_region_code_text` | Char |
| `khhousebankaccount`/`BankDirectoryEntryCollection` | `StreetName` | `x_sap_street_name` | Char |

**Step 2 - import the base file:** `output_odoo/res_bank.csv` (287 rows, 3 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/res_bank.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### product_category.csv &rarr; `product.category`

**Rows:** 24  
**Depends on:** none  
**Extra SAP columns available:** 12

**Step 1 - create these custom fields on `product.category` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `vmumaterial`/`ProductCategoryCollection` | `Description` | `x_sap_description` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `Description` | `x_sap_description_2` | Char |
| `vmumaterial`/`ProductCategoryCollection` | `DescriptionLanguageCode` | `x_sap_description_language_code` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `DescriptionLanguageCode` | `x_sap_description_language_code_2` | Char |
| `vmumaterial`/`ProductCategoryCollection` | `DescriptionLanguageCodeText` | `x_sap_description_language_code_text` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `DescriptionLanguageCodeText` | `x_sap_description_language_code_text_2` | Char |
| `vmumaterial`/`ProductCategoryCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `ObjectID` | `x_sap_object_i_d_2` | Char |
| `vmumaterial`/`ProductCategoryCollection` | `ParentObjectID` | `x_sap_parent_object_i_d` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_2` | Char |
| `vmumaterial`/`ProductCategoryCollection` | `ProductCategoryInternalID` | `x_sap_product_category_internal_i_d` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `ProductCategoryInternalID` | `x_sap_product_category_internal_i_d_2` | Char |

**Step 2 - import the base file:** `output_odoo/product_category.csv` (24 rows, 2 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/product_category.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### account_account.csv &rarr; `account.account`

**Rows:** 213  
**Depends on:** none  
**Extra SAP columns available:** 10

**Step 1 - create these custom fields on `account.account` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `fin_costandrevenue_analytics.svc`/`RPFINCACU04_Q0002QueryResults` | `CGLACCT` | `x_sap_c_g_l_a_c_c_t` | Char |
| `fin_costandrevenue_analytics.svc`/`RPFINCACU04_Q0002QueryResults` | `TGLACCT` | `x_sap_t_g_l_a_c_c_t` | Char |
| `fin_generalledger_analytics.svc`/`RPFINFCDU02_Q0001QueryResults` | `CGLACCT` | `x_sap_c_g_l_a_c_c_t_4` | Char |
| `fin_generalledger_analytics.svc`/`RPFINFCDU02_Q0001QueryResults` | `TGLACCT` | `x_sap_t_g_l_a_c_c_t_4` | Char |
| `fin_generalledger_analytics.svc`/`RPFINFXAU05_Q0001QueryResults` | `CGLACCT` | `x_sap_c_g_l_a_c_c_t_3` | Char |
| `fin_generalledger_analytics.svc`/`RPFINFXAU05_Q0001QueryResults` | `TGLACCT` | `x_sap_t_g_l_a_c_c_t_3` | Char |
| `fin_audit_analytics.svc`/`RPFINGLAU02_Q0002QueryResults` | `CGLACCT` | `x_sap_c_g_l_a_c_c_t_2` | Char |
| `fin_audit_analytics.svc`/`RPFINGLAU02_Q0002QueryResults` | `TGLACCT` | `x_sap_t_g_l_a_c_c_t_2` | Char |
| `fin_audit_analytics.svc`/`RPFININVU03_Q0001QueryResults` | `CGLACCT` | `x_sap_c_g_l_a_c_c_t_5` | Char |
| `fin_audit_analytics.svc`/`RPFININVU03_Q0001QueryResults` | `TGLACCT` | `x_sap_t_g_l_a_c_c_t_5` | Char |

**Step 2 - import the base file:** `output_odoo/account_account.csv` (213 rows, 5 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/account_account.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### account_tax.csv &rarr; `account.tax`

**Rows:** 115  
**Depends on:** none  
**Extra SAP columns available:** 0

**Step 2 - import the base file:** `output_odoo/account_tax.csv` (115 rows, 6 standard columns) - creates the records.


### account_payment_term.csv &rarr; `account.payment.term`

**Rows:** 31  
**Depends on:** none  
**Extra SAP columns available:** 12

**Step 1 - create these custom fields on `account.payment.term` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `khsupplierinvoice`/`CashDiscountTermsCollection` | `Code` | `x_sap_code` | Char |
| `khsupplierinvoice`/`CashDiscountTermsCollection` | `CodeText` | `x_sap_code_text` | Char |
| `khcustomerinvoice`/`CashDiscountTermsCollection` | `FullPaymentEndDate` | `x_sap_full_payment_end_date` | Date |
| `khsupplierinvoice`/`CashDiscountTermsCollection` | `FullPaymentEndDate` | `x_sap_full_payment_end_date_2` | Date |
| `khcustomerinvoice`/`CashDiscountTermsCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `khsupplierinvoice`/`CashDiscountTermsCollection` | `ObjectID` | `x_sap_object_i_d_2` | Char |
| `khcustomerinvoice`/`CashDiscountTermsCollection` | `ParentObjectID` | `x_sap_parent_object_i_d` | Char |
| `khsupplierinvoice`/`CashDiscountTermsCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_2` | Char |
| `khcustomerinvoice`/`CashDiscountTermsCollection` | `PaymentBaselineDate` | `x_sap_payment_baseline_date` | Date |
| `khsupplierinvoice`/`CashDiscountTermsCollection` | `PaymentBaselineDate` | `x_sap_payment_baseline_date_2` | Date |
| `khcustomerinvoice`/`CashDiscountTermsCollection` | `PaymentTermsCode` | `x_sap_payment_terms_code` | Char |
| `khcustomerinvoice`/`CashDiscountTermsCollection` | `PaymentTermsCodeText` | `x_sap_payment_terms_code_text` | Char |

**Step 2 - import the base file:** `output_odoo/account_payment_term.csv` (31 rows, 2 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/account_payment_term.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### account_analytic_account_cc.csv &rarr; `account.analytic.account`

**Rows:** 13  
**Depends on:** account_analytic_plan.csv (plan_id/id)  
**Extra SAP columns available:** 8

**Step 1 - create these custom fields on `account.analytic.account` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `costcentre`/`CostCentreCollection` | `ID` | `x_sap_i_d` | Char |
| `costcentre`/`CostCentreCollection` | `MostRecentDefaultName` | `x_sap_most_recent_default_name` | Char |
| `costcentre`/`CostCentreCollection` | `MostRecentSuperodinateCompanyID` | `x_sap_most_recent_superodinate_company_i_d` | Char |
| `costcentre`/`CostCentreCollection` | `MostRecentSuperodinateCompanyUUID` | `x_sap_most_recent_superodinate_company_u_u_i_d` | Char |
| `costcentre`/`CostCentreCollection` | `MostRecentSuperodinatePermanentEstablishmentID` | `x_sap_most_recent_superodinate_permanent_establishment_i_d` | Char |
| `costcentre`/`CostCentreCollection` | `MostRecentSuperodinatePermanentEstablishmentUUID` | `x_sap_most_recent_superodinate_permanent_establishment_u_u_i_d` | Char |
| `costcentre`/`CostCentreCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `costcentre`/`CostCentreCollection` | `UUID` | `x_sap_u_u_i_d` | Char |

**Step 2 - import the base file:** `output_odoo/account_analytic_account_cc.csv` (13 rows, 4 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/account_analytic_account_cc.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### account_analytic_account.csv &rarr; `account.analytic.account`

**Rows:** 2  
**Depends on:** account_analytic_plan.csv (plan_id/id)  
**Extra SAP columns available:** 20

**Step 1 - create these custom fields on `account.analytic.account` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `khprofitcentre`/`DefinitionCollection` | `BusinessCharacterCode` | `x_sap_business_character_code` | Char |
| `khprofitcentre`/`DefinitionCollection` | `BusinessCharacterCodeText` | `x_sap_business_character_code_text` | Char |
| `khprofitcentre`/`DefinitionCollection` | `EndDate` | `x_sap_end_date` | Date |
| `khprofitcentre`/`DefinitionCollection` | `ObjectID` | `x_sap_object_i_d_2` | Char |
| `khprofitcentre`/`DefinitionCollection` | `ParentObjectID` | `x_sap_parent_object_i_d` | Char |
| `khprofitcentre`/`DefinitionCollection` | `StartDate` | `x_sap_start_date` | Date |
| `khprofitcentre`/`NameCollection` | `EndDate` | `x_sap_end_date_2` | Date |
| `khprofitcentre`/`NameCollection` | `Name` | `x_sap_name` | Char |
| `khprofitcentre`/`NameCollection` | `ObjectID` | `x_sap_object_i_d_3` | Char |
| `khprofitcentre`/`NameCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_2` | Char |
| `khprofitcentre`/`NameCollection` | `StartDate` | `x_sap_start_date_2` | Date |
| `khprofitcentre`/`ProfitCentreCollection` | `ID` | `x_sap_i_d` | Char |
| `khprofitcentre`/`ProfitCentreCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `khprofitcentre`/`ProfitCentreCollection` | `UUID` | `x_sap_u_u_i_d` | Char |
| `khprofitcentre`/`SegmentNameCollection` | `Name` | `x_sap_name_2` | Char |
| `khprofitcentre`/`SegmentNameCollection` | `ObjectID` | `x_sap_object_i_d_4` | Char |
| `khprofitcentre`/`SegmentNameCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_3` | Char |
| `khprofitcentre`/`SuperordinateProfitCentreNameCollection` | `Name` | `x_sap_name_3` | Char |
| `khprofitcentre`/`SuperordinateProfitCentreNameCollection` | `ObjectID` | `x_sap_object_i_d_5` | Char |
| `khprofitcentre`/`SuperordinateProfitCentreNameCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_4` | Char |

**Step 2 - import the base file:** `output_odoo/account_analytic_account.csv` (2 rows, 4 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/account_analytic_account.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### res_partner.csv &rarr; `res.partner`

**Rows:** 2404  
**Depends on:** none (country_id/id uses Odoo's built-in base.<iso2> data)  
**Extra SAP columns available:** 267

**Step 1 - create these custom fields on `res.partner` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `khcustomer`/`AddressInformationCollection` | `ObjectID` | `x_sap_object_i_d_2` | Char |
| `khcustomer`/`AddressInformationCollection` | `ParentObjectID` | `x_sap_parent_object_i_d` | Char |
| `khcustomer`/`AddressInformationCollection` | `UUID` | `x_sap_u_u_i_d_2` | Char |
| `khcustomer`/`AddressUsageCollection` | `AddressUsageCode` | `x_sap_address_usage_code` | Char |
| `khcustomer`/`AddressUsageCollection` | `AddressUsageCodeText` | `x_sap_address_usage_code_text` | Char |
| `khcustomer`/`AddressUsageCollection` | `DefaultIndicator` | `x_sap_default_indicator` | Boolean |
| `khcustomer`/`AddressUsageCollection` | `ObjectID` | `x_sap_object_i_d_3` | Char |
| `khcustomer`/`AddressUsageCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_2` | Char |
| `khcustomer`/`AddressUsageCollection` | `ValidityEndDate` | `x_sap_validity_end_date` | Date |
| `khcustomer`/`AddressUsageCollection` | `ValidityStartDate` | `x_sap_validity_start_date` | Date |
| `khcustomer`/`BankDetailsCollection` | `BankAccountHolderName` | `x_sap_bank_account_holder_name` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankAccountHolderName` | `x_sap_bank_account_holder_name_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankAccountID` | `x_sap_bank_account_i_d` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankAccountID` | `x_sap_bank_account_i_d_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankAccountIDCheckDigitValue` | `x_sap_bank_account_i_d_check_digit_value` | Float |
| `khsupplier`/`BankDetailsCollection` | `BankAccountIDCheckDigitValue` | `x_sap_bank_account_i_d_check_digit_value_2` | Float |
| `khcustomer`/`BankDetailsCollection` | `BankAccountStandardID` | `x_sap_bank_account_standard_i_d` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankAccountStandardID` | `x_sap_bank_account_standard_i_d_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankAccountTypeCode` | `x_sap_bank_account_type_code` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankAccountTypeCode` | `x_sap_bank_account_type_code_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankAccountTypeCodeText` | `x_sap_bank_account_type_code_text` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankAccountTypeCodeText` | `x_sap_bank_account_type_code_text_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankCityName` | `x_sap_bank_city_name` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankCityName` | `x_sap_bank_city_name_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankCountryCode` | `x_sap_bank_country_code` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankCountryCode` | `x_sap_bank_country_code_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankCountryCodeText` | `x_sap_bank_country_code_text` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankCountryCodeText` | `x_sap_bank_country_code_text_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankDirectoryEntryUUID` | `x_sap_bank_directory_entry_u_u_i_d` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankDirectoryEntryUUID` | `x_sap_bank_directory_entry_u_u_i_d_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankFormattedName` | `x_sap_bank_formatted_name` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankFormattedName` | `x_sap_bank_formatted_name_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankGroupCode` | `x_sap_bank_group_code` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankGroupCode` | `x_sap_bank_group_code_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankGroupCodeText` | `x_sap_bank_group_code_text` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankGroupCodeText` | `x_sap_bank_group_code_text_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankInternalID` | `x_sap_bank_internal_i_d` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankInternalID` | `x_sap_bank_internal_i_d_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `BankStandardID` | `x_sap_bank_standard_i_d` | Char |
| `khsupplier`/`BankDetailsCollection` | `BankStandardID` | `x_sap_bank_standard_i_d_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `EndDate` | `x_sap_end_date` | Date |
| `khsupplier`/`BankDetailsCollection` | `EndDate` | `x_sap_end_date_2` | Date |
| `khcustomer`/`BankDetailsCollection` | `ID` | `x_sap_i_d` | Char |
| `khsupplier`/`BankDetailsCollection` | `ID` | `x_sap_i_d_2` | Char |
| `khcustomer`/`BankDetailsCollection` | `ObjectID` | `x_sap_object_i_d_4` | Char |
| `khsupplier`/`BankDetailsCollection` | `ObjectID` | `x_sap_object_i_d_16` | Char |
| `khcustomer`/`BankDetailsCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_3` | Char |
| `khsupplier`/`BankDetailsCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_14` | Char |
| `khcustomer`/`BankDetailsCollection` | `StartDate` | `x_sap_start_date` | Date |
| `khsupplier`/`BankDetailsCollection` | `StartDate` | `x_sap_start_date_2` | Date |
| `khcustomer`/`CommunicationPreferenceCollection` | `CorrespondenceLanguageCode` | `x_sap_correspondence_language_code` | Char |
| `khcustomer`/`CommunicationPreferenceCollection` | `CorrespondenceLanguageCodeText` | `x_sap_correspondence_language_code_text` | Char |
| `khcustomer`/`CommunicationPreferenceCollection` | `ObjectID` | `x_sap_object_i_d_5` | Char |
| `khcustomer`/`CommunicationPreferenceCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_4` | Char |
| `khcustomer`/`CommunicationPreferenceCollection` | `PreferredCommunicationMediumTypeCode` | `x_sap_preferred_communication_medium_type_code` | Char |
| `khcustomer`/`CommunicationPreferenceCollection` | `PreferredCommunicationMediumTypeCodeText` | `x_sap_preferred_communication_medium_type_code_text` | Char |
| `khcustomer`/`ConventionalPhoneCollection` | `FormattedNumberDescription` | `x_sap_formatted_number_description` | Char |
| `khcustomer`/`ConventionalPhoneCollection` | `NormalisedNumberDescription` | `x_sap_normalised_number_description` | Char |
| `khcustomer`/`ConventionalPhoneCollection` | `ObjectID` | `x_sap_object_i_d_6` | Char |
| `khcustomer`/`ConventionalPhoneCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_5` | Char |
| `khsupplier`/`CurrentDefaultAddressInformationCollection` | `ObjectID` | `x_sap_object_i_d_17` | Char |
| `khsupplier`/`CurrentDefaultAddressInformationCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_15` | Char |
| `khsupplier`/`CurrentDefaultAddressInformationCollection` | `UUID` | `x_sap_u_u_i_d_4` | Char |
| `khsupplier`/`CurrentDefaultCommunicationPreferenceCollection` | `CorrespondenceLanguageCode` | `x_sap_correspondence_language_code_2` | Char |
| `khsupplier`/`CurrentDefaultCommunicationPreferenceCollection` | `CorrespondenceLanguageCodeText` | `x_sap_correspondence_language_code_text_2` | Char |
| `khsupplier`/`CurrentDefaultCommunicationPreferenceCollection` | `ObjectID` | `x_sap_object_i_d_18` | Char |
| `khsupplier`/`CurrentDefaultCommunicationPreferenceCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_16` | Char |
| `khsupplier`/`CurrentDefaultCommunicationPreferenceCollection` | `PreferredCommunicationMediumTypeCode` | `x_sap_preferred_communication_medium_type_code_2` | Char |
| `khsupplier`/`CurrentDefaultCommunicationPreferenceCollection` | `PreferredCommunicationMediumTypeCodeText` | `x_sap_preferred_communication_medium_type_code_text_2` | Char |
| `khsupplier`/`CurrentDefaultConventionalPhoneCollection` | `FormattedNumberDescription` | `x_sap_formatted_number_description_4` | Char |
| `khsupplier`/`CurrentDefaultConventionalPhoneCollection` | `NormalisedNumberDescription` | `x_sap_normalised_number_description_4` | Char |
| `khsupplier`/`CurrentDefaultConventionalPhoneCollection` | `ObjectID` | `x_sap_object_i_d_19` | Char |
| `khsupplier`/`CurrentDefaultConventionalPhoneCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_17` | Char |
| `khsupplier`/`CurrentDefaultEMailCollection` | `NormalisedURI` | `x_sap_normalised_u_r_i_2` | Char |
| `khsupplier`/`CurrentDefaultEMailCollection` | `ObjectID` | `x_sap_object_i_d_20` | Char |
| `khsupplier`/`CurrentDefaultEMailCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_18` | Char |
| `khsupplier`/`CurrentDefaultEMailCollection` | `URI` | `x_sap_u_r_i_3` | Char |
| `khsupplier`/`CurrentDefaultFormattedAddressCollection` | `FormattedPostalAddressDescription` | `x_sap_formatted_postal_address_description_2` | Char |
| `khsupplier`/`CurrentDefaultFormattedAddressCollection` | `ObjectID` | `x_sap_object_i_d_21` | Char |
| `khsupplier`/`CurrentDefaultFormattedAddressCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_19` | Char |
| `khsupplier`/`CurrentDefaultMobilePhoneCollection` | `FormattedNumberDescription` | `x_sap_formatted_number_description_5` | Char |
| `khsupplier`/`CurrentDefaultMobilePhoneCollection` | `NormalisedNumberDescription` | `x_sap_normalised_number_description_5` | Char |
| `khsupplier`/`CurrentDefaultMobilePhoneCollection` | `ObjectID` | `x_sap_object_i_d_22` | Char |
| `khsupplier`/`CurrentDefaultMobilePhoneCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_20` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `CareOfName` | `x_sap_care_of_name_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `CityName` | `x_sap_city_name_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `CompanyPostalCode` | `x_sap_company_postal_code_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `CountryCode` | `x_sap_country_code_3` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `CountryCodeText` | `x_sap_country_code_text_3` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `CountyName` | `x_sap_county_name_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `DifferentCityName` | `x_sap_different_city_name_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `DistrictName` | `x_sap_district_name_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `HouseID` | `x_sap_house_i_d_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `ObjectID` | `x_sap_object_i_d_23` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `POBoxDeviatingCityName` | `x_sap_p_o_box_deviating_city_name_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `POBoxDeviatingCountryCode` | `x_sap_p_o_box_deviating_country_code_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `POBoxDeviatingCountryCodeText` | `x_sap_p_o_box_deviating_country_code_text_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `POBoxDeviatingRegionCode` | `x_sap_p_o_box_deviating_region_code_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `POBoxDeviatingRegionCodeText` | `x_sap_p_o_box_deviating_region_code_text_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `POBoxID` | `x_sap_p_o_box_i_d_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `POBoxPostalCode` | `x_sap_p_o_box_postal_code_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_21` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `RegionCode` | `x_sap_region_code_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `RegionCodeText` | `x_sap_region_code_text_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `StreetName` | `x_sap_street_name_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `StreetPostalCode` | `x_sap_street_postal_code_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `StreetPrefixName` | `x_sap_street_prefix_name_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `StreetSuffixName` | `x_sap_street_suffix_name_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `TaxJurisdictionCode` | `x_sap_tax_jurisdiction_code_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `TaxJurisdictionCodeText` | `x_sap_tax_jurisdiction_code_text_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `TimeZoneCode` | `x_sap_time_zone_code_2` | Char |
| `khsupplier`/`CurrentDefaultPostalAddressCollection` | `TimeZoneCodeText` | `x_sap_time_zone_code_text_2` | Char |
| `khsupplier`/`CurrentDefaultWebSiteCollection` | `ObjectID` | `x_sap_object_i_d_24` | Char |
| `khsupplier`/`CurrentDefaultWebSiteCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_22` | Char |
| `khsupplier`/`CurrentDefaultWebSiteCollection` | `URI` | `x_sap_u_r_i_4` | Char |
| `khcustomer`/`CustomerCollection` | `ABCClassificationCode` | `x_sap_a_b_c_classification_code` | Char |
| `khcustomer`/`CustomerCollection` | `ABCClassificationCodeText` | `x_sap_a_b_c_classification_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `BusinessPartnerFormattedName` | `x_sap_business_partner_formatted_name` | Char |
| `khcustomer`/`CustomerCollection` | `CategoryCode` | `x_sap_category_code` | Char |
| `khcustomer`/`CustomerCollection` | `CategoryCodeText` | `x_sap_category_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `CompanyLegalFormCode` | `x_sap_company_legal_form_code` | Char |
| `khcustomer`/`CustomerCollection` | `CompanyLegalFormCodeText` | `x_sap_company_legal_form_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `ContactAllowedCode` | `x_sap_contact_allowed_code` | Char |
| `khcustomer`/`CustomerCollection` | `ContactAllowedCodeText` | `x_sap_contact_allowed_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `CreationDateTime` | `x_sap_creation_date_time` | Datetime |
| `khcustomer`/`CustomerCollection` | `FulfilmentBlockingReasonCode` | `x_sap_fulfilment_blocking_reason_code` | Char |
| `khcustomer`/`CustomerCollection` | `FulfilmentBlockingReasonCodeText` | `x_sap_fulfilment_blocking_reason_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `IndustrialSectorCode` | `x_sap_industrial_sector_code` | Char |
| `khcustomer`/`CustomerCollection` | `IndustrialSectorCodeText` | `x_sap_industrial_sector_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `InternalID` | `x_sap_internal_i_d` | Char |
| `khcustomer`/`CustomerCollection` | `InvoicingBlockingReasonCode` | `x_sap_invoicing_blocking_reason_code` | Char |
| `khcustomer`/`CustomerCollection` | `InvoicingBlockingReasonCodeText` | `x_sap_invoicing_blocking_reason_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `LastChangeDateTime` | `x_sap_last_change_date_time` | Datetime |
| `khcustomer`/`CustomerCollection` | `LegalCompetenceIndicator` | `x_sap_legal_competence_indicator` | Boolean |
| `khcustomer`/`CustomerCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code` | Char |
| `khcustomer`/`CustomerCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `NielsenID` | `x_sap_nielsen_i_d` | Char |
| `khcustomer`/`CustomerCollection` | `NielsenIDText` | `x_sap_nielsen_i_d_text` | Char |
| `khcustomer`/`CustomerCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `khcustomer`/`CustomerCollection` | `OrderBlockingReasonCode` | `x_sap_order_blocking_reason_code` | Char |
| `khcustomer`/`CustomerCollection` | `OrderBlockingReasonCodeText` | `x_sap_order_blocking_reason_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `OrganisationFirstLineName` | `x_sap_organisation_first_line_name` | Char |
| `khcustomer`/`CustomerCollection` | `OrganisationSecondLineName` | `x_sap_organisation_second_line_name` | Char |
| `khcustomer`/`CustomerCollection` | `PersonAcademicTitleCode` | `x_sap_person_academic_title_code` | Char |
| `khcustomer`/`CustomerCollection` | `PersonAcademicTitleCodeText` | `x_sap_person_academic_title_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `PersonBirthDate` | `x_sap_person_birth_date` | Date |
| `khcustomer`/`CustomerCollection` | `PersonBirthName` | `x_sap_person_birth_name` | Char |
| `khcustomer`/`CustomerCollection` | `PersonCommunicationLanguageCode` | `x_sap_person_communication_language_code` | Char |
| `khcustomer`/`CustomerCollection` | `PersonCommunicationLanguageCodeText` | `x_sap_person_communication_language_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `PersonFamilyName` | `x_sap_person_family_name` | Char |
| `khcustomer`/`CustomerCollection` | `PersonFormOfAddressCode` | `x_sap_person_form_of_address_code` | Char |
| `khcustomer`/`CustomerCollection` | `PersonFormOfAddressCodeText` | `x_sap_person_form_of_address_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `PersonGenderCode` | `x_sap_person_gender_code` | Char |
| `khcustomer`/`CustomerCollection` | `PersonGenderCodeText` | `x_sap_person_gender_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `PersonGivenName` | `x_sap_person_given_name` | Char |
| `khcustomer`/`CustomerCollection` | `PersonMiddleName` | `x_sap_person_middle_name` | Char |
| `khcustomer`/`CustomerCollection` | `PersonNickName` | `x_sap_person_nick_name` | Char |
| `khcustomer`/`CustomerCollection` | `PersonProfessionCode` | `x_sap_person_profession_code` | Char |
| `khcustomer`/`CustomerCollection` | `PersonProfessionCodeText` | `x_sap_person_profession_code_text` | Char |
| `khcustomer`/`CustomerCollection` | `SortingFormattedName` | `x_sap_sorting_formatted_name` | Char |
| `khcustomer`/`CustomerCollection` | `UUID` | `x_sap_u_u_i_d` | Char |
| `khcustomer`/`EMailCollection` | `NormalisedURI` | `x_sap_normalised_u_r_i` | Char |
| `khcustomer`/`EMailCollection` | `ObjectID` | `x_sap_object_i_d_7` | Char |
| `khcustomer`/`EMailCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_6` | Char |
| `khcustomer`/`EMailCollection` | `URI` | `x_sap_u_r_i` | Char |
| `khcustomer`/`FacsimileCollection` | `FormattedNumberDescription` | `x_sap_formatted_number_description_2` | Char |
| `khcustomer`/`FacsimileCollection` | `NormalisedNumberDescription` | `x_sap_normalised_number_description_2` | Char |
| `khcustomer`/`FacsimileCollection` | `ObjectID` | `x_sap_object_i_d_8` | Char |
| `khcustomer`/`FacsimileCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_7` | Char |
| `khcustomer`/`FormattedAddressCollection` | `FormattedPostalAddressDescription` | `x_sap_formatted_postal_address_description` | Char |
| `khcustomer`/`FormattedAddressCollection` | `ObjectID` | `x_sap_object_i_d_9` | Char |
| `khcustomer`/`FormattedAddressCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_8` | Char |
| `khcustomer`/`MobilePhoneCollection` | `FormattedNumberDescription` | `x_sap_formatted_number_description_3` | Char |
| `khcustomer`/`MobilePhoneCollection` | `NormalisedNumberDescription` | `x_sap_normalised_number_description_3` | Char |
| `khcustomer`/`MobilePhoneCollection` | `ObjectID` | `x_sap_object_i_d_10` | Char |
| `khcustomer`/`MobilePhoneCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_9` | Char |
| `khcustomer`/`PostalAddressCollection` | `CareOfName` | `x_sap_care_of_name` | Char |
| `khcustomer`/`PostalAddressCollection` | `CityName` | `x_sap_city_name` | Char |
| `khcustomer`/`PostalAddressCollection` | `CompanyPostalCode` | `x_sap_company_postal_code` | Char |
| `khcustomer`/`PostalAddressCollection` | `CountryCode` | `x_sap_country_code` | Char |
| `khcustomer`/`PostalAddressCollection` | `CountryCodeText` | `x_sap_country_code_text` | Char |
| `khcustomer`/`PostalAddressCollection` | `CountyName` | `x_sap_county_name` | Char |
| `khcustomer`/`PostalAddressCollection` | `DifferentCityName` | `x_sap_different_city_name` | Char |
| `khcustomer`/`PostalAddressCollection` | `DistrictName` | `x_sap_district_name` | Char |
| `khcustomer`/`PostalAddressCollection` | `HouseID` | `x_sap_house_i_d` | Char |
| `khcustomer`/`PostalAddressCollection` | `ObjectID` | `x_sap_object_i_d_11` | Char |
| `khcustomer`/`PostalAddressCollection` | `POBoxDeviatingCityName` | `x_sap_p_o_box_deviating_city_name` | Char |
| `khcustomer`/`PostalAddressCollection` | `POBoxDeviatingCountryCode` | `x_sap_p_o_box_deviating_country_code` | Char |
| `khcustomer`/`PostalAddressCollection` | `POBoxDeviatingCountryCodeText` | `x_sap_p_o_box_deviating_country_code_text` | Char |
| `khcustomer`/`PostalAddressCollection` | `POBoxDeviatingRegionCode` | `x_sap_p_o_box_deviating_region_code` | Char |
| `khcustomer`/`PostalAddressCollection` | `POBoxDeviatingRegionCodeText` | `x_sap_p_o_box_deviating_region_code_text` | Char |
| `khcustomer`/`PostalAddressCollection` | `POBoxID` | `x_sap_p_o_box_i_d` | Char |
| `khcustomer`/`PostalAddressCollection` | `POBoxPostalCode` | `x_sap_p_o_box_postal_code` | Char |
| `khcustomer`/`PostalAddressCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_10` | Char |
| `khcustomer`/`PostalAddressCollection` | `RegionCode` | `x_sap_region_code` | Char |
| `khcustomer`/`PostalAddressCollection` | `RegionCodeText` | `x_sap_region_code_text` | Char |
| `khcustomer`/`PostalAddressCollection` | `StreetName` | `x_sap_street_name` | Char |
| `khcustomer`/`PostalAddressCollection` | `StreetPostalCode` | `x_sap_street_postal_code` | Char |
| `khcustomer`/`PostalAddressCollection` | `StreetPrefixName` | `x_sap_street_prefix_name` | Char |
| `khcustomer`/`PostalAddressCollection` | `StreetSuffixName` | `x_sap_street_suffix_name` | Char |
| `khcustomer`/`PostalAddressCollection` | `TaxJurisdictionCode` | `x_sap_tax_jurisdiction_code` | Char |
| `khcustomer`/`PostalAddressCollection` | `TaxJurisdictionCodeText` | `x_sap_tax_jurisdiction_code_text` | Char |
| `khcustomer`/`PostalAddressCollection` | `TimeZoneCode` | `x_sap_time_zone_code` | Char |
| `khcustomer`/`PostalAddressCollection` | `TimeZoneCodeText` | `x_sap_time_zone_code_text` | Char |
| `khcustomer`/`RoleCollection` | `BusinessCharacterCode` | `x_sap_business_character_code` | Char |
| `khsupplier`/`RoleCollection` | `BusinessCharacterCode` | `x_sap_business_character_code_2` | Char |
| `khcustomer`/`RoleCollection` | `BusinessCharacterCodeText` | `x_sap_business_character_code_text` | Char |
| `khsupplier`/`RoleCollection` | `BusinessCharacterCodeText` | `x_sap_business_character_code_text_2` | Char |
| `khcustomer`/`RoleCollection` | `BusinessObjectTypeCode` | `x_sap_business_object_type_code` | Char |
| `khsupplier`/`RoleCollection` | `BusinessObjectTypeCode` | `x_sap_business_object_type_code_2` | Char |
| `khcustomer`/`RoleCollection` | `BusinessObjectTypeCodeText` | `x_sap_business_object_type_code_text` | Char |
| `khsupplier`/`RoleCollection` | `BusinessObjectTypeCodeText` | `x_sap_business_object_type_code_text_2` | Char |
| `khcustomer`/`RoleCollection` | `ObjectID` | `x_sap_object_i_d_12` | Char |
| `khsupplier`/`RoleCollection` | `ObjectID` | `x_sap_object_i_d_25` | Char |
| `khcustomer`/`RoleCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_11` | Char |
| `khsupplier`/`RoleCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_23` | Char |
| `khcustomer`/`RoleCollection` | `RoleCode` | `x_sap_role_code` | Char |
| `khsupplier`/`RoleCollection` | `RoleCode` | `x_sap_role_code_2` | Char |
| `khcustomer`/`RoleCollection` | `RoleCodeText` | `x_sap_role_code_text` | Char |
| `khsupplier`/`RoleCollection` | `RoleCodeText` | `x_sap_role_code_text_2` | Char |
| `khsupplier`/`SupplierCollection` | `ABCClassificationCode` | `x_sap_a_b_c_classification_code_2` | Char |
| `khsupplier`/`SupplierCollection` | `ABCClassificationCodeText` | `x_sap_a_b_c_classification_code_text_2` | Char |
| `khsupplier`/`SupplierCollection` | `BusinessPartnerFormattedName` | `x_sap_business_partner_formatted_name_2` | Char |
| `khsupplier`/`SupplierCollection` | `CompanyLegalFormCode` | `x_sap_company_legal_form_code_2` | Char |
| `khsupplier`/`SupplierCollection` | `CompanyLegalFormCodeText` | `x_sap_company_legal_form_code_text_2` | Char |
| `khsupplier`/`SupplierCollection` | `CreationDateTime` | `x_sap_creation_date_time_2` | Datetime |
| `khsupplier`/`SupplierCollection` | `FirstLineName` | `x_sap_first_line_name` | Char |
| `khsupplier`/`SupplierCollection` | `IndustrialSectorCode` | `x_sap_industrial_sector_code_2` | Char |
| `khsupplier`/`SupplierCollection` | `IndustrialSectorCodeText` | `x_sap_industrial_sector_code_text_2` | Char |
| `khsupplier`/`SupplierCollection` | `InternalID` | `x_sap_internal_i_d_2` | Char |
| `khsupplier`/`SupplierCollection` | `LastChangeDateTime` | `x_sap_last_change_date_time_2` | Datetime |
| `khsupplier`/`SupplierCollection` | `LegalCompetenceIndicator` | `x_sap_legal_competence_indicator_2` | Boolean |
| `khsupplier`/`SupplierCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code_2` | Char |
| `khsupplier`/`SupplierCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text_2` | Char |
| `khsupplier`/`SupplierCollection` | `ObjectID` | `x_sap_object_i_d_15` | Char |
| `khsupplier`/`SupplierCollection` | `SecondLineName` | `x_sap_second_line_name` | Char |
| `khsupplier`/`SupplierCollection` | `SortingFormattedName` | `x_sap_sorting_formatted_name_2` | Char |
| `khsupplier`/`SupplierCollection` | `UUID` | `x_sap_u_u_i_d_3` | Char |
| `khcustomer`/`TaxNumberCollection` | `CountryCode` | `x_sap_country_code_2` | Char |
| `khsupplier`/`TaxNumberCollection` | `CountryCode` | `x_sap_country_code_4` | Char |
| `khcustomer`/`TaxNumberCollection` | `CountryCodeText` | `x_sap_country_code_text_2` | Char |
| `khsupplier`/`TaxNumberCollection` | `CountryCodeText` | `x_sap_country_code_text_4` | Char |
| `khcustomer`/`TaxNumberCollection` | `ObjectID` | `x_sap_object_i_d_13` | Char |
| `khsupplier`/`TaxNumberCollection` | `ObjectID` | `x_sap_object_i_d_26` | Char |
| `khcustomer`/`TaxNumberCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_12` | Char |
| `khsupplier`/`TaxNumberCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_24` | Char |
| `khsupplier`/`TaxNumberCollection` | `PartyTaxID` | `x_sap_party_tax_i_d` | Char |
| `khsupplier`/`TaxNumberCollection` | `TaxIdentificationNumberTypeCode` | `x_sap_tax_identification_number_type_code` | Char |
| `khsupplier`/`TaxNumberCollection` | `TaxIdentificationNumberTypeCodeText` | `x_sap_tax_identification_number_type_code_text` | Char |
| `khcustomer`/`TaxNumberCollection` | `TaxNumberID` | `x_sap_tax_number_i_d` | Char |
| `khcustomer`/`TaxNumberCollection` | `TaxNumberTypeCode` | `x_sap_tax_number_type_code` | Char |
| `khcustomer`/`TaxNumberCollection` | `TaxNumberTypeCodeText` | `x_sap_tax_number_type_code_text` | Char |
| `khcustomer`/`WebSiteCollection` | `ObjectID` | `x_sap_object_i_d_14` | Char |
| `khcustomer`/`WebSiteCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_13` | Char |
| `khcustomer`/`WebSiteCollection` | `URI` | `x_sap_u_r_i_2` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `CountryCode` | `x_sap_country_code_5` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `CountryCodeText` | `x_sap_country_code_text_5` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `ObjectID` | `x_sap_object_i_d_27` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_25` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `TaxExemptionReasonCode` | `x_sap_tax_exemption_reason_code` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `TaxExemptionReasonCodeText` | `x_sap_tax_exemption_reason_code_text` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `TaxRateTypeCode` | `x_sap_tax_rate_type_code` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `TaxRateTypeCodeText` | `x_sap_tax_rate_type_code_text` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `TaxTypeCode` | `x_sap_tax_type_code` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `TaxTypeCodeText` | `x_sap_tax_type_code_text` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `WithholdingTaxIncomeTypeCode` | `x_sap_withholding_tax_income_type_code` | Char |
| `khsupplier`/`WithholdingTaxClassificationCollection` | `WithholdingTaxIncomeTypeCodeText` | `x_sap_withholding_tax_income_type_code_text` | Char |

**Step 2 - import the base file:** `output_odoo/res_partner.csv` (2404 rows, 13 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/res_partner.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### res_users.csv &rarr; `res.users`

**Rows:** 102  
**Depends on:** none  
**Extra SAP columns available:** 41

**Step 1 - create these custom fields on `res.users` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `khemployee`/`EmployeeCollection` | `AcademicTitleCode` | `x_sap_academic_title_code` | Char |
| `khemployee`/`EmployeeCollection` | `AcademicTitleCodeText` | `x_sap_academic_title_code_text` | Char |
| `khemployee`/`EmployeeCollection` | `BirthDate` | `x_sap_birth_date` | Date |
| `khemployee`/`EmployeeCollection` | `BirthName` | `x_sap_birth_name` | Char |
| `khemployee`/`EmployeeCollection` | `BirthPlaceName` | `x_sap_birth_place_name` | Char |
| `khemployee`/`EmployeeCollection` | `CreationDateTime` | `x_sap_creation_date_time` | Datetime |
| `khemployee`/`EmployeeCollection` | `EmployeeID` | `x_sap_employee_i_d` | Char |
| `khemployee`/`EmployeeCollection` | `FamilyName` | `x_sap_family_name` | Char |
| `khemployee`/`EmployeeCollection` | `FormOfAddressCode` | `x_sap_form_of_address_code` | Char |
| `khemployee`/`EmployeeCollection` | `FormOfAddressCodeText` | `x_sap_form_of_address_code_text` | Char |
| `khemployee`/`EmployeeCollection` | `FormattedName` | `x_sap_formatted_name` | Char |
| `khemployee`/`EmployeeCollection` | `GenderCode` | `x_sap_gender_code` | Char |
| `khemployee`/`EmployeeCollection` | `GenderCodeText` | `x_sap_gender_code_text` | Char |
| `khemployee`/`EmployeeCollection` | `GivenName` | `x_sap_given_name` | Char |
| `khemployee`/`EmployeeCollection` | `InternalID` | `x_sap_internal_i_d` | Char |
| `khemployee`/`EmployeeCollection` | `LastChangeDateTime` | `x_sap_last_change_date_time` | Datetime |
| `khemployee`/`EmployeeCollection` | `MaritalStatusCode` | `x_sap_marital_status_code` | Char |
| `khemployee`/`EmployeeCollection` | `MaritalStatusCodeText` | `x_sap_marital_status_code_text` | Char |
| `khemployee`/`EmployeeCollection` | `MiddleName` | `x_sap_middle_name` | Char |
| `khemployee`/`EmployeeCollection` | `NationalityCountryCode` | `x_sap_nationality_country_code` | Char |
| `khemployee`/`EmployeeCollection` | `NationalityCountryCodeText` | `x_sap_nationality_country_code_text` | Char |
| `khemployee`/`EmployeeCollection` | `NickName` | `x_sap_nick_name` | Char |
| `khemployee`/`EmployeeCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `khemployee`/`EmployeeCollection` | `UUID` | `x_sap_u_u_i_d` | Char |
| `khemployee`/`EmployeeTypeCollection` | `EndDate` | `x_sap_end_date` | Date |
| `khemployee`/`EmployeeTypeCollection` | `InternalEmployeeIndicator` | `x_sap_internal_employee_indicator` | Boolean |
| `khemployee`/`EmployeeTypeCollection` | `ObjectID` | `x_sap_object_i_d_2` | Char |
| `khemployee`/`EmployeeTypeCollection` | `ParentObjectID` | `x_sap_parent_object_i_d` | Char |
| `khemployee`/`EmployeeTypeCollection` | `StartDate` | `x_sap_start_date` | Date |
| `khemployee`/`WorkplaceAddressCollection` | `Building` | `x_sap_building` | Char |
| `khemployee`/`WorkplaceAddressCollection` | `Email` | `x_sap_email` | Char |
| `khemployee`/`WorkplaceAddressCollection` | `Fax` | `x_sap_fax` | Char |
| `khemployee`/`WorkplaceAddressCollection` | `Floor` | `x_sap_floor` | Char |
| `khemployee`/`WorkplaceAddressCollection` | `Mobile` | `x_sap_mobile` | Char |
| `khemployee`/`WorkplaceAddressCollection` | `ObjectID` | `x_sap_object_i_d_3` | Char |
| `khemployee`/`WorkplaceAddressCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_2` | Char |
| `khemployee`/`WorkplaceAddressCollection` | `Phone` | `x_sap_phone` | Char |
| `khemployee`/`WorkplaceAddressCollection` | `Room` | `x_sap_room` | Char |
| `khemployee`/`WorkplaceAddressInformationCollection` | `ObjectID` | `x_sap_object_i_d_4` | Char |
| `khemployee`/`WorkplaceAddressInformationCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_3` | Char |
| `khemployee`/`WorkplaceAddressInformationCollection` | `UUID` | `x_sap_u_u_i_d_2` | Char |

**Step 2 - import the base file:** `output_odoo/res_users.csv` (102 rows, 5 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/res_users.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### stock_warehouse.csv &rarr; `stock.warehouse`

**Rows:** 1  
**Depends on:** none  
**Extra SAP columns available:** 30

**Step 1 - create these custom fields on `stock.warehouse` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `khlocation`/`LocationCollection` | `AltitudeMeasure` | `x_sap_altitude_measure` | Float |
| `khlocation`/`LocationCollection` | `AltitudeUnitCode` | `x_sap_altitude_unit_code` | Char |
| `khlocation`/`LocationCollection` | `CreationDateTime` | `x_sap_creation_date_time` | Datetime |
| `khlocation`/`LocationCollection` | `Description` | `x_sap_description` | Char |
| `khlocation`/`LocationCollection` | `ExternallyManagedIndicator` | `x_sap_externally_managed_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `ID` | `x_sap_i_d` | Char |
| `khlocation`/`LocationCollection` | `InventoryManagedLocationIndicator` | `x_sap_inventory_managed_location_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `LastChangeDateTime` | `x_sap_last_change_date_time` | Datetime |
| `khlocation`/`LocationCollection` | `LatitudeMeasure` | `x_sap_latitude_measure` | Float |
| `khlocation`/`LocationCollection` | `LatitudeUnitCode` | `x_sap_latitude_unit_code` | Char |
| `khlocation`/`LocationCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code` | Char |
| `khlocation`/`LocationCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text` | Char |
| `khlocation`/`LocationCollection` | `LongitudeMeasure` | `x_sap_longitude_measure` | Float |
| `khlocation`/`LocationCollection` | `LongitudeUnitCode` | `x_sap_longitude_unit_code` | Char |
| `khlocation`/`LocationCollection` | `Name` | `x_sap_name` | Char |
| `khlocation`/`LocationCollection` | `NegativeInTransitInventoryAllowedIndicator` | `x_sap_negative_in_transit_inventory_allowed_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `NegativeInventoryAllowedIndicator` | `x_sap_negative_inventory_allowed_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `khlocation`/`LocationCollection` | `ParentLocationID` | `x_sap_parent_location_i_d` | Char |
| `khlocation`/`LocationCollection` | `ParentLocationName` | `x_sap_parent_location_name` | Char |
| `khlocation`/`LocationCollection` | `ParentLocationUUID` | `x_sap_parent_location_u_u_i_d` | Char |
| `khlocation`/`LocationCollection` | `ServicePointIndicator` | `x_sap_service_point_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `ShipFromLocationIndicator` | `x_sap_ship_from_location_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `ShipToLocationIndicator` | `x_sap_ship_to_location_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `SiteIndicator` | `x_sap_site_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `TimeZoneCode` | `x_sap_time_zone_code` | Char |
| `khlocation`/`LocationCollection` | `TimeZoneCodeText` | `x_sap_time_zone_code_text` | Char |
| `khlocation`/`LocationCollection` | `UUID` | `x_sap_u_u_i_d` | Char |
| `khlocation`/`LocationCollection` | `WorkingDayCalendarCode` | `x_sap_working_day_calendar_code` | Char |
| `khlocation`/`LocationCollection` | `WorkingDayCalendarCodeText` | `x_sap_working_day_calendar_code_text` | Char |

**Step 2 - import the base file:** `output_odoo/stock_warehouse.csv` (1 rows, 3 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/stock_warehouse.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### stock_location.csv &rarr; `stock.location`

**Rows:** 19  
**Depends on:** stock_warehouse.csv (location hierarchy)  
**Extra SAP columns available:** 30

**Step 1 - create these custom fields on `stock.location` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `khlocation`/`LocationCollection` | `AltitudeMeasure` | `x_sap_altitude_measure` | Float |
| `khlocation`/`LocationCollection` | `AltitudeUnitCode` | `x_sap_altitude_unit_code` | Char |
| `khlocation`/`LocationCollection` | `CreationDateTime` | `x_sap_creation_date_time` | Datetime |
| `khlocation`/`LocationCollection` | `Description` | `x_sap_description` | Char |
| `khlocation`/`LocationCollection` | `ExternallyManagedIndicator` | `x_sap_externally_managed_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `ID` | `x_sap_i_d` | Char |
| `khlocation`/`LocationCollection` | `InventoryManagedLocationIndicator` | `x_sap_inventory_managed_location_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `LastChangeDateTime` | `x_sap_last_change_date_time` | Datetime |
| `khlocation`/`LocationCollection` | `LatitudeMeasure` | `x_sap_latitude_measure` | Float |
| `khlocation`/`LocationCollection` | `LatitudeUnitCode` | `x_sap_latitude_unit_code` | Char |
| `khlocation`/`LocationCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code` | Char |
| `khlocation`/`LocationCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text` | Char |
| `khlocation`/`LocationCollection` | `LongitudeMeasure` | `x_sap_longitude_measure` | Float |
| `khlocation`/`LocationCollection` | `LongitudeUnitCode` | `x_sap_longitude_unit_code` | Char |
| `khlocation`/`LocationCollection` | `Name` | `x_sap_name` | Char |
| `khlocation`/`LocationCollection` | `NegativeInTransitInventoryAllowedIndicator` | `x_sap_negative_in_transit_inventory_allowed_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `NegativeInventoryAllowedIndicator` | `x_sap_negative_inventory_allowed_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `khlocation`/`LocationCollection` | `ParentLocationID` | `x_sap_parent_location_i_d` | Char |
| `khlocation`/`LocationCollection` | `ParentLocationName` | `x_sap_parent_location_name` | Char |
| `khlocation`/`LocationCollection` | `ParentLocationUUID` | `x_sap_parent_location_u_u_i_d` | Char |
| `khlocation`/`LocationCollection` | `ServicePointIndicator` | `x_sap_service_point_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `ShipFromLocationIndicator` | `x_sap_ship_from_location_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `ShipToLocationIndicator` | `x_sap_ship_to_location_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `SiteIndicator` | `x_sap_site_indicator` | Boolean |
| `khlocation`/`LocationCollection` | `TimeZoneCode` | `x_sap_time_zone_code` | Char |
| `khlocation`/`LocationCollection` | `TimeZoneCodeText` | `x_sap_time_zone_code_text` | Char |
| `khlocation`/`LocationCollection` | `UUID` | `x_sap_u_u_i_d` | Char |
| `khlocation`/`LocationCollection` | `WorkingDayCalendarCode` | `x_sap_working_day_calendar_code` | Char |
| `khlocation`/`LocationCollection` | `WorkingDayCalendarCodeText` | `x_sap_working_day_calendar_code_text` | Char |

**Step 2 - import the base file:** `output_odoo/stock_location.csv` (19 rows, 4 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/stock_location.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### mrp_workcenter.csv &rarr; `mrp.workcenter`

**Rows:** 17  
**Depends on:** none  
**Extra SAP columns available:** 16

**Step 1 - create these custom fields on `mrp.workcenter` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `khproductionorder`/`OperationCollection` | `CategoryCode` | `x_sap_category_code` | Char |
| `khproductionorder`/`OperationCollection` | `CategoryCodeText` | `x_sap_category_code_text` | Char |
| `khproductionorder`/`OperationCollection` | `ID` | `x_sap_i_d` | Char |
| `khproductionorder`/`OperationCollection` | `MainResourceCategoryCode` | `x_sap_main_resource_category_code` | Char |
| `khproductionorder`/`OperationCollection` | `MainResourceCategoryCodeText` | `x_sap_main_resource_category_code_text` | Char |
| `khproductionorder`/`OperationCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `khproductionorder`/`OperationCollection` | `ParentObjectID` | `x_sap_parent_object_i_d` | Char |
| `khproductionorder`/`OperationCollection` | `ProcessingNetDuration` | `x_sap_processing_net_duration` | Float |
| `khproductionorder`/`OperationCollection` | `ResourceCategoryCode` | `x_sap_resource_category_code` | Char |
| `khproductionorder`/`OperationCollection` | `ResourceCategoryCodeText` | `x_sap_resource_category_code_text` | Char |
| `khproductionorder`/`OperationCollection` | `ResourceDescription` | `x_sap_resource_description` | Char |
| `khproductionorder`/`OperationCollection` | `ResourceID` | `x_sap_resource_i_d` | Char |
| `khproductionorder`/`OperationCollection` | `ResourceProductionSchedulingRelevanceIndicator` | `x_sap_resource_production_scheduling_relevance_indicator` | Boolean |
| `khproductionorder`/`OperationCollection` | `TypeCode` | `x_sap_type_code` | Char |
| `khproductionorder`/`OperationCollection` | `TypeCodeText` | `x_sap_type_code_text` | Char |
| `khproductionorder`/`OperationCollection` | `UUID` | `x_sap_u_u_i_d` | Char |

**Step 2 - import the base file:** `output_odoo/mrp_workcenter.csv` (17 rows, 3 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/mrp_workcenter.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### product_template.csv &rarr; `product.template`

**Rows:** 9287  
**Depends on:** product_category.csv (categ_id/id), uom_uom.csv (uom_id/id, uom_po_id/id)  
**Extra SAP columns available:** 202

**Step 1 - create these custom fields on `product.template` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `vmumaterial`/`AvailabilityConfirmationCollection` | `AvailabilityCheckScopeCode` | `x_sap_availability_check_scope_code` | Char |
| `vmumaterial`/`AvailabilityConfirmationCollection` | `AvailabilityCheckScopeCodeText` | `x_sap_availability_check_scope_code_text` | Char |
| `vmumaterial`/`AvailabilityConfirmationCollection` | `GoodsIssueProcessingDuration` | `x_sap_goods_issue_processing_duration` | Float |
| `vmumaterial`/`AvailabilityConfirmationCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code` | Char |
| `vmumaterial`/`AvailabilityConfirmationCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text` | Char |
| `vmumaterial`/`AvailabilityConfirmationCollection` | `ManualSourcingRequiredIndicator` | `x_sap_manual_sourcing_required_indicator` | Boolean |
| `vmumaterial`/`AvailabilityConfirmationCollection` | `ObjectID` | `x_sap_object_i_d_2` | Char |
| `vmumaterial`/`AvailabilityConfirmationCollection` | `ParentObjectID` | `x_sap_parent_object_i_d` | Char |
| `vmumaterial`/`AvailabilityConfirmationCollection` | `SupplyPlanningAreaDescription` | `x_sap_supply_planning_area_description` | Char |
| `vmumaterial`/`AvailabilityConfirmationCollection` | `SupplyPlanningAreaID` | `x_sap_supply_planning_area_i_d` | Char |
| `vmumaterial`/`IdentificationCollection` | `ObjectID` | `x_sap_object_i_d_3` | Char |
| `khserviceproduct`/`IdentificationCollection` | `ObjectID` | `x_sap_object_i_d_15` | Char |
| `vmumaterial`/`IdentificationCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_2` | Char |
| `khserviceproduct`/`IdentificationCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_13` | Char |
| `vmumaterial`/`IdentificationCollection` | `ProductID` | `x_sap_product_i_d` | Char |
| `khserviceproduct`/`IdentificationCollection` | `ProductID` | `x_sap_product_i_d_2` | Char |
| `vmumaterial`/`IdentificationCollection` | `ProductIdentifierTypeCode` | `x_sap_product_identifier_type_code` | Char |
| `khserviceproduct`/`IdentificationCollection` | `ProductIdentifierTypeCode` | `x_sap_product_identifier_type_code_2` | Char |
| `vmumaterial`/`IdentificationCollection` | `ProductIdentifierTypeCodeText` | `x_sap_product_identifier_type_code_text` | Char |
| `khserviceproduct`/`IdentificationCollection` | `ProductIdentifierTypeCodeText` | `x_sap_product_identifier_type_code_text_2` | Char |
| `vmumaterial`/`LogisticsCollection` | `CycleCountPlannedDuration` | `x_sap_cycle_count_planned_duration` | Float |
| `vmumaterial`/`LogisticsCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code_2` | Char |
| `vmumaterial`/`LogisticsCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text_2` | Char |
| `vmumaterial`/`LogisticsCollection` | `ObjectID` | `x_sap_object_i_d_4` | Char |
| `vmumaterial`/`LogisticsCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_3` | Char |
| `vmumaterial`/`LogisticsCollection` | `SiteID` | `x_sap_site_i_d` | Char |
| `vmumaterial`/`LogisticsCollection` | `SiteName` | `x_sap_site_name` | Char |
| `vmumaterial`/`LogisticsCollection` | `SiteUUID` | `x_sap_site_u_u_i_d` | Char |
| `vmumaterial`/`MaterialCollection` | `BaseMeasureUnitCode` | `x_sap_base_measure_unit_code` | Char |
| `vmumaterial`/`MaterialCollection` | `BaseMeasureUnitCodeText` | `x_sap_base_measure_unit_code_text` | Char |
| `vmumaterial`/`MaterialCollection` | `CreationDateTime` | `x_sap_creation_date_time` | Datetime |
| `vmumaterial`/`MaterialCollection` | `Description` | `x_sap_description` | Char |
| `vmumaterial`/`MaterialCollection` | `DescriptionLanguageCode` | `x_sap_description_language_code` | Char |
| `vmumaterial`/`MaterialCollection` | `DescriptionLanguageCodeText` | `x_sap_description_language_code_text` | Char |
| `vmumaterial`/`MaterialCollection` | `IdentifiedStockTypeCode` | `x_sap_identified_stock_type_code` | Char |
| `vmumaterial`/`MaterialCollection` | `IdentifiedStockTypeCodeText` | `x_sap_identified_stock_type_code_text` | Char |
| `vmumaterial`/`MaterialCollection` | `InternalID` | `x_sap_internal_i_d` | Char |
| `vmumaterial`/`MaterialCollection` | `LastChangeDateTime` | `x_sap_last_change_date_time` | Datetime |
| `vmumaterial`/`MaterialCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `vmumaterial`/`MaterialCollection` | `PlanningMeasureUnitCode` | `x_sap_planning_measure_unit_code` | Char |
| `vmumaterial`/`MaterialCollection` | `PlanningMeasureUnitCodeText` | `x_sap_planning_measure_unit_code_text` | Char |
| `vmumaterial`/`MaterialCollection` | `SerialNumberProfileCode` | `x_sap_serial_number_profile_code` | Char |
| `vmumaterial`/`MaterialCollection` | `SerialNumberProfileCodeText` | `x_sap_serial_number_profile_code_text` | Char |
| `vmumaterial`/`MaterialCollection` | `UUID` | `x_sap_u_u_i_d` | Char |
| `vmumaterial`/`MaterialCollection` | `ValuationLevelTypeCode` | `x_sap_valuation_level_type_code` | Char |
| `vmumaterial`/`MaterialCollection` | `ValuationLevelTypeCodeText` | `x_sap_valuation_level_type_code_text` | Char |
| `vmumaterial`/`PlanningCollection` | `DemandManagementProcedureCode` | `x_sap_demand_management_procedure_code` | Char |
| `vmumaterial`/`PlanningCollection` | `DemandManagementProcedureCodeText` | `x_sap_demand_management_procedure_code_text` | Char |
| `vmumaterial`/`PlanningCollection` | `GoodsReceiptProcessingDuration` | `x_sap_goods_receipt_processing_duration` | Float |
| `vmumaterial`/`PlanningCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code_3` | Char |
| `vmumaterial`/`PlanningCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text_3` | Char |
| `vmumaterial`/`PlanningCollection` | `LotSizeRoundingQuantity` | `x_sap_lot_size_rounding_quantity` | Float |
| `vmumaterial`/`PlanningCollection` | `LotSizeRoundingQuantityUnitCode` | `x_sap_lot_size_rounding_quantity_unit_code` | Char |
| `vmumaterial`/`PlanningCollection` | `LotSizeRoundingQuantityUnitCodeText` | `x_sap_lot_size_rounding_quantity_unit_code_text` | Char |
| `vmumaterial`/`PlanningCollection` | `LotSizingMethodCode` | `x_sap_lot_sizing_method_code` | Char |
| `vmumaterial`/`PlanningCollection` | `LotSizingMethodCodeText` | `x_sap_lot_sizing_method_code_text` | Char |
| `vmumaterial`/`PlanningCollection` | `MaximumLotSizeQuantity` | `x_sap_maximum_lot_size_quantity` | Float |
| `vmumaterial`/`PlanningCollection` | `MaximumLotSizeQuantityUnitCode` | `x_sap_maximum_lot_size_quantity_unit_code` | Char |
| `vmumaterial`/`PlanningCollection` | `MaximumLotSizeQuantityUnitCodeText` | `x_sap_maximum_lot_size_quantity_unit_code_text` | Char |
| `vmumaterial`/`PlanningCollection` | `MinimumDaysOfSupplyDuration` | `x_sap_minimum_days_of_supply_duration` | Float |
| `vmumaterial`/`PlanningCollection` | `MinimumLotSizeQuantity` | `x_sap_minimum_lot_size_quantity` | Float |
| `vmumaterial`/`PlanningCollection` | `MinimumLotSizeQuantityUnitCode` | `x_sap_minimum_lot_size_quantity_unit_code` | Char |
| `vmumaterial`/`PlanningCollection` | `MinimumLotSizeQuantityUnitCodeText` | `x_sap_minimum_lot_size_quantity_unit_code_text` | Char |
| `vmumaterial`/`PlanningCollection` | `MinimumReceiptDaysOfSupplyDuration` | `x_sap_minimum_receipt_days_of_supply_duration` | Float |
| `vmumaterial`/`PlanningCollection` | `MinimumShelfLifeDuration` | `x_sap_minimum_shelf_life_duration` | Float |
| `vmumaterial`/`PlanningCollection` | `ObjectID` | `x_sap_object_i_d_5` | Char |
| `vmumaterial`/`PlanningCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_4` | Char |
| `vmumaterial`/`PlanningCollection` | `PlanningGroupDescription` | `x_sap_planning_group_description` | Char |
| `vmumaterial`/`PlanningCollection` | `PlanningGroupID` | `x_sap_planning_group_i_d` | Char |
| `vmumaterial`/`PlanningCollection` | `PlanningProcedureCode` | `x_sap_planning_procedure_code` | Char |
| `vmumaterial`/`PlanningCollection` | `PlanningProcedureCodeText` | `x_sap_planning_procedure_code_text` | Char |
| `vmumaterial`/`PlanningCollection` | `PlanningTimeFenceDuration` | `x_sap_planning_time_fence_duration` | Float |
| `vmumaterial`/`PlanningCollection` | `ProcurementLeadDuration` | `x_sap_procurement_lead_duration` | Float |
| `vmumaterial`/`PlanningCollection` | `ProcurementTypeCode` | `x_sap_procurement_type_code` | Char |
| `vmumaterial`/`PlanningCollection` | `ProcurementTypeCodeText` | `x_sap_procurement_type_code_text` | Char |
| `vmumaterial`/`PlanningCollection` | `SafetyLeadDuration` | `x_sap_safety_lead_duration` | Float |
| `vmumaterial`/`PlanningCollection` | `SafetyStockQuantity` | `x_sap_safety_stock_quantity` | Float |
| `vmumaterial`/`PlanningCollection` | `SafetyStockQuantityUnitCode` | `x_sap_safety_stock_quantity_unit_code` | Char |
| `vmumaterial`/`PlanningCollection` | `SafetyStockQuantityUnitCodeText` | `x_sap_safety_stock_quantity_unit_code_text` | Char |
| `vmumaterial`/`PlanningCollection` | `SupplyPlanningAreaDescription` | `x_sap_supply_planning_area_description_2` | Char |
| `vmumaterial`/`PlanningCollection` | `SupplyPlanningAreaID` | `x_sap_supply_planning_area_i_d_2` | Char |
| `vmumaterial`/`PlanningCollection` | `SupplyPlanningAreaUUID` | `x_sap_supply_planning_area_u_u_i_d` | Char |
| `vmumaterial`/`PlanningForecastGroupCollection` | `ObjectID` | `x_sap_object_i_d_6` | Char |
| `vmumaterial`/`PlanningForecastGroupCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_5` | Char |
| `vmumaterial`/`PlanningForecastGroupCollection` | `ProductCategoryDescription` | `x_sap_product_category_description` | Char |
| `vmumaterial`/`PlanningForecastGroupCollection` | `ProductCategoryInternalID` | `x_sap_product_category_internal_i_d` | Char |
| `vmumaterial`/`ProductCategoryCollection` | `Description` | `x_sap_description_2` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `Description` | `x_sap_description_4` | Char |
| `vmumaterial`/`ProductCategoryCollection` | `DescriptionLanguageCode` | `x_sap_description_language_code_2` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `DescriptionLanguageCode` | `x_sap_description_language_code_4` | Char |
| `vmumaterial`/`ProductCategoryCollection` | `DescriptionLanguageCodeText` | `x_sap_description_language_code_text_2` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `DescriptionLanguageCodeText` | `x_sap_description_language_code_text_4` | Char |
| `vmumaterial`/`ProductCategoryCollection` | `ObjectID` | `x_sap_object_i_d_7` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `ObjectID` | `x_sap_object_i_d_16` | Char |
| `vmumaterial`/`ProductCategoryCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_6` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_14` | Char |
| `vmumaterial`/`ProductCategoryCollection` | `ProductCategoryInternalID` | `x_sap_product_category_internal_i_d_2` | Char |
| `khserviceproduct`/`ProductCategoryCollection` | `ProductCategoryInternalID` | `x_sap_product_category_internal_i_d_3` | Char |
| `vmumaterial`/`PurchasingCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code_4` | Char |
| `khserviceproduct`/`PurchasingCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code_7` | Char |
| `vmumaterial`/`PurchasingCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text_4` | Char |
| `khserviceproduct`/`PurchasingCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text_7` | Char |
| `vmumaterial`/`PurchasingCollection` | `ObjectID` | `x_sap_object_i_d_8` | Char |
| `khserviceproduct`/`PurchasingCollection` | `ObjectID` | `x_sap_object_i_d_17` | Char |
| `vmumaterial`/`PurchasingCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_7` | Char |
| `khserviceproduct`/`PurchasingCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_15` | Char |
| `vmumaterial`/`PurchasingCollection` | `PurchasingMeasureUnitCode` | `x_sap_purchasing_measure_unit_code` | Char |
| `khserviceproduct`/`PurchasingCollection` | `PurchasingMeasureUnitCode` | `x_sap_purchasing_measure_unit_code_2` | Char |
| `vmumaterial`/`PurchasingCollection` | `PurchasingMeasureUnitCodeText` | `x_sap_purchasing_measure_unit_code_text` | Char |
| `khserviceproduct`/`PurchasingCollection` | `PurchasingMeasureUnitCodeText` | `x_sap_purchasing_measure_unit_code_text_2` | Char |
| `vmumaterial`/`QuantityConversionCollection` | `BatchDependentIndicator` | `x_sap_batch_dependent_indicator` | Boolean |
| `vmumaterial`/`QuantityConversionCollection` | `CorrespondingQuantity` | `x_sap_corresponding_quantity` | Float |
| `khserviceproduct`/`QuantityConversionCollection` | `CorrespondingQuantity` | `x_sap_corresponding_quantity_2` | Float |
| `vmumaterial`/`QuantityConversionCollection` | `CorrespondingQuantityUnitCode` | `x_sap_corresponding_quantity_unit_code` | Char |
| `khserviceproduct`/`QuantityConversionCollection` | `CorrespondingQuantityUnitCode` | `x_sap_corresponding_quantity_unit_code_2` | Char |
| `vmumaterial`/`QuantityConversionCollection` | `CorrespondingQuantityUnitCodeText` | `x_sap_corresponding_quantity_unit_code_text` | Char |
| `khserviceproduct`/`QuantityConversionCollection` | `CorrespondingQuantityUnitCodeText` | `x_sap_corresponding_quantity_unit_code_text_2` | Char |
| `vmumaterial`/`QuantityConversionCollection` | `ObjectID` | `x_sap_object_i_d_9` | Char |
| `khserviceproduct`/`QuantityConversionCollection` | `ObjectID` | `x_sap_object_i_d_18` | Char |
| `vmumaterial`/`QuantityConversionCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_8` | Char |
| `khserviceproduct`/`QuantityConversionCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_16` | Char |
| `vmumaterial`/`QuantityConversionCollection` | `Quantity` | `x_sap_quantity` | Float |
| `khserviceproduct`/`QuantityConversionCollection` | `Quantity` | `x_sap_quantity_2` | Float |
| `vmumaterial`/`QuantityConversionCollection` | `QuantityUnitCode` | `x_sap_quantity_unit_code` | Char |
| `khserviceproduct`/`QuantityConversionCollection` | `QuantityUnitCode` | `x_sap_quantity_unit_code_2` | Char |
| `vmumaterial`/`QuantityConversionCollection` | `QuantityUnitCodeText` | `x_sap_quantity_unit_code_text` | Char |
| `khserviceproduct`/`QuantityConversionCollection` | `QuantityUnitCodeText` | `x_sap_quantity_unit_code_text_2` | Char |
| `vmumaterial`/`SalesCollection` | `CashDiscountDeductibleIndicator` | `x_sap_cash_discount_deductible_indicator` | Boolean |
| `khserviceproduct`/`SalesCollection` | `CashDiscountDeductibleIndicator` | `x_sap_cash_discount_deductible_indicator_2` | Boolean |
| `vmumaterial`/`SalesCollection` | `DistributionChannelCode` | `x_sap_distribution_channel_code` | Char |
| `khserviceproduct`/`SalesCollection` | `DistributionChannelCode` | `x_sap_distribution_channel_code_2` | Char |
| `vmumaterial`/`SalesCollection` | `DistributionChannelCodeText` | `x_sap_distribution_channel_code_text` | Char |
| `khserviceproduct`/`SalesCollection` | `DistributionChannelCodeText` | `x_sap_distribution_channel_code_text_2` | Char |
| `vmumaterial`/`SalesCollection` | `ItemGroupCode` | `x_sap_item_group_code` | Char |
| `khserviceproduct`/`SalesCollection` | `ItemGroupCode` | `x_sap_item_group_code_2` | Char |
| `vmumaterial`/`SalesCollection` | `ItemGroupCodeText` | `x_sap_item_group_code_text` | Char |
| `khserviceproduct`/`SalesCollection` | `ItemGroupCodeText` | `x_sap_item_group_code_text_2` | Char |
| `vmumaterial`/`SalesCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code_5` | Char |
| `khserviceproduct`/`SalesCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code_8` | Char |
| `vmumaterial`/`SalesCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text_5` | Char |
| `khserviceproduct`/`SalesCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text_8` | Char |
| `vmumaterial`/`SalesCollection` | `MinimumOrderQuantity` | `x_sap_minimum_order_quantity` | Float |
| `khserviceproduct`/`SalesCollection` | `MinimumOrderQuantity` | `x_sap_minimum_order_quantity_2` | Float |
| `vmumaterial`/`SalesCollection` | `MinimumOrderQuantityUnitCode` | `x_sap_minimum_order_quantity_unit_code` | Char |
| `khserviceproduct`/`SalesCollection` | `MinimumOrderQuantityUnitCode` | `x_sap_minimum_order_quantity_unit_code_2` | Char |
| `vmumaterial`/`SalesCollection` | `MinimumOrderQuantityUnitCodeText` | `x_sap_minimum_order_quantity_unit_code_text` | Char |
| `khserviceproduct`/`SalesCollection` | `MinimumOrderQuantityUnitCodeText` | `x_sap_minimum_order_quantity_unit_code_text_2` | Char |
| `vmumaterial`/`SalesCollection` | `ObjectID` | `x_sap_object_i_d_10` | Char |
| `khserviceproduct`/`SalesCollection` | `ObjectID` | `x_sap_object_i_d_19` | Char |
| `vmumaterial`/`SalesCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_9` | Char |
| `khserviceproduct`/`SalesCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_17` | Char |
| `vmumaterial`/`SalesCollection` | `ReferencePriceMaterialDescription` | `x_sap_reference_price_material_description` | Char |
| `vmumaterial`/`SalesCollection` | `ReferencePriceMaterialID` | `x_sap_reference_price_material_i_d` | Char |
| `khserviceproduct`/`SalesCollection` | `ReferencePriceServiceProductDescription` | `x_sap_reference_price_service_product_description` | Char |
| `khserviceproduct`/`SalesCollection` | `ReferencePriceServiceProductID` | `x_sap_reference_price_service_product_i_d` | Char |
| `vmumaterial`/`SalesCollection` | `SalesMeasureUnitCode` | `x_sap_sales_measure_unit_code` | Char |
| `khserviceproduct`/`SalesCollection` | `SalesMeasureUnitCode` | `x_sap_sales_measure_unit_code_2` | Char |
| `vmumaterial`/`SalesCollection` | `SalesMeasureUnitCodeText` | `x_sap_sales_measure_unit_code_text` | Char |
| `khserviceproduct`/`SalesCollection` | `SalesMeasureUnitCodeText` | `x_sap_sales_measure_unit_code_text_2` | Char |
| `vmumaterial`/`SalesCollection` | `SalesOrganisationID` | `x_sap_sales_organisation_i_d` | Char |
| `khserviceproduct`/`SalesCollection` | `SalesOrganisationID` | `x_sap_sales_organisation_i_d_2` | Char |
| `khserviceproduct`/`ServiceProductCollection` | `BaseMeasureUnitCode` | `x_sap_base_measure_unit_code_2` | Char |
| `khserviceproduct`/`ServiceProductCollection` | `BaseMeasureUnitCodeText` | `x_sap_base_measure_unit_code_text_2` | Char |
| `khserviceproduct`/`ServiceProductCollection` | `CreationDateTime` | `x_sap_creation_date_time_2` | Datetime |
| `khserviceproduct`/`ServiceProductCollection` | `Description` | `x_sap_description_3` | Char |
| `khserviceproduct`/`ServiceProductCollection` | `DescriptionLanguageCode` | `x_sap_description_language_code_3` | Char |
| `khserviceproduct`/`ServiceProductCollection` | `DescriptionLanguageCodeText` | `x_sap_description_language_code_text_3` | Char |
| `khserviceproduct`/`ServiceProductCollection` | `ExpenseIndicator` | `x_sap_expense_indicator` | Boolean |
| `khserviceproduct`/`ServiceProductCollection` | `InternalID` | `x_sap_internal_i_d_2` | Char |
| `khserviceproduct`/`ServiceProductCollection` | `LastChangeDateTime` | `x_sap_last_change_date_time_2` | Datetime |
| `khserviceproduct`/`ServiceProductCollection` | `ObjectID` | `x_sap_object_i_d_14` | Char |
| `khserviceproduct`/`ServiceProductCollection` | `UUID` | `x_sap_u_u_i_d_2` | Char |
| `vmumaterial`/`SupplierInformationCollection` | `BusinessPartnerFormattedName` | `x_sap_business_partner_formatted_name` | Char |
| `vmumaterial`/`SupplierInformationCollection` | `ObjectID` | `x_sap_object_i_d_11` | Char |
| `vmumaterial`/`SupplierInformationCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_10` | Char |
| `vmumaterial`/`SupplierInformationCollection` | `SupplierID` | `x_sap_supplier_i_d` | Char |
| `vmumaterial`/`SupplierInformationCollection` | `SupplierLeadTimeDuration` | `x_sap_supplier_lead_time_duration` | Float |
| `vmumaterial`/`SupplierInformationCollection` | `SupplierPartNumber` | `x_sap_supplier_part_number` | Char |
| `vmumaterial`/`TextCollection` | `AuthorName` | `x_sap_author_name` | Char |
| `vmumaterial`/`TextCollection` | `AuthorUUID` | `x_sap_author_u_u_i_d` | Char |
| `vmumaterial`/`TextCollection` | `CreatedBy` | `x_sap_created_by` | Char |
| `vmumaterial`/`TextCollection` | `CreatedOn` | `x_sap_created_on` | Char |
| `vmumaterial`/`TextCollection` | `LanguageCode` | `x_sap_language_code` | Char |
| `vmumaterial`/`TextCollection` | `LanguageCodeText` | `x_sap_language_code_text` | Char |
| `vmumaterial`/`TextCollection` | `LastUpdatedBy` | `x_sap_last_updated_by` | Char |
| `vmumaterial`/`TextCollection` | `ObjectID` | `x_sap_object_i_d_12` | Char |
| `vmumaterial`/`TextCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_11` | Char |
| `vmumaterial`/`TextCollection` | `Text` | `x_sap_text` | Char |
| `vmumaterial`/`TextCollection` | `TypeCode` | `x_sap_type_code` | Char |
| `vmumaterial`/`TextCollection` | `TypeCodeText` | `x_sap_type_code_text` | Char |
| `vmumaterial`/`TextCollection` | `UpdatedOn` | `x_sap_updated_on` | Char |
| `vmumaterial`/`ValuationCollection` | `BusinessResidenceID` | `x_sap_business_residence_i_d` | Char |
| `vmumaterial`/`ValuationCollection` | `CompanyID` | `x_sap_company_i_d` | Char |
| `khserviceproduct`/`ValuationCollection` | `CompanyID` | `x_sap_company_i_d_2` | Char |
| `vmumaterial`/`ValuationCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code_6` | Char |
| `khserviceproduct`/`ValuationCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code_9` | Char |
| `vmumaterial`/`ValuationCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text_6` | Char |
| `khserviceproduct`/`ValuationCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text_9` | Char |
| `vmumaterial`/`ValuationCollection` | `ObjectID` | `x_sap_object_i_d_13` | Char |
| `khserviceproduct`/`ValuationCollection` | `ObjectID` | `x_sap_object_i_d_20` | Char |
| `vmumaterial`/`ValuationCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_12` | Char |
| `khserviceproduct`/`ValuationCollection` | `ParentObjectID` | `x_sap_parent_object_i_d_18` | Char |

**Step 2 - import the base file:** `output_odoo/product_template.csv` (9287 rows, 14 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/product_template.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


### product_supplierinfo.csv &rarr; `product.supplierinfo`

**Rows:** 8556  
**Depends on:** res_partner.csv (partner_id/id), product_template.csv (product_tmpl_id/id)  
**Extra SAP columns available:** 0

**Step 2 - import the base file:** `output_odoo/product_supplierinfo.csv` (8556 rows, 6 standard columns) - creates the records.


### product_pricelist.csv &rarr; `product.pricelist`

**Rows:** 226  
**Depends on:** none (currency_id/id uses Odoo's built-in base.<code> data)  
**Extra SAP columns available:** 23

**Step 1 - create these custom fields on `product.pricelist` before importing the full-column file** (Settings &rarr; Technical &rarr; Fields &rarr; New, or Studio):

| Source entity | SAP field | Suggested technical name | Type |
|---|---|---|---|
| `khsalesarrangement`/`SalesArrangementCollection` | `CompleteDeliveryRequestedIndicator` | `x_sap_complete_delivery_requested_indicator` | Boolean |
| `khsalesarrangement`/`SalesArrangementCollection` | `CreationDateTime` | `x_sap_creation_date_time` | Datetime |
| `khsalesarrangement`/`SalesArrangementCollection` | `CurrencyCode` | `x_sap_currency_code` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `CurrencyCodeText` | `x_sap_currency_code_text` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `CustomerGroupCode` | `x_sap_customer_group_code` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `CustomerGroupCodeText` | `x_sap_customer_group_code_text` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `CustomerUUID` | `x_sap_customer_u_u_i_d` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `DeliveryPriorityCode` | `x_sap_delivery_priority_code` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `DeliveryPriorityCodeText` | `x_sap_delivery_priority_code_text` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `DistributionChannelCode` | `x_sap_distribution_channel_code` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `DistributionChannelCodeText` | `x_sap_distribution_channel_code_text` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `IncotermsCode` | `x_sap_incoterms_code` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `IncotermsCodeText` | `x_sap_incoterms_code_text` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `IncotermsLocationName` | `x_sap_incoterms_location_name` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `LastChangeDateTime` | `x_sap_last_change_date_time` | Datetime |
| `khsalesarrangement`/`SalesArrangementCollection` | `LifeCycleStatusCode` | `x_sap_life_cycle_status_code` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `LifeCycleStatusCodeText` | `x_sap_life_cycle_status_code_text` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `ObjectID` | `x_sap_object_i_d` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `PaymentTermsCode` | `x_sap_payment_terms_code` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `PaymentTermsCodeText` | `x_sap_payment_terms_code_text` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `SalesOrganisationID` | `x_sap_sales_organisation_i_d` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `SalesOrganisationUUID` | `x_sap_sales_organisation_u_u_i_d` | Char |
| `khsalesarrangement`/`SalesArrangementCollection` | `UUID` | `x_sap_u_u_i_d` | Char |

**Step 2 - import the base file:** `output_odoo/product_pricelist.csv` (226 rows, 3 standard columns) - creates the records.

**Step 3 - import the full-column file to populate the custom fields:** `output_full_csv/odoo_models/product_pricelist.csv` - map `id` to *External ID* and each `sap__...` column to its matching custom field from the table above. Odoo updates the records Step 2 created rather than duplicating them.


## Regenerating this file

Everything above the "Per-object detail" tables is hand-written; the tables themselves are
generated from real data, not hand-typed, so they can never silently drift from what's actually
in `output_odoo/`/`output_full_csv/`. To regenerate after new data comes in:

1. Re-run `python -m src.main` (or at least Stage 2 + 2b) so `output_odoo/` and
   `output_full_csv/odoo_models/` are current.
2. The generator script that built the tables above reads each master file's standard header
   from `output_odoo/<file>.csv`, the full header from `output_full_csv/odoo_models/<file>.csv`,
   and for every extra `sap__<service>__<entity>__<field>` column: groups it by source entity,
   infers a field type from the name (`...Indicator` &rarr; Boolean, `...Date`/`...DateTime`
   &rarr; Date/Datetime, `...Quantity`/`...Amount`/`...Duration`/`...Rate`/`...Value`/`...Percent`/
   `...Measure` &rarr; Float, everything else &rarr; Char), and suggests a technical name
   (`x_sap_<snake_case field name>`, de-duplicated with a numeric suffix on collision).
